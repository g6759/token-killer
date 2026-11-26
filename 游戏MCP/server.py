"""Serve the local relay and square-packing games using the official MCP SDK."""

import argparse
import asyncio
from contextlib import contextmanager
import errno
import http.client
import json
import os
from pathlib import Path
import re
import secrets
import shutil
import site
import subprocess
import sys
import tempfile
import time
from typing import Annotated, Any, Literal, NamedTuple


HERE = Path(__file__).resolve().parent
# The bundled SDK includes the .pth bootstrap for its Windows dependencies.
bundled = HERE / ".deps"
if bundled.is_dir() and sys.platform == "win32" and sys.version_info[:2] == (3, 13):
    sys.path.insert(0, str(bundled))
    site.addsitedir(str(bundled))

from mcp.server.fastmcp import FastMCP
from mcp.server.transport_security import TransportSecuritySettings
from mcp.types import ToolAnnotations
from pydantic import BaseModel, Field


sys.path.insert(0, str(HERE.parent))
try:
    from 方格子容纳.packing_catalog import PACKS
finally:
    sys.path.pop(0)

Pack = Literal[("relay-7", "relay-49", "relay-49-frontier", *PACKS)]
PackingPack = Literal[PACKS]
PositiveInt = Annotated[int, Field(gt=0, strict=True)]
JobId = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$", min_length=32, max_length=32)]
RequestId = Annotated[str, Field(pattern=r"^[A-Za-z0-9_-]+$", min_length=1, max_length=96)]
MAX_BYTES = 4 * 1024 * 1024
DEFAULT_PORT = 8877


class PackingPlan(BaseModel):
    pack_id: PackingPack
    turn: PositiveInt
    strategy: Literal["compact", "boundary", "mixed"]


class Offline(ValueError):
    """There is no verified process for this game's registered address."""


class Endpoint(NamedTuple):
    origin: str
    port: int
    pid: int
    instance: str | None


def registry_dir() -> Path:
    return Path(os.environ.get("GAME_MCP_REGISTRY", HERE / "运行中")).resolve()


def family_of(pack_id: Pack) -> str:
    return "packing" if pack_id.startswith("square-") else "relay"


def origin_for(pack_id: Pack) -> Endpoint:
    registry = registry_dir()
    family = family_of(pack_id)
    try:
        entry = json.loads((registry / (family + ".json")).read_text(encoding="utf-8-sig"))
        origin = entry["origin"]
        match = re.fullmatch(r"http://127\.0\.0\.1:([1-9][0-9]{0,4})", origin)
        if not match or int(match[1]) > 65535 or type(entry["pid"]) is not int or entry["pid"] <= 0:
            raise ValueError
        instance = entry.get("instance")
        if instance is not None and (not isinstance(instance, str) or not re.fullmatch("[0-9a-f]{32}", instance)):
            raise ValueError
    except FileNotFoundError:
        raise Offline("本机游戏尚未启动。") from None
    except (OSError, ValueError, TypeError, KeyError):
        raise ValueError("游戏登记地址无效，请重新启动对应游戏。") from None
    return Endpoint(origin, int(match[1]), entry["pid"], instance)


def request(endpoint: Endpoint, path: str, payload: dict | None = None, timeout=15) -> dict:
    connection = http.client.HTTPConnection("127.0.0.1", endpoint.port, timeout=timeout)
    body = None
    headers = {"Accept": "application/json", "Origin": endpoint.origin}
    if payload is not None:
        body = json.dumps(payload, ensure_ascii=False,
                          allow_nan=False).encode("utf-8")
        if len(body) > MAX_BYTES:
            raise ValueError("提交内容超过 4 MiB。")
        headers["Content-Type"] = "application/json"
    try:
        connection.request("GET" if body is None else "POST", path, body=body, headers=headers)
        response = connection.getresponse()
        # HTTPConnection never follows redirects or consults system proxy settings.
        if 300 <= response.status < 400:
            raise ValueError("游戏地址返回重定向，请重新启动对应游戏。")
        data = response.read(MAX_BYTES + 1)
        if len(data) > MAX_BYTES:
            raise ValueError("游戏响应超过 4 MiB。")
        try:
            result = json.loads(data)
        except (UnicodeError, json.JSONDecodeError):
            raise ValueError("本机游戏未返回有效 JSON。") from None
        if not isinstance(result, dict):
            raise ValueError("本机游戏返回的数据格式错误。")
        if not 200 <= response.status < 300:
            detail = result.get("error")
            raise ValueError(detail[:400] if isinstance(detail, str) and detail else
                             f"本机游戏拒绝了请求（HTTP {response.status}）。")
        return result
    except (OSError, http.client.HTTPException):
        raise ValueError("与本机游戏的连接中断。可用 game_status 或 get_turn 核对进度。") from None
    finally:
        connection.close()


