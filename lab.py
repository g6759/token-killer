"""Launch the local research games together."""

import argparse
from concurrent.futures import ThreadPoolExecutor
from contextlib import ExitStack, contextmanager
from datetime import datetime, timezone
from http.client import HTTPConnection, HTTPException
from http.server import ThreadingHTTPServer
import json
import os
from pathlib import Path
import re
import sys
from threading import Event, Lock, Thread
from urllib.parse import parse_qs, urlsplit

ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / folder) for folder in ("四境驿站", "方格子容纳", "自动运行")]

from auto_jobs import MCP_CLIENTS, publish_origin
from benchmark import (LIVE_WINDOW_SECONDS, PLANS, PORTFOLIO_SQUARES, SCENARIOS, WINDOWS,
                       SessionMeter, current_progress, live_suite, replay_suite, resumable_run)
from continuous_history import latest_history, new_history_path, window_rate, write_history
from scoreboard import collect as collect_scores, packing_case_progress, page as score_page
from solo_server import RequestError, SoloHandler, SoloServer
from subscription_worker import confirm_setup, peek_setup_status, start_setup
from workspace_io import check_disk
from quota import MAX_AGE_SECONDS, QuotaError, QuotaGate, get_shared_gate, read_allowance
from 方格子容纳.game import PackingServer
import research_games
from 游戏MCP.setup_mcp import configs as mcp_configs


GAMES = {
    "packing": (PackingServer, "方格子容纳/packing.sqlite3", "square-26", "/lite"),
    "relay": (SoloServer, "四境驿站/relay.sqlite3", "relay-49-frontier", "/"),
}
CONCURRENCY_ARCHIVES = {
    "square-relay-frontier-comparison": ("实跑-方格与接力分支", "square_relay_frontier_benchmark"),
    "concurrency": ("实跑-并发度", "concurrency_benchmark"),
    "high-concurrency": ("实跑-高并发", "high_concurrency_benchmark"),
    "sixteen-lane-comparison": ("实跑-八路十六路", "sixteen_lane_benchmark"),
    "portfolio-comparison": ("实跑-多题组合", "portfolio_benchmark"),
    "portfolio-scale-comparison": ("实跑-多题并发", "portfolio_scale_benchmark"),
    "relay-mix-comparison": ("实跑-接力配比", "relay_mix_benchmark"),
    "relay-scale-comparison": ("实跑-接力并发", "relay_scale_benchmark"),
    "relay-twenty-lane-comparison": ("实跑-接力二十路", "relay_twenty_lane_benchmark"),
    "relay-twenty-two-lane-comparison": ("实跑-接力二十二路", "relay_twenty_two_lane_benchmark"),
    "relay-twenty-three-lane-comparison": ("实跑-接力二十三路", "relay_twenty_three_lane_benchmark"),
    "relay-target-comparison": ("实跑-接力目标", "relay_target_benchmark"),
}
WEB = ROOT / "实验室"
MAX_SESSION_WINDOWS = 12


def _load_game(origin, path):
    connection = HTTPConnection(urlsplit(origin).netloc, timeout=2)
    try:
        connection.request("GET", path)
        with connection.getresponse() as response:
            if response.status != 200:
                raise ValueError("游戏暂时不可用。")
            return json.loads(response.read(2 * 1024**2))
    finally:
        connection.close()


def _existing_origin(game):
    registry = Path(os.environ.get("GAME_MCP_REGISTRY", ROOT / "游戏MCP" / "运行中"))
    try:
        marker = json.loads((registry / (game + ".json")).read_text(encoding="utf-8"))
        origin = marker["origin"]
        if not re.fullmatch(r"http://127\.0\.0\.1:[1-9][0-9]{0,4}", origin):
            return None
        health = _load_game(origin, "/api/auto/health")
        if (health["service"] == "research_games" and health["game"] == game
                and health["instance"] == marker["instance"] and health["pid"] == marker["pid"]):
            return origin
    except (HTTPException, OSError, TypeError, KeyError, ValueError):
        pass
    return None


