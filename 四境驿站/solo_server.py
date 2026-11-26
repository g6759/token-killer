"""Serve the single-player game and its local save on loopback only."""

import argparse
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
import os
import re
from pathlib import Path
import secrets
import sys
from threading import Lock
from urllib.parse import parse_qs, urlsplit

from packs import get_pack
from auto_jobs import GameJobs, publish_origin
from mission import MissionBook, ROUND_LIMITS, instructions
from relay import DEFAULT_DB, PACKS, prompt_for
from relay_store import Store
from report import render


MAX_BODY_BYTES = 128 * 1024
PAGE = Path(__file__).with_name("solo.html")


class RequestError(ValueError):
    def __init__(self, status, message, **details):
        super().__init__(message)
        self.status = status
        self.details = details


def game_view(record):
    stored = record["state"]
    pack = get_pack(stored["pack_id"])
    result = pack.evaluate(record["solution"])
    complete = stored["score"] == stored["total"]
    achievement = "初始成绩"
    if complete:
        achievement = "全覆盖"
    elif stored["parent_hash"] is not None:
        achievement = "已提高覆盖"

    return {
        "state": {
            "pack_id": stored["pack_id"],
            "title": pack.title,
            "score": stored["score"],
            "total": stored["total"],
            "n": pack.n,
            "k": pack.k,
            "count": pack.count,
            "complete": complete,
            "hash": record["hash"],
        },
        "missing": result["missing"],
        "covered_blocks": result["solution"]["blocks"],
        "prompt": "" if complete else prompt_for(record),
        "lineage": record["lineage"],
        "handoff": record["handoff"],
        "achievement": achievement,
    }


def _query(query, server, mission=False):
    parameters = parse_qs(query, keep_blank_values=True, max_num_fields=2, strict_parsing=True)
    allowed = {"pack", "rounds"} if mission else {"pack"}
    if parameters.keys() - allowed or any(len(values) != 1 for values in parameters.values()):
        raise ValueError("查询参数无效")
    pack_id = parameters.get("pack", [server.default_pack])[0]
    if pack_id not in server.packs:
        raise ValueError("请选择有效的题包")
    rounds = parameters.get("rounds", ["1"])[0]
    if rounds not in {str(n) for n in ROUND_LIMITS}:
        raise ValueError("请选择 1、3 或 5 轮")
    return pack_id, int(rounds)


def _submit(store, payload):
    if not isinstance(payload, dict) or payload.keys() != {"author", "candidate"}:
        raise ValueError("请求须包含 author 和 candidate 两个字段")
    author = payload["author"]
    candidate = payload["candidate"]
    if not isinstance(candidate, dict) or candidate.keys() != {"pack_id", "parent_hash", "solution", "handoff"}:
        raise ValueError("候选须包含 pack_id、parent_hash、solution、handoff 四个字段")
    pack_id = candidate["pack_id"]
    if pack_id not in PACKS:
        raise ValueError("请选择有效的题包")
    parent = store.get(candidate["parent_hash"])
    pack = get_pack(pack_id)
    if (parent["state"]["pack_id"], parent["state"]["version"]) != (pack_id, pack.version):
        raise ValueError("父档所属题包或版本与候选不符")
    result = pack.evaluate(candidate["solution"])
    delta = result["score"] - parent["state"]["score"]
    if delta <= 0:
        raise RequestError(
            HTTPStatus.BAD_REQUEST,
            f"本轮覆盖 {result['score']}/{result['total']} 对，未高于父档的 {parent['state']['score']} 对。",
            score=result["score"], total=result["total"], missing=result["missing"],
        )

    known = any(entry["solution"] == result["solution"] for entry in store.history(pack_id))
    saved = store.submit(pack_id, parent["hash"], author, result["solution"], candidate["handoff"])
    best = store.best(pack_id)
    response = game_view(best)
    response["new_result"] = not known
    if known:
        response["message"] = f"这个方案已收录，沿用已有的 {saved['state']['score']} 分存档。"
    elif saved["hash"] == best["hash"]:
        response["message"] = f"已存档，相对父档 +{delta} 对，当前最高覆盖 {best['state']['score']} 对。"
    else:
        response["message"] = f"已保存分支，相对父档 +{delta} 对，当前最高仍为 {best['state']['score']} 对。"
    return response


