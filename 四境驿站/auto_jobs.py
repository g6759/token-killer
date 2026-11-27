"""Durable game turns shared by the browser, MCP clients and the local solver."""
from contextlib import closing, contextmanager
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import shutil
import sqlite3
import tempfile
import sys
from threading import Thread
import time
from uuid import UUID

from mission import ROUND_LIMITS


ACTIVE = {"running", "stopping"}
MCP_CLIENTS = {"codex-mcp": "Codex", "claude-code": "Claude Code", "antigravity": "Antigravity"}
MCP_ROUNDS = (1, 3, 5, 10, 25, 100)
MAX_SAFE_INTEGER = 2**53 - 1
ROOT = Path(__file__).resolve().parents[1]


def _encode(data):
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False)


def _view(job):
    result = {k: job[k] for k in (
        "id", "pack_id", "mode", "rounds", "status", "stage", "message", "completed")}
    end = job.get("ended") or time.time()
    result["elapsed_seconds"] = round(max(0, end - job["started"]), 2)
    if job.get("client"):
        result.update(client=job["client"], target_tokens=job.get("target_tokens"), end_reason=job.get("end_reason"))
        metered = [(r["at"], r["usage_receipt"]["uncached_input_tokens"] + r["usage_receipt"]["output_tokens"])
                   for r in job["records"] if r.get("usage_receipt")]
        seconds = max(0.001, min(60, end - job["started"]))
        result.update(metered_rounds=len(metered), effective_tokens=sum(n for _, n in metered) if metered else None,
                      recent_tokens_per_minute=(sum(n for at, n in metered if at > end - 60) * 60 / seconds
                                                if metered else None))
    if job.get("profile"):
        result["profile"] = job["profile"]
    if job.get("work") and job["status"] == "running":
        result["work"] = job["work"]
    active_since = job.get("claimed_at") if job["mode"] == "mcp" else job.get("turn_started")
    result["turn_active_seconds"] = (
        round(max(0, end - active_since), 3) if job["status"] == "running" and active_since is not None else None
    )
    result["receipts"] = []
    for record in job["records"]:
        receipt = {k: record[k] for k in ("turn", "accepted", "message", "seconds")}
        if "active_seconds" in record:
            receipt["active_seconds"] = record["active_seconds"]
        if "work" in record:
            receipt["work"] = record["work"]
        usage = (record.get("model_response") or {}).get("usage") or {}
        if type(usage.get("output_tokens")) is int and usage["output_tokens"] >= 0:
            receipt["output_tokens"] = usage["output_tokens"]
        result["receipts"].append(receipt)
    return result


def _idle(pack):
    return {"id": None, "pack_id": pack, "mode": None, "rounds": 0, "status": "idle",
            "stage": "", "message": "", "completed": 0, "elapsed_seconds": 0, "receipts": []}


def _disk(path):
    locations = [path.parent, ROOT, Path(tempfile.gettempdir())]
    if os.name == "nt":
        locations.append(Path(os.environ["SystemRoot"]))
    if any(shutil.disk_usage(item).free < 10 * 1024**3 for item in locations):
        raise ValueError("可用磁盘空间低于 10 GiB，本局已暂停。")