def _visible_game(origin, game):
    _, _, pack, page = GAMES[game]
    value = {"url": origin + page, "score": None, "records": None, "job": {}}
    try:
        snapshot = _load_game(origin, f"/api/game?pack={pack}")
        state = snapshot["state"]
        value["score"] = state["side"] if game == "packing" else f"{state['score']} / {state['total']}"
        lineage = snapshot.get("lineage")
        if isinstance(lineage, list) and lineage:
            value["records"] = len(lineage) - 1
        job = _load_game(origin, f"/api/auto?pack={pack}")
        value["job"] = {key: job.get(key) for key in (
            "id", "status", "mode", "stage", "rounds", "completed", "work", "elapsed_seconds",
            "client", "target_tokens", "end_reason", "metered_rounds", "effective_tokens", "recent_tokens_per_minute")}
    except (HTTPException, OSError, TypeError, KeyError, ValueError):
        pass
    return value


def _weekly_window_snapshot(value):
    if not isinstance(value, dict):
        return None
    used = value.get("used_percent")
    minutes = value.get("window_duration_minutes")
    reset = value.get("resets_at")
    sampled = value.get("sampled_at_utc")
    source = value.get("source")
    if (type(used) not in (int, float) or not 0 <= used <= 100
            or type(minutes) is not int or minutes < 1
            or (reset is not None and type(reset) not in (int, float, str))
            or not isinstance(sampled, str) or not isinstance(source, str)):
        return None
    return {"used_percent": used, "window_duration_minutes": minutes, "resets_at": reset,
            "sampled_at_utc": sampled, "source": source}


def _application_limit_declaration(value):
    if not isinstance(value, dict):
        return None
    limit = value.get("weekly_limit_percent")
    source = value.get("source")
    if type(limit) not in (int, float) or not 0 <= limit <= 100 or not isinstance(source, str):
        return None
    return {"weekly_limit_percent": limit, "actual_used_percent": None, "source": source}


def _public_weekly_meter(value):
    if not isinstance(value, dict) or not isinstance(value.get("game_window"), str):
        return None
    result = {"game_window": value["game_window"]}
    for key in ("before", "after"):
        snapshot = value.get(key)
        if snapshot is None:
            result[key] = None
            continue
        if not isinstance(snapshot, dict):
            return None
        result[key] = {
            "codex_account_shared_weekly": _weekly_window_snapshot(
                snapshot.get("codex_account_shared_weekly")),
            "application_local_declaration": _application_limit_declaration(
                snapshot.get("application_local_declaration")),
        }
    before_window = result["before"]["codex_account_shared_weekly"] if result["before"] else None
    after_window = result["after"]["codex_account_shared_weekly"] if result["after"] else None
    same_week = (
        isinstance(before_window, dict) and isinstance(after_window, dict)
        and before_window["window_duration_minutes"] == after_window["window_duration_minutes"] == 10080
        and before_window["resets_at"] is not None
        and before_window["resets_at"] == after_window["resets_at"]
    )
    comparable = value.get("comparable") is True and same_week
    change = value.get("percentage_point_change")
    if type(change) not in (int, float):
        change = None
        comparable = False
    tokens = value.get("effective_game_tokens")
    if type(tokens) is not int or tokens < 0:
        tokens = None
    result.update(comparable=comparable, percentage_point_change=change if comparable else None,
                  effective_game_tokens=tokens)
    return result


def _latest_weekly_meter(results):
    """Read one latest top-level weekly record; never walk reply or receipt trees."""
    try:
        candidates = list(results.glob("*/*-周限额-*.json"))
        if not candidates:
            return None
        latest = max(candidates, key=lambda path: path.stat().st_mtime_ns)
        return _public_weekly_meter(json.loads(latest.read_text(encoding="utf-8-sig")))
    except (OSError, ValueError, TypeError):
        return None