def _decode_json(raw):
    def unique_fields(pairs):
        result = {}
        for key, value in pairs:
            if key in result:
                raise ValueError("JSON 对象含有重复字段")
            result[key] = value
        return result

    def reject_constant(_):
        raise ValueError("JSON 数字必须有限")

    try:
        return json.loads(raw.decode("utf-8"), object_pairs_hook=unique_fields, parse_constant=reject_constant)
    except (UnicodeDecodeError, json.JSONDecodeError, RecursionError) as exc:
        raise ValueError("请求须提供有效的 UTF-8 JSON") from exc


class SoloHandler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass

    def setup(self):
        self.request.settimeout(5)
        super().setup()

    def _check_origin(self, required=False):
        hosts = self.headers.get_all("Host", [])
        origins = self.headers.get_all("Origin", [])
        if len(hosts) != 1 or hosts[0] not in self.server.host_headers:
            raise RequestError(HTTPStatus.FORBIDDEN, "请使用本机游戏地址访问")
        if (required or origins) and origins != [self.server.origin]:
            raise RequestError(HTTPStatus.FORBIDDEN, "请求须来自当前游戏页面")

    def _send(self, status, payload, content_type):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(payload)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Cross-Origin-Resource-Policy", "same-origin")
        self.send_header("Referrer-Policy", "no-referrer")
        try:
            self.end_headers()
            self.wfile.write(payload)
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def _json(self, status, value):
        self._send(status, json.dumps(value, ensure_ascii=False).encode("utf-8"), "application/json; charset=utf-8")

    def _read_body(self):
        media = self.headers.get_all("Content-Type", [])
        if len(media) != 1 or self.headers.get_content_type() != "application/json":
            raise RequestError(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, "提交需要 application/json")
        if "Transfer-Encoding" in self.headers or "Content-Encoding" in self.headers:
            raise ValueError("请直接发送 JSON 内容")
        lengths = self.headers.get_all("Content-Length", [])
        if len(lengths) != 1 or not lengths[0].isascii() or not lengths[0].isdigit():
            raise ValueError("请求缺少有效的 Content-Length")
        length = lengths[0].lstrip("0") or "0"
        if len(length) > 6 or int(length) > MAX_BODY_BYTES:
            raise RequestError(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, "提交大小不得超过 128 KiB")
        size = int(length)
        data = self.rfile.read(size)
        if len(data) != size:
            raise ValueError("提交内容不完整")
        return _decode_json(data)

    def _target(self):
        target = urlsplit(self.path)
        if target.scheme or target.netloc or target.fragment:
            raise ValueError("请求路径无效")
        return target

    def _handle(self, action):
        try:
            action()
        except RequestError as exc:
            self._json(exc.status, {"error": str(exc), **exc.details})
        except ValueError as exc:
            self._json(HTTPStatus.BAD_REQUEST, {"error": str(exc)})
        except TimeoutError:
            self._json(HTTPStatus.REQUEST_TIMEOUT, {"error": "读取请求超时"})
        except (BrokenPipeError, ConnectionAbortedError, ConnectionResetError):
            return

    def do_GET(self):
        self._handle(self._get)

    def _get(self):
        self._check_origin()
        target = self._target()
        if target.path == "/":
            try:
                page = self.server.page.read_bytes()
            except OSError as exc:
                raise RequestError(HTTPStatus.SERVICE_UNAVAILABLE, "游戏页面文件无法读取") from exc
            self._send(HTTPStatus.OK, page, "text/html; charset=utf-8")
        elif target.path == "/autorun.js" and not target.query:
            self._send(HTTPStatus.OK, PAGE.with_name("autorun.js").read_bytes(), "text/javascript; charset=utf-8")
        elif target.path == "/api/auto/health" and not target.query:
            server = self.server
            self._json(HTTPStatus.OK, {"service": "research_games", "game": server.game_family,
                                      "pid": os.getpid(), "instance": server.instance})
        elif target.path == "/api/subscription/status" and not target.query:
            from subscription_worker import get_setup_status
            self._subscription_response(get_setup_status)
        elif target.path in ("/api/auto", "/api/auto/ledger"):
            pack, _ = _query(target.query, self.server)
            self._json(HTTPStatus.OK, self.server.jobs.status(pack, ledger=target.path.endswith("/ledger")))
        elif target.path == "/api/neighborhood" and self.server.supports_neighborhood:
            from neighborhood import audit
            pack_id, _ = _query(target.query, self.server)
            record = self.server.store.best(pack_id)
            proof = audit(record)
            proof["checkpoint"] = self.server.store.export(record["hash"])
            self._json(HTTPStatus.OK, proof)
        elif target.path in ("/api/game", "/api/checkpoint", "/api/mission", "/report"):
            pack_id, rounds = _query(target.query, self.server, target.path == "/api/mission")
            best = self.server.store.best(pack_id)
            if target.path == "/api/game":
                self._json(HTTPStatus.OK, self.server.view(best))
            elif target.path == "/api/checkpoint":
                self._json(HTTPStatus.OK, self.server.store.export(best["hash"]))
            elif target.path == "/api/mission":
                view = self.server.view(best)
                if not view["prompt"]:
                    raise ValueError("当前题目已完成")
                prompt = instructions(self.server.origin, pack_id, rounds, view["prompt"])
                self._json(HTTPStatus.OK, {"prompt": prompt})
            else:
                report = self.server.render_report(best, self.server.store.history(pack_id))
                self._send(HTTPStatus.OK, report.encode("utf-8"), "text/html; charset=utf-8")
        else:
            raise RequestError(HTTPStatus.NOT_FOUND, "游戏没有该路径")

    def do_POST(self):
        self._handle(self._post)

    def _subscription_response(self, action, *args):
        try:
            result = action(*args)
        except (ValueError, RuntimeError, OSError) as exc:
            raise ValueError(str(exc)) from None
        self._json(HTTPStatus.OK, result)

    def _post(self):
        self._check_origin(required=True)
        target = self._target()
        if not target.query and target.path in ("/api/subscription/connect", "/api/subscription/confirm"):
            from subscription_worker import start_setup, confirm_setup
            payload = self._read_body()
            if target.path.endswith("/connect") and payload == {}:
                self._subscription_response(start_setup)
            elif target.path.endswith("/confirm") and isinstance(payload, dict) and payload.keys() == {"percent"}:
                self._subscription_response(confirm_setup, payload["percent"])
            else:
                raise ValueError("订阅设置的字段无效。")
            return
        auto = {"/api/auto/start": (self.server.jobs.start, {"pack_id", "mode", "rounds"}),
                "/api/auto/progress": (self.server.jobs.report_progress, {"pack_id", "id", "turn", "checked", "stage"}),
                "/api/auto/turn": (self.server.jobs.turn, {"pack_id", "id"}),
                "/api/auto/claim": (self.server.jobs.claim, {"pack_id", "id"}),
                "/api/auto/submit": (self.server.jobs.submit, {"pack_id", "id", "turn", "candidate"}),
                "/api/auto/stop": (self.server.jobs.stop, {"pack_id", "id"}),
                "/api/auto/evaluate": (self.server.jobs.evaluate, {"pack_id", "solution"})}
        if target.path in auto and not target.query:
            fn, keys = auto[target.path]
            payload = self._read_body()
            if target.path.endswith("/start") and isinstance(payload, dict) and "request_id" in payload:
                keys = keys | {"request_id"}
            if target.path == "/api/auto/claim" and isinstance(payload, dict) and "thread_id" in payload:
                keys = keys | {"thread_id"}
            if not isinstance(payload, dict) or payload.keys() != keys:
                raise ValueError("自动任务字段不匹配。")
            self._json(HTTPStatus.OK, fn(**payload))
            return
        if target.path == "/api/reference/sync" and not target.query and hasattr(self.server, "sync_reference"):
            payload = self._read_body()
            if not isinstance(payload, dict) or set(payload) != {"pack_id"} or payload["pack_id"] not in self.server.packs:
                raise ValueError("请选择当前方格关卡。")
            self._json(HTTPStatus.OK, self.server.sync_reference(payload["pack_id"]))
            return
        if target.path not in ("/api/submit", "/api/mission/start", "/api/mission/play") or target.query:
            raise RequestError(HTTPStatus.NOT_FOUND, "游戏没有该提交路径")
        payload = self._read_body()
        server = self.server
        with self.server.submit_lock:
            if target.path == "/api/submit":
                response = server.judge(server.store, payload)
            elif target.path == "/api/mission/start":
                if (not isinstance(payload, dict) or payload.keys() != {"pack_id", "rounds"}
                        or payload["pack_id"] not in server.packs):
                    raise ValueError("无效的任务参数")
                current = server.view(server.store.best(payload["pack_id"]))["prompt"]
                if not current:
                    raise ValueError("当前题目已完成")
                response = server.missions.start(payload["pack_id"], payload["rounds"])
                response["prompt"] = current
            else:
                if not isinstance(payload, dict) or payload.keys() != {"id", "turn", "candidate", "author"}:
                    raise ValueError("提交须包含任务、轮号、署名与候选")
                response = dict(server.missions.play(
                    payload["id"], payload["turn"], {"author": payload["author"], "candidate": payload["candidate"]},
                    lambda item: server.judge(server.store, item)))
                if response["remaining"]:
                    response["prompt"] = server.view(server.store.best(response["pack_id"]))["prompt"]
        self._json(HTTPStatus.OK, response)


