"""Compact, single-case browser entry point for the native square-packing game."""

from http import HTTPStatus
import json
from pathlib import Path
import re
from statistics import median

from auto_jobs import ACTIVE, _idle, _view
from lite_submission import submission_view, submission_zip
from packing_batches import PackingBatchHandler
from reference_sync import load_reference, refresh_reference
from solo_server import RequestError


LITE_PACK = "square-26"
LITE_CASE = 26
PROFILE = "packing-lite"
ROUNDS = (5, 20, 50, 100, 200, 500)
HERE = Path(__file__).resolve().parent


def fast_prompt(base, job):
    turn = job["completed"] + 1
    return ("方格冲榜 · 快跑第 " + str(turn) + "/" + str(job["rounds"]) + " 轮。"
            "本轮用模型研究不同的邻接与拼块构型。优先比较整体收缩、边界重排、局部转角、替换接触结构四个方向。"
            "可用本机脚本辅助生成。通过 research_games 的 evaluate_candidate 精确复算实际候选，"
            "用 report_progress 上报实际已检查数量和当前策略。只提交这一轮通过复算的最小布局，"
            "手记记录各方向中关键的成功或碰撞。若唯一可验方案是父档，交回父档并如实说明未改善。"
            "立即接手下一轮直到本局结束、收到停止，或平台明确提示用完套餐或续跑需付费。\n" + base)