class LabHandler(SoloHandler):
    def _mcp_run(self, stop=False):
        body = self._read_body()
        required = {"game", "id"} if stop else {"game", "client", "rounds", "request_id"}
        allowed = required if stop else required | {"target_tokens"}
        if not isinstance(body, dict) or not required <= body.keys() <= allowed or body["game"] not in tuple(GAMES):
            raise ValueError("Invalid MCP run / 对局参数无效")
        game = body["game"]
        if stop:
            active = _load_game(self.server.origins[game], "/api/auto?pack=" + GAMES[game][2])
            if not active.get("client") or active.get("id") != body["id"]:
                raise ValueError("This client run has changed / 当前客户端对局已变更")
            payload = {"id": body["id"]}
        else:
            if body["client"] not in tuple(MCP_CLIENTS):
                raise ValueError("Invalid client / 客户端无效")
            payload = {key: value for key, value in body.items() if key != "game"}
            payload["mode"] = "mcp"
        payload["pack_id"] = GAMES[game][2]
        origin = self.server.origins[game]
        conn = HTTPConnection(urlsplit(origin).netloc, timeout=5)
        try:
            conn.request("POST", "/api/auto/" + ("stop" if stop else "start"), json.dumps(payload),
                         headers={"Origin": origin, "Content-Type": "application/json"})
            with conn.getresponse() as reply:
                self._json(reply.status, json.loads(reply.read(2 * 1024**2)))
        except (HTTPException, OSError) as exc:
            raise ValueError("Local game unavailable / 本机游戏暂时无法连接") from exc
        finally:
            conn.close()

    def _get(self):
        self._check_origin()
        target = self._target()
        path = target.path
        if path == "/api/research":
            query = parse_qs(target.query)
            game = query.get("game", [""])[0]
            rules = research_games.get_pack(game)
            if "example" in query:
                example = query["example"][0]
                if example == "reference" and game == "cap-set-8d":
                    from research_games.discrete import cap_set_8d_401_reference
                    solution = cap_set_8d_401_reference()
                elif example == "demo" and game.startswith("cap-set"):
                    from research_games.discrete import cap_set_improvement_example
                    solution = cap_set_improvement_example(game)
                elif example == "demo" and game.startswith("golomb-ruler"):
                    from research_games.discrete import golomb_ruler_improvement_example
                    solution = golomb_ruler_improvement_example(game)
                elif example == "demo" and game == "lorenz-divergence":
                    solution = {"initial_x_tick": 150}
                else:
                    raise ValueError("Unknown example / 未知示例")
                self._json(200, {"solution": solution, "example": example})
                return
            store = research_games.build_store(self.server.data / research_games.DATABASE)
            best = store.best(game)
            response = {"game": game, "best": best, "result": rules.evaluate(best["solution"])}
            if game == "lorenz-divergence":
                from research_games.lorenz import sample_trajectory
                response["trace"] = sample_trajectory(best["solution"], sample_count=241)
            self._json(200, response)
            return
        if path == "/api/lab":
            self._json(200, self.server.state())
            return
        if path == "/client-guide":
            self._send(200, (ROOT / "CLIENTS.md").read_bytes(), "text/plain; charset=utf-8")
            return
        if path == "/api/lab/mcp-config":
            names = {"codex-mcp": "codex.config.toml", "claude-code": "claude-code.mcp.json",
                     "antigravity": "antigravity.mcp_config.json"}
            requested = parse_qs(target.query).get("client", [])
            if len(requested) != 1 or requested[0] not in names:
                raise ValueError("Unknown MCP client / 客户端无效")
            self._json(200, {"config": mcp_configs(ROOT, sys.executable)[names[requested[0]]]})
            return
        if path in {"/scores", "/leaderboard.json"}:
            proof = collect_scores(self.server.data)
            if path == "/scores":
                self._send(200, score_page(proof).encode("utf-8"), "text/html; charset=utf-8")
            else:
                self._json(200, proof)
            return
        files = {
            "/": ("index.html", "text/html"),
            "/research": ("research.html", "text/html"),
            "/research.js": ("research.js", "text/javascript"),
            "/research.css": ("research.css", "text/css"),
            "/console.js": ("console.js", "text/javascript"),
            "/lab.css": ("lab.css", "text/css"),
            "/lab.js": ("lab.js", "text/javascript"),
        }
        if path not in files:
            raise RequestError(404, "找不到页面。")
        file, mime = files[path]
        self._send(200, (WEB / file).read_bytes(), f"{mime}; charset=utf-8")

    def _post(self):
        self._check_origin(required=True)
        target = self._target()
        if target.path in {"/api/lab/mcp-start", "/api/lab/mcp-stop"} and not target.query:
            self._mcp_run(stop=target.path.endswith("/mcp-stop"))
            return
        if target.path == "/api/lab/quota" and not target.query:
            if self._read_body() != {}:
                raise ValueError("额度刷新不接受参数。 / Quota refresh expects an empty object.")
            self._json(200, {"allowance": self.server.refresh_quota()})
            return
        if target.path == "/api/lab/session" and not target.query:
            payload = self._read_body()
            if (not isinstance(payload, dict) or "scenario" not in payload
                    or set(payload) - {"scenario", "windows", "target_tokens"}):
                raise ValueError("请选择玩法和窗口数。 / Choose a scenario and window count.")
            self._json(202, self.server.start("session", scenario=payload["scenario"],
                                              windows=payload.get("windows", 1), target_tokens=payload.get("target_tokens")))
            return
        if target.path in {"/api/research/check", "/api/research/submit", "/api/research/benchmark"} and not target.query:
            payload = self._read_body()
            if not isinstance(payload, dict):
                raise ValueError("Invalid game request / 游戏请求格式有误")
            game = payload.get("game")
            rules = research_games.get_pack(game)
            if target.path.endswith("/benchmark"):
                if set(payload) != {"game"}:
                    raise ValueError("Unexpected parameters / 参数有误")
                try:
                    self._json(202, self.server.start(game))
                except (RuntimeError, OSError) as exc:
                    raise ValueError(str(exc)) from None
                return
            submit = target.path.endswith("/submit")
            required = {"game", "solution", "parent"} if submit else {"game", "solution"}
            if set(payload) != required:
                raise ValueError("Invalid candidate fields / 候选字段有误")
            result = rules.evaluate(payload["solution"])
            output = {"result": result}
            if submit:
                if result.get("certified") is False:
                    raise ValueError("Numerically unresolved / 数值尚未通过复核")
                store = research_games.build_store(self.server.data / research_games.DATABASE)
                store.submit(game, payload["parent"], "player", result["solution"], "")
                output["best"] = store.best(game)
            if game == "lorenz-divergence":
                from research_games.lorenz import sample_trajectory
                output["trace"] = sample_trajectory(result["solution"], sample_count=241)
            self._json(200, output)
            return
        operations = {
            "/api/lab/replay": lambda: self.server.start("replay"),
            "/api/lab/live": lambda: self.server.start("live"),
            "/api/lab/mixed": lambda: self.server.start("mixed"),
            "/api/lab/mixed-four": lambda: self.server.start("mixed-four"),
            "/api/lab/mixed-eight": lambda: self.server.start("mixed-eight"),
            "/api/lab/mixed-sixteen": lambda: self.server.start("mixed-sixteen"),
            "/api/lab/mixed-portfolio": lambda: self.server.start("mixed-portfolio"),
            "/api/lab/mixed-portfolio-twenty-four": lambda: self.server.start("mixed-portfolio-twenty-four"),
            "/api/lab/relay-sixteen": lambda: self.server.start("relay-sixteen"),
            "/api/lab/relay-twenty": lambda: self.server.start("relay-twenty"),
            "/api/lab/relay-twenty-two": lambda: self.server.start("relay-twenty-two"),
            "/api/lab/relay-twenty-three": lambda: self.server.start("relay-twenty-three"),
            "/api/lab/relay-twenty-four": lambda: self.server.start("relay-twenty-four"),
            "/api/lab/relay-dual-target": lambda: self.server.start("relay-dual-target"),
            "/api/lab/concurrency": lambda: self.server.start("concurrency"),
            "/api/lab/square-relay-frontier-comparison": lambda: self.server.start("square-relay-frontier-comparison"),
            "/api/lab/square-frontier-1152-40": lambda: self.server.start("square-frontier-1152-40"),
            "/api/lab/relay-frontier-1152-40": lambda: self.server.start("relay-frontier-1152-40"),
            "/api/lab/high-concurrency": lambda: self.server.start("high-concurrency"),
            "/api/lab/sixteen-lane-comparison": lambda: self.server.start("sixteen-lane-comparison"),
            "/api/lab/portfolio-comparison": lambda: self.server.start("portfolio-comparison"),
            "/api/lab/portfolio-scale-comparison": lambda: self.server.start("portfolio-scale-comparison"),
            "/api/lab/relay-mix-comparison": lambda: self.server.start("relay-mix-comparison"),
            "/api/lab/relay-scale-comparison": lambda: self.server.start("relay-scale-comparison"),
            "/api/lab/relay-twenty-lane-comparison": lambda: self.server.start("relay-twenty-lane-comparison"),
            "/api/lab/relay-twenty-two-lane-comparison": lambda: self.server.start("relay-twenty-two-lane-comparison"),
            "/api/lab/relay-twenty-three-lane-comparison": lambda: self.server.start("relay-twenty-three-lane-comparison"),
            "/api/lab/relay-target-comparison": lambda: self.server.start("relay-target-comparison"),
            "/api/lab/continuous": lambda: self.server.start("continuous", windows=payload.get("windows")),
            "/api/lab/resume": lambda: self.server.start("resume"),
            "/api/lab/stop": self.server.stop,
            "/api/lab/connect": lambda: self.server.connection(start_setup),
            "/api/lab/confirm": lambda: self.server.connection(confirm_setup, payload["percent"]),
        }
        if target.path not in operations or target.query:
            raise RequestError(404, "找不到操作。")
        payload = self._read_body()
        expected = {"percent"} if target.path.endswith("/confirm") else set()
        if target.path == "/api/lab/continuous" and isinstance(payload, dict):
            expected = {"windows"} if "windows" in payload else set()
        if not isinstance(payload, dict) or payload.keys() != expected:
            raise ValueError("操作参数无效。")
        try:
            self._json(202, operations[target.path]())
        except (RuntimeError, OSError) as exc:
            raise ValueError(str(exc)) from None