@contextmanager
def _worker_lease(path):
    with path.open("a+b") as stream:
        if stream.tell() == 0:
            stream.write(b"0")
            stream.flush()
        stream.seek(0)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(stream.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            yield False
        else:
            yield True


class GameJobs:
    def __init__(self, server):
        self.server = server
        self.file = Path(server.store.db_path).resolve().with_suffix(".jobs.sqlite3")
        with self.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS jobs "
                       "(id TEXT PRIMARY KEY, pack TEXT NOT NULL, request_id TEXT UNIQUE, body TEXT NOT NULL)")
            db.execute("CREATE TABLE IF NOT EXISTS start_requests "
                       "(request_id TEXT PRIMARY KEY, job_id TEXT NOT NULL)")
            latest = db.execute("SELECT body FROM jobs WHERE rowid IN "
                                "(SELECT MAX(rowid) FROM jobs GROUP BY pack)").fetchall()
        for (body,) in latest:
            job = json.loads(body)
            if job["mode"] == "local" and job["status"] == "running":
                self._launch_local(job)
            elif job["mode"] == "codex" and job["status"] == "running":
                with _worker_lease(self.file.with_suffix(f".{job['id']}.lock")) as acquired:
                    if acquired:
                        self._finish_worker(job["pack_id"], job["id"], "blocked",
                                            "游戏服务已重启。本局已暂停，保留已有回执，以免重复发送模型请求。")

    @contextmanager
    def transaction(self, write=True):
        with closing(sqlite3.connect(self.file, timeout=8)) as db, db:
            if write:
                db.execute("BEGIN IMMEDIATE")
            yield db

    def _pack(self, pack):
        if not isinstance(pack, str) or pack not in self.server.packs:
            raise ValueError("请选择此游戏支持的题包。")
        return pack

    @staticmethod
    def _latest(db, pack):
        row = db.execute("SELECT body FROM jobs WHERE pack=? ORDER BY rowid DESC LIMIT 1", (pack,)).fetchone()
        return json.loads(row[0]) if row else None

    def _lookup(self, db, pack, key):
        self._pack(pack)
        if not isinstance(key, str) or not re.fullmatch("[0-9a-f]{32}", key):
            raise ValueError("无效的局号。")
        row = db.execute("SELECT body FROM jobs WHERE id=? AND pack=?", (key, pack)).fetchone()
        if not row:
            raise ValueError("找不到此题包的这局游戏。")
        return json.loads(row[0])

    @staticmethod
    def _save(db, job):
        db.execute("UPDATE jobs SET body=? WHERE id=?", (_encode(job), job["id"]))

    def status(self, pack, *, ledger=False):
        with self.transaction(False) as db:
            job = self._latest(db, self._pack(pack))
            samples = None if ledger else self._estimate_samples(db, pack)
        if ledger and job:
            usage = [r.get("model_response") for r in job["records"]] if job["mode"] == "codex" else None
            return {**_view(job), "started": job["started"], "ended": job.get("ended"),
                    "records": job["records"], "provider_usage": usage or None}
        result = _view(job) if job else _idle(pack)
        result["best_hash"] = self.server.store.best_hash(pack)
        if samples:
            result["estimate_samples"] = samples
        return result

    @staticmethod
    def _estimate_samples(db, pack):
        # Select scalar telemetry, avoiding candidate payloads in poll responses.
        rows = db.execute("""
            WITH recent AS (SELECT body FROM jobs WHERE pack=? ORDER BY rowid DESC LIMIT 60)
            SELECT json_extract(body, '$.mode'),
                   json_extract(r.value, '$.seconds'), json_extract(r.value, '$.active_seconds'),
                   json_extract(r.value, '$.model_response.usage.total_tokens'),
                   json_extract(r.value, '$.model_response.usage.input_tokens'),
                   json_extract(r.value, '$.model_response.usage.output_tokens')
              FROM recent, json_each(body, '$.records') AS r
             ORDER BY json_extract(r.value, '$.at') DESC
        """, (pack,))
        samples = {}
        for mode, elapsed, active, total, inputs, outputs in rows:
            seconds = active if mode == "mcp" else elapsed
            if total is None and type(inputs) is int and type(outputs) is int and min(inputs, outputs) >= 0:
                total = inputs + outputs
            sample = {}
            if type(seconds) in (float, int) and math.isfinite(seconds) and seconds >= 0:
                sample["seconds"] = seconds
            if type(total) is int and total > 0:
                sample["tokens"] = total
            if sample and len(samples.get(mode, [])) < 20:
                samples.setdefault(mode, []).append(sample)
        return samples

    def _next(self, job):
        job.pop("claimed_at", None)
        job.pop("work", None)
        best = self.server.store.best(job["pack_id"])
        prompt = (self.server.profile_prompt(best, job)
                  if job.get("profile") else self.server.view(best)["prompt"])
        job.update(parent=best["hash"], prompt=prompt, turn_started=time.time())
        if not prompt or job["completed"] == job["rounds"]:
            job.update(status="completed", stage="已结束", message="本局已完成。", ended=time.time())
        else:
            job["stage"] = {"mcp": "等待 ChatGPT 领取", "codex": "正在启动订阅执行器",
                            "local": "本机搜索中"}[job["mode"]]
            if job["completed"] and job["mode"] == "mcp":
                job["stage"] = "等待 ChatGPT 提交下一轮"
            if job.get("client"):
                job["stage"] = "等待 " + MCP_CLIENTS[job["client"]] + " 接手当前轮"

    def start(self, pack_id, rounds, mode, request_id=None, *, profile=None, client=None, target_tokens=None):
        pack = self._pack(pack_id)
        allowed_rounds = ROUND_LIMITS
        if client is not None:
            if not isinstance(client, str) or client not in MCP_CLIENTS or mode != "mcp" or profile is not None:
                raise ValueError("未知 MCP 客户端。 / Unknown MCP client.")
            allowed_rounds = MCP_ROUNDS
        if target_tokens is not None and (not client or type(target_tokens) is not int or not 1 <= target_tokens <= MAX_SAFE_INTEGER):
            raise ValueError("有效 token 目标须为正整数。 / Enter a positive whole token target.")
        if profile is not None:
            setting = getattr(self.server, "job_profiles", {}).get(profile)
            if setting is None or pack != setting[0] or mode != "mcp":
                raise ValueError("这套研究规则不适用于当前关卡。")
            allowed_rounds = setting[1]
        if type(rounds) is not int or rounds not in allowed_rounds or mode not in ("mcp", "local", "codex"):
            if profile:
                raise ValueError("请选择此速跑支持的轮数。")
            raise ValueError("请选择有效的执行方式与轮数（1、3 或 5 轮）。")
        if request_id is not None and (not isinstance(request_id, str) or not re.fullmatch(r"[\w-]{1,96}", request_id, re.ASCII)):
            raise ValueError("请求编号无效。")
        _disk(self.file)
        with self.transaction() as db:
            if request_id is not None:
                row = db.execute("SELECT body FROM jobs WHERE request_id=? OR id="
                                 "(SELECT job_id FROM start_requests WHERE request_id=?)",
                                 (request_id, request_id)).fetchone()
                if row:
                    old = json.loads(row[0])
                    initial = old.get("initial_rounds", old["rounds"])
                    if (old["pack_id"], old["mode"], initial, old.get("profile"), old.get("client"), old.get("target_tokens")) != (pack, mode, rounds, profile, client, target_tokens):
                        raise ValueError("该请求编号已经用于另一局。")
                    return _view(old)
            old = self._latest(db, pack)
            if old and old["status"] in ACTIVE:
                if (old["rounds"], old["mode"], old.get("profile"), old.get("client"), old.get("target_tokens")) == (rounds, mode, profile, client, target_tokens):
                    if request_id is not None:
                        db.execute("INSERT INTO start_requests VALUES (?,?)", (request_id, old["id"]))
                    return _view(old)
                raise ValueError("这个题包已有进行中的任务，请先结束该局。")
            batches = getattr(self.server, "batches", None)
            if batches and batches.pack_busy(db, pack):
                raise ValueError("这关正在并行批次中，请在并行面板继续。")
            job = {"id": secrets.token_hex(16), "pack_id": pack, "mode": mode, "rounds": rounds,
                   "started": time.time(), "status": "running", "stage": "", "completed": 0,
                   "message": "", "records": []}
            if profile is not None:
                job["profile"] = profile
            if client is not None:
                job.update(client=client, target_tokens=target_tokens)
            self._next(job)
            db.execute("INSERT INTO jobs VALUES (?,?,?,?)", (job["id"], pack, request_id, _encode(job)))
        result = _view(job)
        if mode == "local" and job["status"] == "running":
            self._launch_local(job)
        elif mode == "codex" and job["status"] == "running":
            Thread(target=self._subscription, args=(pack, job["id"]), daemon=True).start()
        return result

    @staticmethod
    def _task(job):
        active = job["status"] == "running"
        return {"job": _view(job), "turn": job["completed"] + 1 if active else None,
                "prompt": job["prompt"] if active else None,
                "previous_receipt": _view(job)["receipts"][-1] if job["records"] else None}

    def turn(self, pack_id, id):
        with self.transaction(False) as db:
            job = self._lookup(db, pack_id, id)
        if job["mode"] != "mcp":
            raise ValueError("这局由网页的后台执行器处理。")
        return self._task(job)

    def claim(self, pack_id, id, *, worker=False, thread_id=None):
        with self.transaction() as db:
            job = self._lookup(db, pack_id, id)
            if job["mode"] != ("codex" if worker else "mcp"):
                raise ValueError("这局属于其他执行方式。")
            changed = False
            if job.get("profile") and thread_id is not None and job["status"] == "running":
                try:
                    if not isinstance(thread_id, str) or str(UUID(thread_id)) != thread_id:
                        raise ValueError()
                except (AttributeError, ValueError):
                    raise ValueError("Codex 对话编号必须是有效的 UUID。") from None
                if job.get("controller_thread") not in (None, thread_id):
                    raise ValueError("这局已由另一条 Codex 对话领取。")
                job["controller_thread"] = thread_id
                changed = True
            if job["status"] == "running" and "claimed_at" not in job:
                job["claimed_at"] = time.time()
                job["stage"] = "正在检查游戏订阅授权" if worker else "ChatGPT 已接手，等待交卷"
                changed = True
            if changed:
                self._save(db, job)
        return self._task(job)

    def report_progress(self, pack_id, id, turn, checked, stage, *, trace=None):
        if type(turn) is not int or type(checked) is not int or not 0 <= checked < 2**53:
            raise ValueError("请提供当前轮号与累计检查的候选数。")
        if not isinstance(stage, str) or not 1 <= len(stage.strip()) <= 120:
            raise ValueError("阶段说明须为 1 至 120 个字符。")
        with self.transaction() as db:
            job = self._lookup(db, pack_id, id)
            if (job["mode"] != "mcp" or job["status"] != "running"
                    or job["completed"] + 1 != turn or "claimed_at" not in job):
                raise ValueError("只能给已接手的当前轮次更新进展。")
            if checked < job.get("work", {}).get("checked", 0):
                raise ValueError("本轮已检查的候选数不能减少。")
            job["work"] = {"checked": checked, "stage": stage.strip()}
            if trace is not None:
                if not job.get("profile"):
                    raise ValueError("本局不支持试摆预览。")
                event = {**trace, **job["work"], "turn": turn, "at": time.time()}
                job["trace"] = [*job.get("trace", [])[-15:], event]
            self._save(db, job)
        return _view(job)

    def evaluate(self, pack_id, solution):
        rules = self.server.store.resolve_pack(self._pack(pack_id))
        rated = rules.evaluate(solution)
        digest = hashlib.sha256(_encode(rated["solution"]).encode()).hexdigest()
        result = {"pack_id": pack_id, "solution_hash": digest, "score": rated["score"],
                  "total": rated["total"]}
        if "side_ticks" in rated:
            ticks = rated["side_ticks"]
            result.update(side_ticks=ticks, side=f"{ticks / 1_000_000:.6f}",
                          area=f"{ticks*ticks // 10**12}.{ticks*ticks % 10**12:012d}")
        else:
            result["missing"] = rated["missing"]
        return result

    def submit(self, pack_id, id, turn, candidate, *, local=False, model_response=None, usage_receipt=None):
        if type(turn) is not int or not isinstance(candidate, dict):
            raise ValueError("提交需要正确的轮号和候选对象。")
        _disk(self.file)
        with self.transaction() as db:
            job = self._lookup(db, pack_id, id)
            mode = "codex" if model_response is not None else ("local" if local else "mcp")
            if job["mode"] != mode:
                raise ValueError("提交入口与本局模式不符。")
            if usage_receipt is not None:
                if (not job.get("client") or not isinstance(usage_receipt, dict)
                        or set(usage_receipt) != {"response_id", "uncached_input_tokens", "output_tokens"}
                        or not isinstance(usage_receipt["response_id"], str)
                        or not 1 <= len(usage_receipt["response_id"].strip()) <= 256
                        or any(type(usage_receipt[k]) is not int or not 0 <= usage_receipt[k] <= MAX_SAFE_INTEGER
                               for k in ("uncached_input_tokens", "output_tokens"))):
                    raise ValueError("客户端回执字段无效。 / Invalid client usage receipt.")
            if 1 <= turn <= job["completed"]:
                previous = job["records"][turn - 1]
                if previous["candidate"] != candidate or previous.get("usage_receipt") != usage_receipt:
                    raise ValueError("这轮已经收到另一份方案。")
                return self._task(job)
            if job["status"] != "running" or turn != job["completed"] + 1:
                raise ValueError("游戏已停止或轮号不正确。")
            if usage_receipt and any((r.get("usage_receipt") or {}).get("response_id") == usage_receipt["response_id"]
                                     for r in job["records"]):
                raise ValueError("用量回执 ID 重复。 / Duplicate usage receipt ID.")
            wrong_parent = candidate.get("pack_id") != pack_id or candidate.get("parent_hash") != job["parent"]
            if wrong_parent and mode != "codex":
                raise ValueError("请使用本轮题面中的题号和父档。")
            author = {"mcp": MCP_CLIENTS.get(job.get("client"), "ChatGPT · MCP"), "local": "本机自动搜索", "codex": "ChatGPT 订阅自动运行"}[mode]
            with self.server.submit_lock:
                try:
                    if wrong_parent:
                        raise ValueError("模型回复的格式、题号或父档不符合本轮要求。")
                    answer = self.server.judge(self.server.store, {"candidate": candidate, "author": author})
                    message, accepted = answer["message"], True
                except ValueError as exc:
                    message, accepted = str(exc), False
                job["records"].append({"turn": turn, "candidate": candidate, "accepted": accepted,
                        "seconds": round(time.time() - job["turn_started"], 3), "message": message,
                        "at": time.time(), "parent": job["parent"],
                        "best_hash": self.server.store.best(pack_id)["hash"]})
                if mode == "mcp" and job.get("claimed_at") is not None:
                    job["records"][-1]["active_seconds"] = round(max(0, time.time() - job["claimed_at"]), 3)
                    if "work" in job:
                        job["records"][-1]["work"] = job["work"]
                if model_response is not None:
                    job["records"][-1]["model_response"] = model_response
                if usage_receipt:
                    job["records"][-1]["usage_receipt"] = usage_receipt
                job.update(completed=turn, message=message)
                self._next(job)
                target = job.get("target_tokens")
                if target and usage_receipt is None:
                    job.update(status="stopped", end_reason="unmetered", stage="缺少用量回执", ended=time.time())
                elif target and _view(job)["effective_tokens"] >= target:
                    job.update(status="completed", end_reason="target", stage="已达目标", ended=time.time())
            self._save(db, job)
        return self._task(job)

    def stop(self, pack_id, id):
        with self.transaction() as db:
            job = self._lookup(db, pack_id, id)
            if job["status"] in ACTIVE:
                job.update(status="stopped", stage="已停止", ended=time.time(), message="已停止接收新回合。")
                self._save(db, job)
            return _view(job)

    def extend_profile(self, pack_id, id, rounds):
        """Raise a running profile's target while retaining its claimed round and receipts."""
        _disk(self.file)
        with self.transaction() as db:
            job = self._lookup(db, pack_id, id)
            setting = getattr(self.server, "job_profiles", {}).get(job.get("profile"))
            if (setting is None or pack_id != setting[0] or job["status"] != "running"
                    or type(rounds) is not int or rounds not in setting[1] or rounds < job["rounds"]):
                raise ValueError("请选择正在运行的速跑和一个更大的轮数。")
            if rounds > job["rounds"]:
                job.setdefault("initial_rounds", job["rounds"])
                job["rounds"] = rounds
                parent = self.server.store.get(job["parent"])
                job["prompt"] = self.server.profile_prompt(parent, job)
                self._save(db, job)
        return _view(job)

    def _launch_local(self, job):
        Thread(target=self._local, args=(job["pack_id"], job["id"]), daemon=True).start()

    def _finish_worker(self, pack, key, status, reason):
        with self.transaction() as db:
            job = self._lookup(db, pack, key)
            if job["status"] == "running":
                job.update(status=status, stage="已暂停" if status == "blocked" else "运行失败",
                           ended=time.time(), message=reason)
                self._save(db, job)

    def _subscription(self, pack, key):
        with _worker_lease(self.file.with_suffix(f".{key}.lock")) as acquired:
            if not acquired:
                return
            try:
                import asyncio
                if str(ROOT) not in sys.path:
                    sys.path.insert(0, str(ROOT))
                from 游戏MCP.autoplay import play
                from subscription_worker import SubscriptionDriver, PreflightError
                factory = getattr(self.server, "subscription_factory", SubscriptionDriver)
                try:
                    asyncio.run(play(self, pack, key, factory()))
                except PreflightError as exc:
                    self._finish_worker(pack, key, "blocked", str(exc))
            except Exception as exc:
                self._finish_worker(pack, key, "failed", str(exc)[:450] or "后台执行器中断。")

    def worker_stage(self, pack, key, turn, stage):
        with self.transaction() as db:
            job = self._lookup(db, pack, key)
            if job["status"] != "running" or job["completed"] + 1 != turn:
                raise ValueError("本局已停止接收该轮。")
            job["stage"] = stage
            self._save(db, job)

    def _local(self, pack, key):
        # Per job rather than per pack: a new job can start as a stopped worker exits.
        with _worker_lease(self.file.with_suffix(f".{key}.lock")) as acquired:
            if acquired:
                self._search(pack, key)

    def _search(self, pack, key):
        next_check, cancelled = 0.0, False
        def stopped(force=False):
            nonlocal next_check, cancelled
            now = time.monotonic()
            if force or now >= next_check:
                with self.transaction(False) as db:
                    cancelled = self._lookup(db, pack, key)["status"] != "running"
                next_check = now + 0.05
            return cancelled
        try:
            from auto_search import next_candidate
            while not stopped(True):
                _disk(self.file)
                with self.transaction(False) as db:
                    job = self._lookup(db, pack, key)
                turn = job["completed"] + 1
                parent = self.server.store.get(job["parent"])
                candidate = next_candidate(pack, parent, turn, stopped)
                if not stopped(True):
                    self.submit(pack, key, turn, candidate, local=True)
        except Exception as exc:
            with self.transaction() as db:
                job = self._lookup(db, pack, key)
                if job["status"] == "running":
                    job.update(status="failed", stage="已停止", message=str(exc), ended=time.time())
                    self._save(db, job)


def publish_origin(server, game):
    directory = Path(os.environ.get("GAME_MCP_REGISTRY", ROOT / "游戏MCP" / "运行中"))
    directory.mkdir(parents=True, exist_ok=True)
    temp = directory / f"{game}-{os.getpid()}.tmp"
    temp.write_text(_encode({"origin": server.origin, "pid": os.getpid(), "instance": server.instance}), encoding="utf-8")
    temp.replace(directory / f"{game}.json")
