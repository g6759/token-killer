"""Exercise the real SDK over both transports, using isolated game HTTP fixtures."""

import asyncio
from contextlib import ExitStack, contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import signal
import socket
import subprocess
import sys
import tempfile
import threading
import time
import unittest
from unittest.mock import patch

from server import HERE, Offline, game_request, origin_for, verified_endpoint
import httpx
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import streamable_http_client


JOB_ID = "a" * 32
SERVER = str(HERE / "server.py")
READ_TOOLS = {"game_status", "get_turn", "evaluate_candidate", "packing_batch_status"}
WRITE_TOOLS = {"start_game", "claim_turn", "report_progress", "report_packing_trial", "submit_turn", "stop_game",
               "start_packing_batch", "plan_packing_batch", "stop_packing_batch"}


@contextmanager
def game_fixture(which):
    calls = []

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.answer(None)

        def do_POST(self):
            self.answer(json.loads(self.rfile.read(int(self.headers["Content-Length"]))))

        def answer(self, body):
            if self.path == "/api/auto/health":
                result = {"service": "research_games", "game": which, "pid": os.getpid()}
            else:
                calls.append((self.path, body, self.headers["Origin"]))
                result = {"game": which, "id": JOB_ID, "turn": 1, "prompt": "test prompt"}
            if self.path == "/api/auto/submit":
                if body["turn"] == 1:
                    result.update(turn=2, prompt="next prompt")
                else:
                    result.update(turn=None, prompt=None)
            raw = json.dumps(result).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(raw)))
            self.end_headers()
            self.wfile.write(raw)

        def log_message(self, *args):
            pass

    with ThreadingHTTPServer(("127.0.0.1", 0), Handler) as httpd:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            yield f"http://127.0.0.1:{httpd.server_port}", calls
        finally:
            httpd.shutdown()
            thread.join(timeout=5)