class LabServer(ThreadingHTTPServer):
    def __init__(self, data, port, origins):
        super().__init__(("127.0.0.1", port), LabHandler)
        authority = f"127.0.0.1:{self.server_port}"
        self.origin = f"http://{authority}"
        self.host_headers = {authority}
        self.origins = origins
        self.data = data
        self.results = data / "验收"
        self.lock = Lock()
        self.task = None
        self.task_dir = None
        self.continuous = latest_history(self.results)
        self.continuous_archive = None
        self.continuous_windows = []
        self.session_meter = None
        self.progress_receipts = {}
        self.abort = Event()
        self.quota = get_shared_gate()
        self.protection_error = None
        self.protection_reason = None
        self.pool = ThreadPoolExecutor(max_workers=1, thread_name_prefix="lab-benchmark")

    def server_close(self):
        super().server_close()
        if hasattr(self, "pool"):
            self.abort.set()
            self.pool.shutdown()

    def _recent_reports(self):
        reports = []
        for path in self.results.glob("吞吐对照-*/summary.json"):
            try:
                reports.append((path.stat().st_mtime_ns, path))
            except OSError:
                continue
        benchmark, mixed, records = None, None, None
        comparisons = {name: None for name in CONCURRENCY_ARCHIVES}
        priorities = {name: index for index, name in enumerate(CONCURRENCY_ARCHIVES)}
        featured, best = "mixed", (-1, -1)
        for _, path in sorted(reports, reverse=True):
            try:
                report = json.loads(path.read_text(encoding="utf-8"))
                scenarios = report.get("scenarios")
                if not isinstance(scenarios, dict):
                    continue
                if records is None and report.get("live"):
                    records = sum(entry.get("project_records", 0) for entry in scenarios.values()
                                  if isinstance(entry, dict))
                folder = path.parent.name
                comparison = next((name for name, (title, _) in CONCURRENCY_ARCHIVES.items()
                                   if folder.startswith(f"吞吐对照-{title}-")), None)
                if comparison is not None:
                    winner = report.get("winner")
                    if (report.get("live") and report.get("incomplete") is False and isinstance(winner, str)
                            and winner in PLANS[comparison]):
                        entry = scenarios.get(winner)
                        if isinstance(entry, dict):
                            rate = entry.get("tokens_per_minute", 0)
                            if type(rate) in (int, float) and 0 <= rate < float("inf"):
                                rank = (rate, priorities[comparison])
                                if rank > best and entry.get("rankable", rate == 0):
                                    featured, best = winner, rank
                    if comparisons[comparison] is None and report.get("live"):
                        if report.get("incomplete"):
                            for name in PLANS[comparison].keys() - scenarios.keys():
                                try:
                                    summary = path.parent / name / "summary.json"
                                    earlier = json.loads(summary.read_text(encoding="utf-8"))
                                except (OSError, ValueError):
                                    continue
                                if (isinstance(earlier, dict) and earlier.get("live")
                                        and earlier.get("packs") == list(PLANS[comparison][name])):
                                    scenarios[name] = earlier
                        comparisons[comparison] = report
                elif folder.startswith("吞吐对照-混合冲榜-"):
                    entry = next(iter(scenarios.values())) if len(scenarios) == 1 else None
                    if mixed is None and report.get("live") and isinstance(entry, dict):
                        mixed = entry
                elif benchmark is None or (report.get("live") and not benchmark.get("live")):
                    benchmark = report
            except (OSError, ValueError, AttributeError):
                continue
        estimate = round(best[0] * LIVE_WINDOW_SECONDS / 60) if best[0] > 0 else None
        return benchmark, mixed, comparisons, records or 0, featured, estimate

    def state(self):
        with self.lock:
            running = self.task is not None and not self.task.done()
            error = not running and self.task is not None and self.task.exception()
            task_dir = self.task_dir if running else None
            receipts_cache = self.progress_receipts
            stopping = running and self.abort.is_set()
            continuous = None if self.continuous is None else self.continuous.copy()
        try:
            subscription = peek_setup_status()
        except RuntimeError:
            subscription = {"ready": False, "connected": False, "error_message": "此设备暂不支持订阅连接。"}
        benchmark, mixed, comparisons, records, featured, estimate = self._recent_reports()
        progress = current_progress(task_dir, receipts_cache=receipts_cache)
        if continuous is not None and task_dir is not None:
            measured = progress
            if measured is None:
                # The window can finish writing just before the worker adds it to the session.
                try:
                    report = json.loads((task_dir / "summary.json").read_text(encoding="utf-8"))
                    measured = report["scenarios"].get(continuous["scenario"])
                except (OSError, ValueError, KeyError):
                    measured = None
            if measured is not None:
                continuous["effective_tokens"] += measured["uncached_input_tokens"] + measured["output_tokens"]
                continuous["metered_rounds"] += measured["metered_rounds"]
                continuous["new_records"] += measured.get("project_records", 0)
        if continuous is not None and self.session_meter is not None:
            metering = self.session_meter.snapshot()
            if metering["metered_rounds"] or metering["target_tokens"] is not None:
                continuous.update(metering)
            else:
                continuous.update({key: value for key, value in metering.items()
                                   if key not in {"effective_tokens", "metered_rounds"}})
        return {"games": {name: _visible_game(origin, name) for name, origin in self.origins.items()},
                "benchmark": benchmark, "mixed_run": mixed, "progress": progress,
                "continuous": continuous,
                "packing_cases": packing_case_progress(self.data, ("square-27", *PORTFOLIO_SQUARES[1:])),
                **{key: comparisons[name] for name, (_, key) in CONCURRENCY_ARCHIVES.items()},
                "last_run_records": records, "featured_mixed": featured,
                "featured_seconds": LIVE_WINDOW_SECONDS, "featured_estimate_tokens": estimate,
                "resumable": not running and resumable_run(self.results) is not None,
                "subscription": subscription, "weekly_meter": _latest_weekly_meter(self.results),
                "allowance": self.quota.snapshot(),
                "error": str(error) if error else self.protection_error,
                "run": "stopping" if stopping else "running" if running else "error" if error else "idle"}

    def refresh_quota(self):
        value = self.quota.refresh()
        if value["status"] != "available":
            with self.lock:
                if self.task is not None and not self.task.done():
                    self.protection_error = value["message"]
                    self.protection_reason = "quota"
                    self.abort.set()
        return value

    def _require_quota(self, abort, *, force=False):
        try:
            return self.quota.require(force=force)
        except QuotaError as exc:
            with self.lock:
                self.protection_error = str(exc)
                self.protection_reason = "quota"
                abort.set()
            raise

    def _guarded_live_suite(self, directory, scenarios, *, abort, resume=False, session_meter=None):
        """One shared gate and one watcher protect every lane in this window."""
        self._require_quota(abort, force=True)
        finished = Event()

        def before_infer():
            if abort.is_set():
                raise QuotaError(self.protection_error or "运行已停止。 / Run stopped.")
            self._require_quota(abort)

        def watch():
            while not finished.wait(MAX_AGE_SECONDS):
                if abort.is_set():
                    return
                try:
                    check_disk(self.data)
                    self._require_quota(abort)
                except (ValueError, OSError) as exc:
                    with self.lock:
                        self.protection_error = str(exc)
                        self.protection_reason = "quota" if isinstance(exc, QuotaError) else "disk"
                        abort.set()
                    return

        watcher = Thread(target=watch, name="lab-ordinary-plan-guard", daemon=True)
        watcher.start()
        try:
            options = {"abort": abort, "before_infer": before_infer}
            if session_meter is not None:
                options["session_meter"] = session_meter
            if resume:
                options["resume"] = True
            return live_suite(self.data, directory, scenarios, **options)
        finally:
            finished.set()
            watcher.join(timeout=25)

    def connection(self, action, *args):
        with self.lock:
            if self.task is not None and not self.task.done():
                raise RequestError(409, "请在本次对照结束后更改订阅连接。")
            return action(*args)

    def _run_directory(self, title):
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S-%f")
        return self.results / f"吞吐对照-{title}-{stamp}"

    def _save_continuous(self):
        session = self.continuous
        write_history(self.continuous_archive, session["scenario"], session["window_limit"], self.continuous_windows,
                      end_reason=session["end_reason"], message=session["message"],
                      target_tokens=session.get("target_tokens"), started_at=session.get("started_at"))

    def _run_continuous(self, scenario, abort, windows):
        scenarios = {scenario: WINDOWS[scenario]}
        while not abort.is_set():
            try:
                check_disk(self.data)
            except (ValueError, OSError) as exc:
                with self.lock:
                    self.protection_error, self.protection_reason = str(exc), "disk"
                    abort.set()
                    self.continuous.update(end_reason="disk", message=str(exc))
                    self._save_continuous()
                return
            with self.lock:
                if abort.is_set():
                    break
                directory = self.task_dir = self._run_directory("混合冲榜")
                self.progress_receipts = {}
                self.continuous["window"] += 1
                self.continuous_windows.append(directory)
                self._save_continuous()
            try:
                report = self._guarded_live_suite(directory, scenarios, abort=abort, session_meter=self.session_meter)
            except QuotaError as exc:
                with self.lock:
                    self.task_dir = None
                    self.continuous.update(end_reason="quota", message=str(exc))
                    self._save_continuous()
                return
            entry = report["scenarios"].get(scenario, {})
            errors = entry.get("errors", [])
            metered = entry.get("metered_rounds", 0)
            with self.lock:
                self.task_dir = None
                self.progress_receipts = {}
                session = self.continuous
                session["effective_tokens"] += entry.get("uncached_input_tokens", 0) + entry.get("output_tokens", 0)
                session["metered_rounds"] += metered
                if self.session_meter is not None:
                    snapshot = self.session_meter.snapshot()
                    if snapshot["metered_rounds"] or snapshot["target_tokens"] is not None:
                        session.update(snapshot)
                if metered:
                    session["last_window_rate"] = window_rate(entry)
                session["new_records"] += entry.get("project_records", 0)
                if not report["incomplete"] and metered and entry.get("stop") == "window":
                    session["completed"] += 1
                if self.protection_error:
                    session.update(end_reason=self.protection_reason or "quota", message=self.protection_error)
                    self._save_continuous()
                    return
                if errors:
                    session.update(end_reason="interrupted", message=errors[0])
                    self._save_continuous()
                    return
                if self.session_meter is not None and self.session_meter.reached.is_set():
                    session["end_reason"] = "target"
                    self._save_continuous()
                    return
                if abort.is_set() or report["stopped"]:
                    break
                if report["incomplete"] or not metered:
                    session.update(end_reason="interrupted", message="本窗口没有形成完整的计量回执。")
                    self._save_continuous()
                    return
                if windows is not None and session["completed"] >= windows:
                    session["end_reason"] = "limit"
                    self._save_continuous()
                    return
        with self.lock:
            self.continuous["end_reason"] = "stopped"
            self._save_continuous()

    def start(self, mode, *, windows=None, scenario=None, target_tokens=None):
        with self.lock:
            if self.task is not None and not self.task.done():
                raise RequestError(409, "已有对照正在运行。")
            if mode in {"continuous", "session"}:
                meter = SessionMeter(target_tokens)
                windows = 1 if windows is None else windows
                if type(windows) is not int or not 1 <= windows <= MAX_SESSION_WINDOWS:
                    raise ValueError("窗口数须为 1 至 12 的整数。 / Windows must be an integer from 1 to 12.")
                scenario = "mixed" if scenario is None and mode == "continuous" else scenario
                if not isinstance(scenario, str) or scenario not in WINDOWS:
                    raise ValueError("未知玩法。 / Unknown scenario.")
            elif mode not in {"replay", "live", "resume"} and mode not in WINDOWS and mode not in PLANS:
                raise ValueError("未知运行方式。 / Unknown run mode.")
            if mode != "replay" and not peek_setup_status()["ready"]:
                raise ValueError("请先登录并确认应用周上限。 / Connect and confirm the application weekly limit first.")
            if mode != "replay":
                self.quota.require(force=True)
            check_disk(self.data)
            self.protection_error = None
            self.protection_reason = None
            if mode in {"continuous", "session"}:
                self.abort = Event()
                self.session_meter = meter = SessionMeter(target_tokens)
                self.task_dir = None
                self.continuous = {"scenario": scenario, "window_limit": windows, "window": 0,
                                   "completed": 0, "effective_tokens": 0, "metered_rounds": 0,
                                   "new_records": 0, "end_reason": None, "message": None, **meter.snapshot()}
                self.continuous_archive = new_history_path(self.results)
                self.continuous_windows = []
                self._save_continuous()
                self.task = self.pool.submit(self._run_continuous, scenario, self.abort, windows)
                return {"run": "running", "scenario": scenario, "windows": windows, "target_tokens": target_tokens}
            if mode == "resume":
                self.task_dir = resumable_run(self.results)
                if self.task_dir is None:
                    raise ValueError("没有可继续的对照。")
            else:
                if mode in CONCURRENCY_ARCHIVES:
                    title = CONCURRENCY_ARCHIVES[mode][0]
                else:
                    title = {"live": "实跑", "replay": "离线演练"}.get(mode, "混合冲榜")
                self.task_dir = self._run_directory(title)
            self.continuous = None
            self.session_meter = None
            self.progress_receipts = {}
            self.abort = Event()
            if mode != "replay":
                if mode in WINDOWS:
                    scenarios = {mode: WINDOWS[mode]}
                else:
                    scenarios = PLANS.get(mode, SCENARIOS)
                self.task = self.pool.submit(self._guarded_live_suite, self.task_dir, scenarios, abort=self.abort,
                                             resume=(mode == "resume"))
            else:
                self.task = self.pool.submit(replay_suite, self.data, self.task_dir)
            return {"run": "running"}

    def stop(self):
        with self.lock:
            self.abort.set()
            return {"run": "stopping" if self.task and not self.task.done() else "idle"}


def _serve(stack, server):
    stack.enter_context(server)
    thread = Thread(target=server.serve_forever, kwargs={"poll_interval": 0.1}, daemon=True)
    thread.start()
    stack.callback(thread.join)
    stack.callback(server.shutdown)
    return server


@contextmanager
def launch(data=ROOT, *, port=0, register=True):
    data = Path(data).resolve()
    check_disk(data)
    with ExitStack() as stack:
        origins = {}
        for name, (factory, database, _, _) in GAMES.items():
            existing = _existing_origin(name) if register else None
            if existing:
                origins[name] = existing
                continue
            path = data / database
            path.parent.mkdir(parents=True, exist_ok=True)
            server = _serve(stack, factory(path, port=0))
            if register:
                publish_origin(server, name)
            origins[name] = server.origin
        yield _serve(stack, LabServer(data, port, origins))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=0)
    parser.add_argument("--data-dir", type=Path, default=ROOT)
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("端口须为 0 至 65535。")
    try:
        with launch(args.data_dir, port=args.port, register=args.data_dir.resolve() == ROOT) as server:
            print(server.origin, flush=True)
            if args.open:
                import webbrowser
                webbrowser.open(server.origin)
            Event().wait()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