def verified_endpoint(pack_id: Pack) -> Endpoint:
    endpoint = origin_for(pack_id)
    try:
        health = request(endpoint, "/api/auto/health", timeout=2)
    except ValueError:
        raise Offline("登记的进程无法通过游戏健康检查。") from None
    if (health.get("service") != "research_games" or health.get("game") != family_of(pack_id)
            or health.get("pid") != endpoint.pid
            or (endpoint.instance is not None and health.get("instance") != endpoint.instance)):
        raise Offline("登记地址已由其他进程使用。")
    return endpoint


@contextmanager
def launch_lock(path: Path):
    """Serialize cold launches across independent MCP clients."""
    with path.open("a+b") as guard:
        if guard.seek(0, 2) == 0:
            guard.write(b"0")
            guard.flush()
        deadline = time.monotonic() + 23
        while True:
            guard.seek(0)
            try:
                if os.name == "nt":
                    import msvcrt
                    msvcrt.locking(guard.fileno(), msvcrt.LK_NBLCK, 1)
                else:
                    import fcntl
                    fcntl.flock(guard.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except OSError as exc:
                if exc.errno not in (errno.EACCES, errno.EAGAIN, errno.EDEADLK):
                    raise
                if time.monotonic() >= deadline:
                    raise ValueError("另一会话正启动此游戏，请稍后用同一请求编号重试。") from None
                time.sleep(0.1)
        # Closing the file releases the OS lock even after exceptions or process death.
        yield


def start_service(pack_id: Pack) -> Endpoint:
    try:
        return verified_endpoint(pack_id)
    except Offline:
        pass
    registry = registry_dir()
    override = os.environ.get("GAME_MCP_DATA_DIR")
    if "GAME_MCP_REGISTRY" in os.environ and not override:
        raise ValueError("自定义登记目录没有正在运行的游戏。隔离测试须提供 GAME_MCP_DATA_DIR 才能启动。")
    family = family_of(pack_id)
    script = HERE.parent / ("方格子容纳/game.py" if family == "packing" else "四境驿站/solo_server.py")
    data_dir = Path(override) if override else script.parent
    registry.mkdir(parents=True, exist_ok=True)
    data_dir.mkdir(parents=True, exist_ok=True)
    with launch_lock(registry / (family + ".lock")):
        try:
            return verified_endpoint(pack_id)
        except Offline:
            pass
        locations = [HERE, registry, data_dir, Path(tempfile.gettempdir())]
        if os.name == "nt":
            locations.append(Path(os.environ["SystemRoot"]))
        if any(shutil.disk_usage(directory).free < 10 * 1024**3 for directory in locations):
            raise ValueError("任务相关磁盘可用空间低于 10 GiB，已停止启动游戏。")
        command = [sys.executable, "-B", "-X", "utf8", str(script), "--port", "0",
                   "--db", str((data_dir / (family + ".sqlite3")).resolve())]
        # A Windows venv launcher can start the Python server under a different PID.
        launch_instance = secrets.token_hex(16)
        child_env = dict(os.environ, GAME_MCP_REGISTRY=str(registry), GAME_MCP_LAUNCH_INSTANCE=launch_instance)
        with (registry / (family + ".log")).open("ab") as log:
            child = subprocess.Popen(command, cwd=script.parent, stdin=subprocess.DEVNULL, stdout=log, stderr=log,
                                     env=child_env,
                                     creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                                     start_new_session=os.name != "nt")
        deadline = time.monotonic() + 16
        try:
            while child.poll() is None and time.monotonic() < deadline:
                try:
                    endpoint = verified_endpoint(pack_id)
                    if endpoint.instance == launch_instance:
                        return endpoint
                except Offline:
                    pass
                time.sleep(0.12)
            raise ValueError(f"游戏未在时限内完成启动，可核对运行目录中的 {family}.log。")
        except BaseException:
            if child.poll() is None:
                child.terminate()
                child.wait(timeout=5)
            raise


def game_request(pack_id: Pack, path: str, payload: dict | None = None) -> dict:
    batch = path.startswith("/api/packing/batch")
    starts = path in ("/api/auto/start", "/api/packing/batch/start")
    status = path.startswith("/api/auto?pack=") or (batch and payload is None)
    try:
        endpoint = start_service(pack_id) if starts else verified_endpoint(pack_id)
    except Offline as exc:
        if status:
            if batch:
                return {"batch": None, "status": "offline", "message": str(exc)}
            return {"pack_id": pack_id, "id": None, "status": "offline",
                    "message": f"{exc} 可用 start_game 启动。"}
        raise
    body = {"pack_id": pack_id, **payload} if payload is not None and not batch and path != "/api/lite/trace" else payload
    result = request(endpoint, path, body)
    if starts or status or batch:
        result["origin"] = endpoint.origin
    return result


READ = ToolAnnotations(readOnlyHint=True, destructiveHint=False, idempotentHint=True, openWorldHint=False)
WRITE = ToolAnnotations(readOnlyHint=False, destructiveHint=False, idempotentHint=True, openWorldHint=False)


def make_server(port: int, read_only: bool = False, *, dispatch=game_request) -> FastMCP:
    address = f"127.0.0.1:{port}"
    instructions = (
        "先用 game_status 发现网页等待中的任务，再用 get_turn 读取题面。"
        "用 evaluate_candidate 复算一个或多个构造，在对话里给出得分、候选和结论。"
        if read_only else
        "先用 game_status 发现网页等待中的任务，用 claim_turn 接手当前轮并获取题面。"
        "长轮次通过 report_progress 报告实际阶段及已检查的候选总数，网页可实时查看。"
        "根据题面生成候选并通过 submit_turn 提交；若有下一轮则接手并继续，任务结束后停止。"
        "单会话方格并行批次用 packing_batch_status 读取，再用 plan_packing_batch 按关下发策略。"
        "检查当前普通套餐额度，使用同一会话的 CODEX_THREAD_ID，每轮等待真实本机结果后再规划。"
        "仅在用户要求新开任务时使用 start_game 或 start_packing_batch。"
    )
    server = FastMCP(
        "研究游戏",
        instructions=instructions,
        host="127.0.0.1", port=port, streamable_http_path="/mcp",
        stateless_http=True, json_response=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True, allowed_hosts=[address],
            allowed_origins=[f"http://{address}"],
        ),
    )

    @server.tool(annotations=READ)
    async def game_status(pack_id: Pack) -> dict[str, Any]:
        """读取题包的网页队列状态，发现正在等待模型的任务 ID。"""
        return await asyncio.to_thread(dispatch, pack_id, f"/api/auto?pack={pack_id}")

    @server.tool(annotations=READ)
    async def get_turn(pack_id: Pack, job_id: JobId) -> dict[str, Any]:
        """读取已知任务当前轮号和完整题面，不领取、不推进任务。"""
        return await asyncio.to_thread(dispatch, pack_id, "/api/auto/turn", {"id": job_id})

    @server.tool(annotations=READ)
    async def evaluate_candidate(pack_id: Pack, solution: dict) -> dict[str, Any]:
        """只读复算构造的分数与哈希。驿站传含 blocks 的对象，装箱传含 poses 的对象。"""
        return await asyncio.to_thread(dispatch, pack_id, "/api/auto/evaluate", {"solution": solution})

    @server.tool(annotations=READ)
    async def packing_batch_status(batch_id: JobId | None = None) -> dict[str, Any]:
        """读取最新或指定方格批次的各关状态、验收成果、累计与估算用量。"""
        path = "/api/packing/batch" + (f"?batch_id={batch_id}" if batch_id else "")
        return await asyncio.to_thread(dispatch, PACKS[0], path)

    if read_only:
        return server

    @server.tool(annotations=WRITE)
    async def start_packing_batch(packs: list[PackingPack], rounds: Literal[1, 3, 5],
                                  parallel: Literal[1, 2, 3], request_id: RequestId) -> dict[str, Any]:
        """按用户指定的新批次开局，关卡互不重复。批次等待同一 Codex 会话分派。"""
        return await asyncio.to_thread(dispatch, PACKS[0], "/api/packing/batch/start",
                                      {"packs": packs, "rounds": rounds, "parallel": parallel,
                                       "request_id": request_id})

    @server.tool(annotations=WRITE)
    async def plan_packing_batch(batch_id: JobId, plans: list[PackingPlan], quota: dict,
                                 thread_id: str | None = None) -> dict[str, Any]:
        """为本批次分派当前轮的本机搜索策略。thread_id 填当前对话的 CODEX_THREAD_ID。
        quota 只传刚取得的 ordinaryUsageAllowed、适用 Codex 窗口 primary 和 secondary；
        状态无法确认时传普通套餐不可用，使当前批次暂停。一个请求可以分派多个关卡。
        """
        thread_id = thread_id or os.environ.get("CODEX_THREAD_ID")
        return await asyncio.to_thread(dispatch, PACKS[0], "/api/packing/batch/plan",
                                      {"batch_id": batch_id, "thread_id": thread_id, "quota": quota,
                                       "plans": [plan.model_dump() for plan in plans]})

    @server.tool(annotations=WRITE)
    async def stop_packing_batch(batch_id: JobId, pack_id: PackingPack | None = None) -> dict[str, Any]:
        """停止整个并行批次或其中一关，保留裁判已通过的成绩与楼层。"""
        return await asyncio.to_thread(dispatch, PACKS[0], "/api/packing/batch/stop",
                                      {"batch_id": batch_id, "pack_id": pack_id})

    @server.tool(annotations=WRITE)
    async def claim_turn(pack_id: Pack, job_id: JobId, thread_id: str | None = None) -> dict[str, Any]:
        """接手已知任务的当前轮并返回题面。方格冲榜速跑可传本会话 CODEX_THREAD_ID 以显示实测用量。"""
        payload = {"id": job_id}
        if thread_id is not None:
            payload["thread_id"] = thread_id
        return await asyncio.to_thread(dispatch, pack_id, "/api/auto/claim", payload)

    @server.tool(annotations=WRITE)
    async def report_progress(pack_id: Pack, job_id: JobId, turn: PositiveInt,
                              checked: Annotated[int, Field(ge=0, lt=2**53, strict=True)],
                              stage: Annotated[str, Field(min_length=1, max_length=120)]) -> dict[str, Any]:
        """报告已接手轮次的实际工作阶段及累计检查候选数。此操作不提交成绩。"""
        return await asyncio.to_thread(dispatch, pack_id, "/api/auto/progress",
                                      {"id": job_id, "turn": turn, "checked": checked, "stage": stage})

    @server.tool(annotations=WRITE)
    async def report_packing_trial(job_id: JobId, turn: PositiveInt,
                                   checked: Annotated[int, Field(ge=0, lt=2**53, strict=True)],
                                   stage: Annotated[str, Field(min_length=1, max_length=120)],
                                   summary: Annotated[str, Field(min_length=1, max_length=300)],
                                   solution: dict | None = None) -> dict[str, Any]:
        """在 26 块轻量版公开本轮可简述的研究假设、实际检验数量及可选的真实试摆。游戏会复算结果。"""
        return await asyncio.to_thread(dispatch, "square-26", "/api/lite/trace",
                                      {"id": job_id, "turn": turn, "checked": checked, "stage": stage,
                                       "summary": summary, "solution": solution})

    @server.tool(annotations=WRITE)
    async def start_game(pack_id: Pack, rounds: Literal[1, 3, 5], request_id: RequestId) -> dict[str, Any]:
        """按指定轮数开局。若游戏离线则后台启动并返回网页地址；重试请沿用同一个 request_id。"""
        return await asyncio.to_thread(dispatch, pack_id, "/api/auto/start",
                                      {"rounds": rounds, "mode": "mcp", "request_id": request_id})

    @server.tool(annotations=WRITE)
    async def submit_turn(pack_id: Pack, job_id: JobId, turn: PositiveInt, candidate: dict) -> dict[str, Any]:
        """将一份候选交给指定轮次的裁判，保存回执。按返回的下一轮题面继续，结束时停止。"""
        return await asyncio.to_thread(dispatch, pack_id, "/api/auto/submit",
                                      {"id": job_id, "turn": turn, "candidate": candidate})

    @server.tool(annotations=WRITE)
    async def stop_game(pack_id: Pack, job_id: JobId) -> dict[str, Any]:
        """停止指定游戏任务的后续轮次，保留已完成的回执。"""
        return await asyncio.to_thread(dispatch, pack_id, "/api/auto/stop", {"id": job_id})

    return server


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--transport", choices=("stdio", "streamable-http"), default="stdio")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT)
    parser.add_argument("--read-only", action="store_true", help="仅注册读取与纯计分工具")
    args = parser.parse_args()
    if not 1 <= args.port <= 65535:
        parser.error("端口须为 1 到 65535 的整数")
    make_server(args.port, read_only=args.read_only).run(transport=args.transport)