class ProtocolTests(unittest.TestCase):
    def setUp(self):
        resources = self.enterContext(ExitStack())
        self.registry = Path(resources.enter_context(tempfile.TemporaryDirectory()))
        self.endpoints = {}
        for family in ("relay", "packing"):
            origin, calls = resources.enter_context(game_fixture(family))
            self.endpoints[family] = origin, calls
            (self.registry / f"{family}.json").write_text(
                json.dumps({"origin": origin, "pid": os.getpid()}), encoding="utf-8")

    def test_registry_only_allows_literal_loopback(self):
        with patch.dict(os.environ, GAME_MCP_REGISTRY=str(self.registry)):
            for origin in ("https://127.0.0.1:8777", "http://localhost:8777",
                           "http://127.0.0.1:90000", "http://127.0.0.1:8777/path"):
                (self.registry / "relay.json").write_text(json.dumps({"origin": origin, "pid": 1}))
                with self.assertRaisesRegex(ValueError, "登记地址"):
                    origin_for("relay-7")

    def test_stale_registration_never_receives_a_game_write(self):
        wrong, calls = self.endpoints["packing"]
        (self.registry / "relay.json").write_text(json.dumps({"origin": wrong, "pid": os.getpid()}))
        with patch.dict(os.environ, GAME_MCP_REGISTRY=str(self.registry), GAME_MCP_DATA_DIR=""):
            self.assertEqual(game_request("relay-7", "/api/auto?pack=relay-7")["status"], "offline")
            with self.assertRaises(Offline):
                game_request("relay-7", "/api/auto/claim", {"id": JOB_ID})
            with self.assertRaisesRegex(ValueError, "隔离测试"):
                game_request("relay-7", "/api/auto/start", {"rounds": 1, "request_id": "x", "mode": "mcp"})
            self.assertEqual(calls, [])

    async def call(self, session, name, **arguments):
        response = await session.call_tool(name, arguments)
        self.assertFalse(response.isError, response.content)
        return response.structuredContent

    async def exercise(self, read, write, read_only=False):
        async with ClientSession(read, write) as session:
            await session.initialize()
            tools = {t.name: t for t in (await session.list_tools()).tools}
            self.assertEqual(set(tools), READ_TOOLS if read_only else READ_TOOLS | WRITE_TOOLS)
            self.assertEqual({k for k, t in tools.items() if t.annotations.readOnlyHint}, READ_TOOLS)
            for pack, family, field in (("relay-7", "relay", "blocks"), ("square-18", "packing", "poses")):
                origin, calls = self.endpoints[family]
                args = {"pack_id": pack}
                status = await self.call(session, "game_status", **args)
                self.assertEqual(status["game"], family)
                args["job_id"] = status["id"]
                await self.call(session, "get_turn", **args)
                solution = {field: [[1, 2, 3]]}
                await self.call(session, "evaluate_candidate", pack_id=pack, solution=solution)
                self.assertEqual(calls[-1], ("/api/auto/evaluate", {"pack_id": pack, "solution": solution}, origin))
                if read_only:
                    disabled = await session.call_tool("start_game", {"pack_id": pack, "rounds": 1, "request_id": "A"})
                    self.assertTrue(disabled.isError)
                    self.assertEqual(len(calls), 3)
                    continue
                await self.call(session, "start_game", pack_id=pack, rounds=3, request_id="relay-or-pack")
                self.assertEqual(calls[-1][1], {"pack_id": pack, "rounds": 3, "request_id": "relay-or-pack", "mode": "mcp"})
                await self.call(session, "claim_turn", **args)
                self.assertEqual(calls[-1], ("/api/auto/claim", {"pack_id": pack, "id": JOB_ID}, origin))
                await self.call(session, "report_progress", **args, turn=1, checked=9, stage="精确复算")
                self.assertEqual(calls[-1][0], "/api/auto/progress")
                candidate = {"pack_id": pack, "parent_hash": "fixture", "solution": solution, "handoff": "说明"}
                first = await self.call(session, "submit_turn", **args, turn=1, candidate=candidate)
                self.assertEqual(first["turn"], 2)
                second = await self.call(session, "submit_turn", **args, turn=first["turn"], candidate=candidate)
                self.assertIsNone(second["turn"])
                self.assertEqual(calls[-1][1], {"pack_id": pack, "id": JOB_ID, "turn": 2, "candidate": candidate})
                await self.call(session, "stop_game", **args)
                self.assertEqual(calls[-1], ("/api/auto/stop", {"pack_id": pack, "id": JOB_ID}, origin))
                self.assertTrue(all(source == origin for _, _, source in calls))
            invalid = await session.call_tool("game_status", {"pack_id": "https://example.invalid"})
            self.assertTrue(invalid.isError)
            origin, calls = self.endpoints["packing"]
            await self.call(session, "packing_batch_status", batch_id=JOB_ID)
            self.assertEqual(calls[-1], (f"/api/packing/batch?batch_id={JOB_ID}", None, origin))
            if not read_only:
                new_batch = {"packs": ["square-26", "square-37", "square-50"],
                             "rounds": 3, "parallel": 3, "request_id": "batch-1"}
                await self.call(session, "start_packing_batch", **new_batch)
                self.assertEqual(calls[-1], ("/api/packing/batch/start", new_batch, origin))
                plan = {"batch_id": JOB_ID, "thread_id": "00000000-0000-4000-8000-000000000001",
                        "quota": {"ordinaryUsageAllowed": True},
                        "plans": [{"pack_id": "square-50", "turn": 1, "strategy": "mixed"}]}
                await self.call(session, "plan_packing_batch", **plan)
                self.assertEqual(calls[-1], ("/api/packing/batch/plan", plan, origin))
                await self.call(session, "stop_packing_batch", batch_id=JOB_ID, pack_id="square-26")
                self.assertEqual(calls[-1], ("/api/packing/batch/stop", {"batch_id": JOB_ID, "pack_id": "square-26"}, origin))

    def test_stdio_full_protocol(self):
        async def run():
            params = StdioServerParameters(command=sys.executable, args=["-B", "-X", "utf8", SERVER],
                                           env={"GAME_MCP_REGISTRY": str(self.registry)})
            async with stdio_client(params) as (read, write):
                await self.exercise(read, write)
        asyncio.run(asyncio.wait_for(run(), timeout=45))

    def test_http_read_only_and_rebinding_checks(self):
        with socket.socket() as unused:
            unused.bind(("127.0.0.1", 0))
            port = unused.getsockname()[1]
        args = [sys.executable, "-B", "-X", "utf8", SERVER, "--transport", "streamable-http",
                "--port", str(port), "--read-only"]
        env = dict(os.environ, GAME_MCP_REGISTRY=str(self.registry))
        process = subprocess.Popen(args, env=env, stderr=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        try:
            async def run():
                url = f"http://127.0.0.1:{port}/mcp"
                async with httpx.AsyncClient(trust_env=False) as http:
                    deadline = time.monotonic() + 12
                    while True:
                        try:
                            foreign = await http.post(url, headers={"Host": "unexpected.invalid"}, json={})
                            break
                        except httpx.ConnectError:
                            if process.poll() is not None or time.monotonic() >= deadline:
                                raise
                            await asyncio.sleep(0.05)
                    self.assertEqual(foreign.status_code, 421)
                    bad_origin = await http.post(url, headers={"Origin": "https://unexpected.invalid"}, json={})
                    self.assertEqual(bad_origin.status_code, 403)
                    async with streamable_http_client(url, http_client=http) as (read, write, _):
                        await self.exercise(read, write, read_only=True)
            asyncio.run(asyncio.wait_for(run(), timeout=45))
        finally:
            process.terminate()
            process.wait(timeout=5)


class GameIntegrationTests(unittest.TestCase):
    def test_two_clients_start_a_single_game_and_reuse_it(self):
        with tempfile.TemporaryDirectory() as tmp:
            registry = Path(tmp) / "registry"
            data = Path(tmp) / "data"
            env = {"GAME_MCP_REGISTRY": str(registry), "GAME_MCP_DATA_DIR": str(data)}
            params = StdioServerParameters(command=sys.executable, args=["-B", "-X", "utf8", SERVER], env=env)
            pack = "relay-7"
            async def run():
                async with stdio_client(params) as (r1, w1), stdio_client(params) as (r2, w2):
                    async with ClientSession(r1, w1) as first, ClientSession(r2, w2) as second:
                        await asyncio.gather(first.initialize(), second.initialize())
                        offline = await first.call_tool("game_status", {"pack_id": pack})
                        self.assertEqual(offline.structuredContent["status"], "offline")
                        self.assertFalse(registry.exists())
                        self.assertFalse(data.exists())
                        params_start = {"pack_id": pack, "request_id": "cold-start-test", "rounds": 1}
                        jobs = await asyncio.gather(first.call_tool("start_game", params_start),
                                                    second.call_tool("start_game", params_start))
                        for result in jobs:
                            self.assertFalse(result.isError, result.content)
                        ids = {result.structuredContent["id"] for result in jobs}
                        origins = {result.structuredContent["origin"] for result in jobs}
                        self.assertEqual(len(ids), 1)
                        self.assertEqual(len(origins), 1)
                        self.assertTrue((data / "relay.sqlite3").is_file())
                        log = (registry / "relay.log").read_text(encoding="utf-8")
                        self.assertEqual([line for line in log.splitlines() if line.startswith("http://")],
                                         list(origins))
                        stat = await first.call_tool("game_status", {"pack_id": pack})
                        self.assertEqual(stat.structuredContent["origin"], origins.pop())
            try:
                asyncio.run(asyncio.wait_for(run(), timeout=60))
            finally:
                with patch.dict(os.environ, env):
                    try:
                        endpoint = verified_endpoint(pack)
                    except Offline:
                        # The SDK may have already stopped the child when closing stdio.
                        pass
                    else:
                        os.kill(endpoint.pid, signal.SIGTERM)

    def test_official_stdio_reaches_both_real_judges(self):
        root = HERE.parent
        # The seven translates of a cyclic difference set cover every pair exactly once.
        relay_solution = {"blocks": [sorted(1 + (start + delta) % 7 for delta in (0, 1, 3))
                                     for start in range(7)]}
        packing_example = json.loads((root / "方格子容纳/reference_layouts.json").read_text(encoding="utf-8"))
        games = [
            ("relay", "relay-7", root / "四境驿站/solo_server.py", relay_solution),
            ("packing", "square-18", root / "方格子容纳/game.py", packing_example["square-18"]["finer"]),
        ]
        with tempfile.TemporaryDirectory() as registry, ExitStack() as children:
            env = dict(os.environ, GAME_MCP_REGISTRY=registry)
            for family, _, script, _ in games:
                db = Path(registry) / f"{family}.sqlite3"
                process = subprocess.Popen(
                    [sys.executable, "-B", "-X", "utf8", str(script), "--port", "0", "--db", str(db)],
                    stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                    env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
                children.callback(process.wait, timeout=5)
                children.callback(process.terminate)
                deadline = time.monotonic() + 15
                while not (Path(registry) / f"{family}.json").exists():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        self.fail(f"{family} did not register its game server")
                    time.sleep(0.05)

            async def run():
                params = StdioServerParameters(command=sys.executable, args=["-B", "-X", "utf8", SERVER],
                                               env={"GAME_MCP_REGISTRY": registry})
                async with stdio_client(params) as (read, write), ClientSession(read, write) as client:
                    await client.initialize()
                    async def call(name, **args):
                        response = await client.call_tool(name, args)
                        self.assertFalse(response.isError, response.content)
                        return response.structuredContent

                    for family, pack, _, solution in games:
                        before = await call("game_status", pack_id=pack)
                        verified = await call("evaluate_candidate", pack_id=pack, solution=solution)
                        self.assertEqual(len(verified["solution_hash"]), 64)
                        self.assertEqual(before, await call("game_status", pack_id=pack))
                        if family == "relay":
                            self.assertEqual((verified["score"], verified["total"]), (21, 21))
                        else:
                            self.assertEqual(verified["side_ticks"], 4822914)
                        new = await call("start_game", pack_id=pack, rounds=1, request_id=family)
                        self.assertEqual(new["id"], (await call("game_status", pack_id=pack))["id"])
                        args = dict(pack_id=pack, job_id=new["id"])
                        pending = await call("get_turn", **args)
                        claimed = await call("claim_turn", **args)
                        self.assertEqual(pending["prompt"], claimed["prompt"])
                        self.assertNotEqual(pending["job"]["stage"], claimed["job"]["stage"])
                        progress = await call("report_progress", **args, turn=1, checked=1, stage="已复算公开构造")
                        self.assertEqual(progress["work"], (await call("game_status", pack_id=pack))["work"])
                        parent = re.search(r'"parent_hash"\s*:\s*"([a-f0-9]{64})"', claimed["prompt"])
                        self.assertIsNotNone(parent)
                        candidate = {"pack_id": pack, "solution": solution, "parent_hash": parent[1],
                                     "handoff": "协议验收，复用已归档的公开构造。"}
                        result = await call("submit_turn", **args, turn=1, candidate=candidate)
                        self.assertTrue(result["previous_receipt"]["accepted"], result)
                        self.assertEqual(result["previous_receipt"]["work"]["checked"], 1)
                        self.assertEqual(result["job"]["status"], "completed")
                        repeated = await call("submit_turn", **args, turn=1, candidate=candidate)
                        self.assertEqual(len(repeated["job"]["receipts"]), 1)
            asyncio.run(asyncio.wait_for(run(), timeout=60))


if __name__ == "__main__":
    unittest.main()