class PackingLiteHandler(PackingBatchHandler):
    def _lite_state(self, notice=None, sources=None):
        server = self.server
        record = server.store.best(LITE_PACK)
        game = server.view(record)
        sources = sources if sources is not None else load_reference(LITE_CASE, server.data_dir)
        with server.jobs.transaction(False) as db:
            current = server.jobs._latest(db, LITE_PACK)
            rows = db.execute("SELECT body FROM jobs WHERE pack=? AND json_extract(body, '$.profile')=? "
                              "ORDER BY rowid DESC LIMIT 8", (LITE_PACK, PROFILE)).fetchall()
        history = [json.loads(row[0]) for row in rows]
        lite_job = history[0] if history else None
        job = _view(lite_job) if lite_job else _idle(LITE_PACK)
        meter = server.lite_usage
        controller = lite_job.get("controller_thread") if lite_job else None
        usage = (meter.sample(controller, lite_job["started"], lite_job.get("ended")) if controller else None)
        seconds = [r["active_seconds"] for item in history for r in item["records"]
                   if r.get("active_seconds", 0) > 0]
        token_samples = []
        for item in history:
            if item.get("ended") and item["completed"] and item.get("controller_thread"):
                sample = meter.sample(item["controller_thread"], item["started"], item["ended"])
                if sample and sample["total_tokens"] > 0:
                    token_samples.append(sample["total_tokens"] / item["completed"])
        return {
            "score": game["state"], "solution": game["solution"], "reference": game["reference"],
            "submission": submission_view(record, sources), "job": job, "usage": usage,
            "estimate": {"seconds_per_round": median(seconds) if seconds else None,
                         "tokens_per_round": median(token_samples) if token_samples else None},
            "busy": bool(current and current.get("profile") != PROFILE and current["status"] in ACTIVE),
            "trace": lite_job.get("trace", []) if lite_job else [],
            "message": notice,
        }

    def _get(self):
        target = self._target()
        if target.path not in {"/lite", "/lite.js", "/api/lite/state", "/api/lite/submission"}:
            return super()._get()
        self._check_origin()
        if target.query and not (target.path == "/lite" and target.query in {"lang=en", "lang=zh-CN"}):
            raise RequestError(HTTPStatus.NOT_FOUND, "轻量版地址不接受查询参数。")
        if target.path == "/lite":
            self._send(HTTPStatus.OK, (HERE / "lite.html").read_bytes(), "text/html; charset=utf-8")
        elif target.path == "/lite.js":
            self._send(HTTPStatus.OK, (HERE / "lite.js").read_bytes(), "text/javascript; charset=utf-8")
        elif target.path == "/api/lite/state":
            self._json(HTTPStatus.OK, self._lite_state())
        else:
            record = self.server.store.best(LITE_PACK)
            reference = load_reference(LITE_CASE, self.server.data_dir)
            bundle = submission_zip(record, reference)
            self._send(HTTPStatus.OK, bundle, "application/zip")

    def _post(self):
        target = self._target()
        if not target.path.startswith("/api/lite/"):
            return super()._post()
        self._check_origin(required=True)
        if target.query:
            raise RequestError(HTTPStatus.NOT_FOUND, "轻量版地址不接受查询参数。")
        payload = self._read_body()
        paths = {"start": {"rounds", "request_id"}, "extend": {"id", "rounds"}, "stop": {"id"}, "check": {"solution"},
                 "trace": {"id", "turn", "checked", "stage", "summary", "solution"},
                 "save": {"solution", "parent_hash"}, "sync": set()}
        operation = target.path.rsplit("/", 1)[-1]
        if operation not in paths:
            raise RequestError(HTTPStatus.NOT_FOUND, "找不到此轻量版操作。")
        if not isinstance(payload, dict) or payload.keys() != paths[operation]:
            raise ValueError("轻量版请求字段不匹配。")
        server = self.server
        if operation == "check":
            self._json(HTTPStatus.OK, server.jobs.evaluate(LITE_PACK, payload["solution"]))
            return
        if operation == "trace":
            self._json(HTTPStatus.OK, self._publish_trace(payload))
            return
        if operation == "start":
            server.jobs.start(LITE_PACK, payload["rounds"], "mcp", payload["request_id"], profile=PROFILE)
            response = self._lite_state("快跑已排好，等待当前 Codex 对话领取。")
        elif operation == "extend":
            job = server.jobs.extend_profile(LITE_PACK, payload["id"], payload["rounds"])
            response = self._lite_state(f"当前快跑已延长到 {job['rounds']} 轮。")
        elif operation == "stop":
            with server.jobs.transaction(False) as db:
                job = server.jobs._lookup(db, LITE_PACK, payload["id"])
            if job.get("profile") != PROFILE:
                raise ValueError("这局不属于方格冲榜。")
            server.jobs.stop(LITE_PACK, payload["id"])
            response = self._lite_state("已停止，已验收成绩仍可导出。")
        elif operation == "save":
            with server.submit_lock:
                if payload["parent_hash"] != server.store.best_hash(LITE_PACK):
                    raise ValueError("已有更新的成绩，请先载入最新布局。")
                saved = server.judge(server.store, {"author": "本机玩家", "candidate": {
                    "pack_id": LITE_PACK, **payload, "handoff": "在方格冲榜中手动调整布局并提交精确裁判。"}})
            response = self._lite_state(saved["message"])
        else:
            sources = refresh_reference(LITE_CASE, server.data_dir)
            message = " ".join(sources.get("warnings", [])) or "已核对 Squares Project 和 GitHub 来源。"
            response = self._lite_state(message, sources)
        self._json(HTTPStatus.OK, response)

    def _publish_trace(self, payload):
        summary, solution = payload.pop("summary"), payload.pop("solution")
        if not isinstance(summary, str) or not 1 <= len(summary.strip()) <= 300:
            raise ValueError("请提供 1 至 300 字的公开研究说明。")
        event = {"summary": summary.strip()}
        if solution is not None:
            pack = self.server.store.resolve_pack(LITE_PACK)
            pack._validated_poses(solution)
            event["solution"] = solution
            try:
                rated = self.server.jobs.evaluate(LITE_PACK, solution)
                event.update(side=rated["side"], valid=True)
            except ValueError as exc:
                event.update(valid=False, error=str(exc))
                match = re.search(r"第 (\d+) 个方块与第 (\d+) 个方块重叠", str(exc))
                if match:
                    event["collision"] = [int(value) for value in match.groups()]
        return self.server.jobs.report_progress(LITE_PACK, **payload, trace=event)