class SoloServer(ThreadingHTTPServer):
    game_family = "relay"
    handler = SoloHandler
    supports_neighborhood = True
    default_pack = "relay-49-frontier"
    packs = PACKS
    page = PAGE
    build_store = staticmethod(Store)
    view = staticmethod(game_view)
    judge = staticmethod(_submit)
    render_report = staticmethod(render)

    def __init__(self, db_path, port=8765):
        super().__init__(("127.0.0.1", port), self.handler)
        try:
            self.store = self.build_store(db_path)
        except (ValueError, OSError):
            self.server_close()
            raise
        self.submit_lock = Lock()
        launched = os.environ.get("GAME_MCP_LAUNCH_INSTANCE", "")
        self.instance = launched if re.fullmatch("[0-9a-f]{32}", launched) else secrets.token_hex(16)
        self.missions = MissionBook()
        self.jobs = GameJobs(self)
        host, actual_port = self.server_address
        authority = f"{host}:{actual_port}"
        self.host_headers = {host, authority} if actual_port == 80 else {authority}
        self.origin = f"http://{host if actual_port == 80 else authority}"


def main():
    parser = argparse.ArgumentParser(description="在本机浏览器运行驿站编组")
    parser.add_argument("--db", default=DEFAULT_DB, help="SQLite 存档路径")
    parser.add_argument("--port", type=int, default=8765, help="本机端口，0 自动选择空闲端口")
    parser.add_argument("--open", action="store_true", help="准备好之后在浏览器打开游戏")
    args = parser.parse_args()
    if not 0 <= args.port <= 65535:
        parser.error("--port 必须在 0 和 65535 之间")
    try:
        with SoloServer(args.db, args.port) as server:
            publish_origin(server, "relay")
            print(server.origin, flush=True)
            if args.open:
                import webbrowser
                webbrowser.open(server.origin)
            server.serve_forever()
    except KeyboardInterrupt:
        return 0
    except (OSError, ValueError) as exc:
        print(f"无法启动本机游戏：{exc}", file=sys.stderr)
        return 2
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
