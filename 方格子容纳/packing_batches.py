"""Durable local search batches directed by one existing Codex conversation."""

from collections import defaultdict
from http import HTTPStatus
from math import ceil
import json
import multiprocessing
import os
from pathlib import Path
import re
import secrets
from statistics import median
from threading import Event, RLock, Thread
import time
from urllib.parse import parse_qs
from uuid import UUID

from auto_jobs import ACTIVE, ROUND_LIMITS, _disk, _worker_lease
from auto_search import SearchStopped, search_packing_round
from geometry import SCALE, SCORE_LIMIT
from packing_catalog import DEFAULT_BATCH_PACKS, PACK_COUNTS
from packing_usage import CodexUsage
from reference_sync import case_is_open, load_reference
from solo_server import RequestError, SoloHandler


WORK_LIMIT = 2400
MAX_LANES = 3
QUOTA_LIFETIME = 60
STRATEGIES = {"compact": "整体压缩", "boundary": "边界重排", "mixed": "位置与倾角"}
AVAILABLE = {"waiting", "paused", "review"}
TERMINAL = {"done", "stopped", "failed"}
IDENTIFIER = re.compile("[0-9a-f]{32}")
HERE = Path(__file__).resolve().parent


def _side(ticks):
    return f"{ticks // SCALE}.{ticks % SCALE:06}"


def _quota_allows(quota):
    if not isinstance(quota, dict) or quota.get("ordinaryUsageAllowed") is not True:
        return False
    buckets = quota.get("rateLimitsByLimitId")
    bucket = (buckets.get("codex") if isinstance(buckets, dict) else None)
    if bucket is None:
        bucket = quota.get("rateLimits", quota)
    if not isinstance(bucket, dict):
        return False

    def has_time(window):
        return (isinstance(window, dict)
                and type(window.get("usedPercent")) in (int, float)
                and 0 <= window["usedPercent"] < 100
                and type(window.get("windowDurationMins")) is int
                and window["windowDurationMins"] > 0)

    return has_time(bucket.get("primary")) and (bucket.get("secondary") is None or has_time(bucket["secondary"]))


def _worker(channel, pack, parent, turn, strategy, seed, remaining):
    last = 0.0

    def cancelled():
        if channel.poll():
            try:
                channel.recv()
            except EOFError:
                pass
            return True
        return False

    def report(count, stage):
        nonlocal last
        now = time.monotonic()
        if now - last >= 0.16:
            channel.send(("progress", count, stage))
            last = now

    try:
        channel.send(("started", os.getpid(), time.time()))
        solution, note = search_packing_round(pack, parent, turn, strategy, seed, remaining, report, cancelled)
        if not cancelled():
            channel.send(("result", remaining, solution, note, time.time()))
    except (BrokenPipeError, EOFError, SearchStopped):
        pass
    except Exception as exc:
        try:
            channel.send(("error", str(exc)))
        except OSError:
            pass
    finally:
        channel.close()


class PackingBatches:
    def __init__(self, server):
        self.server = server
        self.jobs = server.jobs
        self.usage = CodexUsage()
        self.processes = multiprocessing.get_context("spawn")
        self.lock = RLock()
        self.workers = {}
        self.closing = False
        self.lease = None
        with self.jobs.transaction() as db:
            db.execute("CREATE TABLE IF NOT EXISTS packing_batches "
                       "(id TEXT PRIMARY KEY, request_id TEXT NOT NULL UNIQUE, body TEXT NOT NULL)")
        self._take_ownership(required=False)

    def _take_ownership(self, required=True):
        if self.lease is not None:
            return
        lease = _worker_lease(self.jobs.file.with_suffix(".packing-batch.lock"))
        if not lease.__enter__():
            lease.__exit__(None, None, None)
            if required:
                raise ValueError("同一份存档的并行任务由另一个游戏服务处理。")
            return
        self.lease = lease
        with self.jobs.transaction() as db:
            latest = self._load(db)
            if latest and latest["status"] not in TERMINAL:
                for row in latest["rows"]:
                    if row.get("pending"):
                        self._settle_pending(latest, row, replay=True)
                    if row["status"] in {"running", "queued"}:
                        self._interrupt(row, "paused", "服务已恢复，等待 Codex 继续", account_time=False)
                self._update_status(latest)
                self._save(db, latest)

    @staticmethod
    def _load(db, batch_id=None):
        if batch_id is None:
            record = db.execute("SELECT body FROM packing_batches ORDER BY rowid DESC LIMIT 1").fetchone()
        else:
            if not isinstance(batch_id, str) or not IDENTIFIER.fullmatch(batch_id):
                raise ValueError("批次编号无效。")
            record = db.execute("SELECT body FROM packing_batches WHERE id = ?", (batch_id,)).fetchone()
            if record is None:
                raise ValueError("找不到此并行批次。")
        return json.loads(record[0]) if record else None

    @staticmethod
    def _save(db, batch):
        body = json.dumps(batch, ensure_ascii=False, separators=(",", ":"))
        db.execute("UPDATE packing_batches SET body = ? WHERE id = ?", (body, batch["id"]))

    @staticmethod
    def _row(batch, pack_id):
        for row in batch["rows"]:
            if row["pack_id"] == pack_id:
                return row
        raise ValueError("这一关不在该批次。")

    @staticmethod
    def _update_status(batch):
        statuses = {row["status"] for row in batch["rows"]}
        for status in ("running", "queued", "paused", "review", "waiting", "failed", "stopped", "done"):
            if status in statuses:
                batch["status"] = "running" if status == "queued" else status
                if status in TERMINAL and batch.get("ended") is None:
                    batch["ended"] = time.time()
                break

    def pack_busy(self, db, pack_id):
        batch = self._load(db)
        return bool(batch and any(row["pack_id"] == pack_id and row["status"] not in TERMINAL
                                  for row in batch["rows"]))

    @staticmethod
    def _configuration(packs, rounds, parallel):
        if (not isinstance(packs, list) or not 1 <= len(packs) <= len(PACK_COUNTS)
                or any(not isinstance(p, str) or p not in PACK_COUNTS for p in packs)
                or len(set(packs)) != len(packs)):
            raise ValueError("请选择至少一关且互不重复。")
        if type(rounds) is not int or rounds not in ROUND_LIMITS:
            raise ValueError("并行批次可运行 1、3 或 5 轮。")
        if type(parallel) is not int or not 1 <= parallel <= min(len(packs), MAX_LANES):
            raise ValueError("并行路数须介于一至三，且不超过关卡数。")

    def _history(self, active_id=None):
        with self.jobs.transaction(False) as db:
            rows = db.execute("SELECT body FROM packing_batches ORDER BY rowid DESC LIMIT 12").fetchall()
        history = [json.loads(row[0]) for row in rows]
        return [batch for batch in history if batch["id"] != active_id]

    @staticmethod
    def _speeds(history):
        values = defaultdict(list)
        for batch in history:
            for row in batch["rows"]:
                for receipt in row["receipts"]:
                    count = receipt.get("segment_checked", receipt["checked"])
                    if receipt["seconds"] > 0 and count:
                        values[row["pack_id"]].append(count / receipt["seconds"])
        return {pack: median(samples) for pack, samples in values.items()}

    @staticmethod
    def _scheduled_seconds(durations, parallel):
        if any(seconds is None for seconds in durations):
            return None
        lanes = [0.0] * parallel
        for seconds in sorted(durations, reverse=True):
            index = lanes.index(min(lanes))
            lanes[index] += seconds
        return ceil(max(lanes))

    def _estimate(self, packs, rounds, parallel, history):
        speeds = self._speeds(history)
        seconds = self._scheduled_seconds([rounds * WORK_LIMIT / speeds[p] if p in speeds else None
                                           for p in packs], parallel)
        usage = []
        comparable = [b for b in history if b["status"] == "done" and b["rounds"] == rounds
                      and len(b["rows"]) == len(packs)]
        for batch in comparable[:5]:
            totals = self._usage(batch)
            if totals and totals["total_tokens"] > 0:
                usage.append(totals["total_tokens"])
        return {"seconds": seconds, "token_min": min(usage) if usage else None,
                "token_max": max(usage) if usage else None}

    def estimate(self, packs, rounds, parallel):
        self._configuration(packs, rounds, parallel)
        return self._estimate(packs, rounds, parallel, self._history())

    def start(self, packs, rounds, parallel, request_id):
        self._configuration(packs, rounds, parallel)
        if not isinstance(request_id, str) or not re.fullmatch("[A-Za-z0-9_-]{1,96}", request_id):
            raise ValueError("请求编号无效。")
        _disk(self.jobs.file)
        estimate = self.estimate(packs, rounds, parallel)
        with self.lock:
            if self.closing:
                raise ValueError("游戏正在关闭。")
            self._take_ownership()
            batch_id = self._start(packs, rounds, parallel, request_id, estimate)
        return self.status(batch_id)

    def _start(self, packs, rounds, parallel, request_id, estimate):
        with self.jobs.transaction() as db:
            remembered = db.execute("SELECT body FROM packing_batches WHERE request_id = ?", (request_id,)).fetchone()
            if remembered:
                batch = json.loads(remembered[0])
                if ([row["pack_id"] for row in batch["rows"]], batch["rounds"], batch["parallel"]) != (packs, rounds, parallel):
                    raise ValueError("该请求编号已经用于另一批。")
            else:
                latest = self._load(db)
                if latest and latest["status"] not in TERMINAL:
                    raise ValueError("已有进行中的并行批次。")
                for pack in packs:
                    previous = self.jobs._latest(db, pack)
                    if previous and previous["status"] in ACTIVE:
                        raise ValueError(f"{PACK_COUNTS[pack]} 块已有单关任务，请先结束该局。")
                batch = {"id": secrets.token_hex(16), "status": "waiting", "parallel": parallel,
                         "rounds": rounds, "started": time.time(), "estimate": estimate, "floors": [],
                         "rows": [{"pack_id": pack, "n": PACK_COUNTS[pack], "status": "waiting",
                                   "stage": "等待 Codex 分派策略", "checked": 0, "verified_rounds": 0,
                                   "receipts": [], "search_seconds": 0} for pack in packs]}
                db.execute("INSERT INTO packing_batches VALUES (?, ?, ?)",
                           (batch["id"], request_id, json.dumps(batch, ensure_ascii=False)))
        return batch["id"]

    def plan(self, batch_id, thread_id, plans, quota):
        try:
            if not isinstance(thread_id, str) or str(UUID(thread_id)) != thread_id:
                raise ValueError()
        except (ValueError, AttributeError):
            raise ValueError("请提供当前 Codex 会话的 UUID。") from None
        if (not isinstance(plans, list) or not plans or len(plans) > len(PACK_COUNTS)
                or any(not isinstance(p, dict) or set(p) != {"pack_id", "turn", "strategy"}
                       or type(p["turn"]) is not int or p["turn"] < 1
                       or not isinstance(p["strategy"], str) or p["strategy"] not in STRATEGIES
                       or not isinstance(p["pack_id"], str) or p["pack_id"] not in PACK_COUNTS for p in plans)
                or len({p["pack_id"] for p in plans}) != len(plans)):
            raise ValueError("每个所选关卡须提供当前轮号和一种支持的策略。")
        allowed = _quota_allows(quota)
        if allowed:
            _disk(self.jobs.file)
        with self.lock:
            self._take_ownership()
            with self.jobs.transaction() as db:
                batch = self._load(db, batch_id)
                if batch.get("thread_id") not in (None, thread_id):
                    raise ValueError("这批已由另一条 Codex 对话接手。")
                if self.closing:
                    raise ValueError("游戏正在关闭。")
                if allowed:
                    batch["thread_id"] = thread_id
                    batch["quota_until"] = time.time() + QUOTA_LIFETIME
                    for plan in plans:
                        row = self._row(batch, plan["pack_id"])
                        version = row["verified_rounds"] + 1
                        if plan["turn"] < version:
                            continue
                        if version != plan["turn"] or row["status"] in TERMINAL:
                            raise ValueError("当前轮号已变化，请读取批次状态。")
                        if row["status"] == "running":
                            if row["strategy"] != plan["strategy"]:
                                raise ValueError("本轮正在计算，可在下一轮调整策略。")
                            continue
                        row.update(status="queued", stage="等待本机搜索进程", strategy=plan["strategy"])
                    batch.pop("message", None)
                else:
                    self._pause(batch, "普通套餐额度不可用或无法确认，搜索已暂停。")
                self._update_status(batch)
                self._save(db, batch)
            if allowed:
                self._launch(batch_id)
            else:
                raise ValueError("普通套餐额度不可用或无法确认，搜索已暂停。")
        return self.status(batch_id)

    def _pause(self, batch, reason):
        for row in batch["rows"]:
            if row["status"] not in TERMINAL:
                self._interrupt(row, "paused", reason)
                handle = self.workers.get((batch["id"], row["pack_id"]))
                if handle:
                    handle[0].set()
        batch["message"] = reason

    @staticmethod
    def _interrupt(row, status, reason, *, account_time=True):
        if row["status"] == "running":
            started = row.get("worker_started")
            ended = time.time() if account_time else row.get("last_progress_at")
            if started and ended:
                row["search_seconds"] += max(0, ended - started)
            attempt = {key: row.get(key) for key in ("seed", "strategy", "parent_hash", "checked", "worker_started")}
            row.setdefault("interrupted", []).append(attempt)
        row.update(status=status, stage=reason)

    def stop(self, batch_id, pack_id=None):
        with self.lock:
            self._take_ownership()
            with self.jobs.transaction() as db:
                batch = self._load(db, batch_id)
                rows = batch["rows"] if pack_id is None else [self._row(batch, pack_id)]
                for row in rows:
                    if row["status"] not in TERMINAL:
                        self._interrupt(row, "stopped", "已停止，保留已有成果")
                        handle = self.workers.get((batch_id, row["pack_id"]))
                        if handle:
                            handle[0].set()
                self._update_status(batch)
                self._save(db, batch)
        return self.status(batch_id)

    def _launch(self, batch_id):
        """Claim queued rounds under the scheduler lock before starting workers."""
        if self.closing:
            return
        launches = []
        with self.jobs.transaction() as db:
            batch = self._load(db, batch_id)
            queued = [row for row in batch["rows"] if row["status"] == "queued"
                      and (batch_id, row["pack_id"]) not in self.workers]
            if queued:
                try:
                    _disk(self.jobs.file)
                    if time.time() >= batch["quota_until"]:
                        raise ValueError("上次普通套餐检查已过期，请由 Codex 重新确认。")
                except (ValueError, OSError) as exc:
                    self._pause(batch, str(exc))
                    queued = []
            available = max(0, batch["parallel"] - len(self.workers))
            for row in queued:
                if row["status"] != "queued":
                    continue
                if row.get("pending"):
                    self._settle_pending(batch, row, replay=True)
                    continue
                if len(launches) >= available:
                    continue
                pack = row["pack_id"]
                parent = self.server.store.best(pack)
                row.update(status="running", stage="正在启动本机搜索", seed=secrets.randbits(48),
                           parent_hash=parent["hash"], launched=time.time(), pid=None, worker_started=None,
                           segment_offset=row["checked"], last_progress_at=None)
                key = (batch_id, pack)
                cancel = Event()
                thread = Thread(target=self._drive,
                                args=(key, parent, row["verified_rounds"] + 1, row["strategy"], row["seed"],
                                      row["checked"], cancel), daemon=True)
                launches.append((key, cancel, thread))
            self._update_status(batch)
            self._save(db, batch)
        for key, cancel, thread in launches:
            self.workers[key] = (cancel, thread)
            thread.start()

    def _advance(self, key, seed, **fields):
        with self.lock, self.jobs.transaction() as db:
            batch = self._load(db, key[0])
            row = self._row(batch, key[1])
            if row["status"] == "running" and row.get("seed") == seed:
                row.update(fields)
                self._save(db, batch)

    def _drive(self, key, parent, turn, strategy, seed, previous, cancel):
        local, child = self.processes.Pipe(duplex=True)
        process = self.processes.Process(target=_worker, args=(child, key[1], parent, turn, strategy, seed,
                                                               WORK_LIMIT - previous), daemon=True)
        result = None
        error = "本机搜索进程意外退出，请重新尝试该关。"
        next_disk_check = time.monotonic() + 20
        try:
            process.start()
            child.close()
            while not cancel.is_set():
                if time.monotonic() >= next_disk_check:
                    self._check_running_disk(key[0])
                    next_disk_check = time.monotonic() + 20
                if not local.poll(0.16):
                    if not process.is_alive():
                        break
                    continue
                message, *values = local.recv()
                if message == "started":
                    self._advance(key, seed, pid=values[0], worker_started=values[1], stage="正在检查候选")
                elif message == "progress":
                    self._advance(key, seed, checked=previous + values[0], stage=values[1], last_progress_at=time.time())
                elif message == "result":
                    result = (previous + values[0], values[1], values[2], values[3])
                    break
                elif message == "error":
                    error = values[0]
                    break
        except EOFError:
            pass
        except (OSError, RuntimeError) as exc:
            error = str(exc)
        finally:
            child.close()
            if process.pid is not None:
                if process.is_alive() and cancel.is_set():
                    try:
                        local.send("stop")
                    except OSError:
                        pass
                process.join(1.5)
                if process.is_alive():
                    process.terminate()
                    process.join(1.5)
                process.close()
            local.close()
            self._finish(key, seed, result, error)

    def _check_running_disk(self, batch_id):
        try:
            _disk(self.jobs.file)
        except (ValueError, OSError) as exc:
            with self.lock, self.jobs.transaction() as db:
                batch = self._load(db, batch_id)
                self._pause(batch, str(exc))
                self._update_status(batch)
                self._save(db, batch)

    def _reference(self, pack):
        return load_reference(PACK_COUNTS[pack], self.server.data_dir)

    def _accept(self, parent, solution, handoff=None, *, replay=False):
        pack = parent["state"]["pack_id"]
        rated = self.server.store.resolve_pack(pack).evaluate(solution)
        with self.server.submit_lock:
            current = self.server.store.best(pack)
            before = SCORE_LIMIT - current["state"]["score"]
            after = rated["side_ticks"]
            if after >= before:
                if not replay or not handoff:
                    return None
                # Recover a committed score if the service exited before recording
                # the same result in the separate batch ledger.
                saved = next((item for item in self.server.store.history(pack)
                              if item["handoff"] == handoff and item["solution"] == rated["solution"]), None)
                if saved is None:
                    return None
                previous = self.server.store.get(saved["state"]["parent_hash"])
                before = SCORE_LIMIT - previous["state"]["score"]
            else:
                saved = self.server.store.submit(
                    pack, current["hash"], "Codex · 并行本机搜索", rated["solution"],
                    handoff or "并行批次产生的更紧布局。精确裁判已验证全部方块与包围边长。")
        sources = self._reference(pack)
        upper = sources.get("upper_bound") or {}
        threshold = upper.get("comparison_ticks")
        floor = {"pack_id": pack, "n": PACK_COUNTS[pack], "before_side": _side(before),
                 "after_side": _side(after), "saved_ticks": before - after,
                 "public_candidate": type(threshold) is int and after < threshold, "at": time.time()}
        if floor["public_candidate"]:
            witness = sources.get("witness") or {}
            floor["evidence"] = {"hash": saved["hash"], "side_ticks": after, "exact_side": rated["exact_side"],
                                 "upper": upper, "github_url": witness.get("source_url"),
                                 "github_blob_sha": witness.get("blob_sha")}
        return floor

    def _finish(self, key, seed, result, error):
        with self.lock:
            self.workers.pop(key, None)
            settling = False
            with self.jobs.transaction() as db:
                batch = self._load(db, key[0])
                row = self._row(batch, key[1])
                if row["status"] == "running" and row.get("seed") == seed:
                    if result is None:
                        row.update(status="failed", stage="运行失败", note=error)
                    else:
                        row.update(pending=result, checked=result[0], stage="正在验收本轮布局")
                        settling = True
                    self._update_status(batch)
                    self._save(db, batch)
            if settling:
                with self.jobs.transaction() as db:
                    batch = self._load(db, key[0])
                    self._settle_pending(batch, self._row(batch, key[1]))
                    self._update_status(batch)
                    self._save(db, batch)
            with self.jobs.transaction(False) as db:
                newest_id = self._load(db)["id"]
            self._launch(newest_id)

    def _settle_pending(self, batch, row, *, replay=False):
        try:
            _disk(self.jobs.file)
        except (OSError, ValueError) as exc:
            self._pause(batch, str(exc))
            return
        count, solution, note, finished = row.pop("pending")
        turn, seed = row["verified_rounds"] + 1, row["seed"]
        try:
            parent = self.server.store.get(row["parent_hash"])
            handoff = f"并行批次 {batch['id']} 第 {turn} 轮，种子 {seed}。精确裁判已验证布局。"
            floor = self._accept(parent, solution, handoff, replay=replay)
        except (OSError, ValueError) as exc:
            row.update(status="failed", stage="无法保存本轮验收", note=str(exc))
            return
        started = row.get("worker_started") or row["launched"]
        seconds = max(0.001, finished - started)
        receipt = {"turn": turn, "pid": row["pid"], "started": started, "ended": finished,
                   "seconds": seconds, "checked": count, "segment_checked": count - row.get("segment_offset", 0),
                   "seed": seed, "strategy": row["strategy"],
                   "parent_hash": parent["hash"], "note": note, "improved": floor is not None}
        row["receipts"].append(receipt)
        if floor:
            floor["turn"] = turn
            batch["floors"].append(floor)
            note = f"新纪录 {floor['before_side']} → {floor['after_side']}，已加一层。"
        done = turn == batch["rounds"]
        row.update(verified_rounds=turn, checked=0, search_seconds=row["search_seconds"] + seconds,
                   status="done" if done else "review", note=note,
                   stage="已验收" if done else "等待 Codex 分派下一轮")

    def _usage(self, batch):
        thread = batch.get("thread_id")
        return self.usage.sample(thread, batch["started"], batch.get("ended")) if thread else None

    def _row_view(self, batch, row, speeds, now):
        pack = row["pack_id"]
        store = self.server.store
        record_hash = store.best_hash(pack)
        record = store.get(record_hash) if record_hash else store.best(pack)
        best_ticks = SCORE_LIMIT - record["state"]["score"]
        upper = self._reference(pack).get("upper_bound") or {}
        threshold = upper.get("comparison_ticks")
        elapsed = max(0, now - (row.get("worker_started") or row.get("launched") or now))
        active = row["status"] == "running"
        segment_checked = row["checked"] - row.get("segment_offset", 0)
        live_speed = segment_checked / elapsed if active and elapsed > 0 and segment_checked > 0 else None
        throughput = live_speed or speeds.get(pack)
        checked = row["checked"] + sum(r["checked"] for r in row["receipts"])
        left = None
        if throughput and row["status"] not in TERMINAL:
            left = max(0, WORK_LIMIT - row["checked"]) / throughput
        proof = any(floor["pack_id"] == pack and floor["public_candidate"] for floor in batch["floors"])
        completed = row["verified_rounds"]
        verified_rate = speeds.get(pack) if row["receipts"] else None
        return {**{key: row.get(key) for key in
                    ("pack_id", "n", "status", "stage", "checked", "strategy", "seed", "note", "receipts")},
                "turn": min(completed + 1, batch["rounds"]), "rounds": batch["rounds"], "completed_rounds": completed,
                "work_limit": WORK_LIMIT, "total_checked": checked, "rate": live_speed or verified_rate,
                "best_side": _side(best_ticks), "best_hash": record["hash"], "upper": upper.get("value_text"),
                "gap_ticks": best_ticks - threshold if type(threshold) is int else None,
                "elapsed_seconds": row["search_seconds"] + (elapsed if active else 0),
                "remaining_seconds": ceil(left) if left is not None else None,
                "progress_pct": self._percent(checked, batch["rounds"] * WORK_LIMIT, completed == batch["rounds"]),
                "improved": any(r["improved"] for r in row["receipts"]),
                "proof_url": f"/api/packing/batch/proof?batch_id={batch['id']}&pack_id={pack}" if proof else None}

    @staticmethod
    def _percent(checked, planned, finished):
        return 100 if finished else min(99.9, round(100 * checked / planned, 1))

    def status(self, batch_id=None):
        with self.jobs.transaction(False) as db:
            batch = self._load(db, batch_id)
        packs = [{"id": p, "n": n, "open": case_is_open(n, self.server.data_dir)} for p, n in PACK_COUNTS.items()]
        open_packs = [p["id"] for p in packs if p["open"]]
        defaults = [p for p in DEFAULT_BATCH_PACKS if p in open_packs]
        defaults += [p for p in open_packs if p not in defaults][:MAX_LANES-len(defaults)]
        answer = {"batch": None, "packs": packs, "default_packs": defaults,
                  "strategies": [{"id": key, "label": label} for key, label in STRATEGIES.items()]}
        if batch is None:
            return answer
        now = time.time()
        history = self._history(batch["id"])
        speeds = self._speeds([*history, batch])
        rows = [self._row_view(batch, row, speeds, now) for row in batch["rows"]]
        total = sum(row["total_checked"] for row in rows)
        completed = sum(row["completed_rounds"] for row in rows)
        planned = len(rows) * batch["rounds"]
        durations = []
        for row in rows:
            if row["status"] not in TERMINAL:
                speed = row["rate"] or speeds.get(row["pack_id"])
                left = WORK_LIMIT * (row["rounds"] - row["completed_rounds"]) - row["checked"]
                durations.append(left / speed if speed else None)
        floors = [{k: v for k, v in floor.items() if k != "evidence"} for floor in batch["floors"]]
        answer["batch"] = {**{k: batch.get(k) for k in ("id", "status", "parallel", "rounds", "estimate", "message")},
                           "rows": rows, "usage": self._usage(batch), "eta_seconds":
                               self._scheduled_seconds(durations, batch["parallel"]) if durations else 0,
                           "elapsed_seconds": max(0, (batch.get("ended") or now) - batch["started"]),
                           "floors": floors, "saved_ticks": sum(floor["saved_ticks"] for floor in floors),
                           "improved_count": len(floors), "total_checked": total, "completed_rounds": completed,
                           "planned_rounds": planned, "progress_pct":
                               self._percent(total, planned * WORK_LIMIT, completed == planned)}
        return answer

    def proof(self, batch_id, pack_id):
        with self.jobs.transaction(False) as db:
            batch = self._load(db, batch_id)
        floor = next((item for item in reversed(batch["floors"])
                      if item["pack_id"] == pack_id and item.get("evidence")), None)
        if floor is None:
            raise ValueError("这关还没有达到公开纪录候选。")
        proof = floor["evidence"]
        return {"batch_id": batch_id, "result": floor, "checkpoint": self.server.store.export(proof["hash"])}

    def close(self):
        with self.lock:
            self.closing = True
            if self.workers:
                with self.jobs.transaction() as db:
                    batch = self._load(db)
                    self._pause(batch, "游戏服务已关闭，保留已验收成绩")
                    self._update_status(batch)
                    self._save(db, batch)
            threads = [thread for _, thread in self.workers.values()]
        for thread in threads:
            thread.join(4)
        if self.lease is not None:
            self.lease.__exit__(None, None, None)
            self.lease = None


class PackingBatchHandler(SoloHandler):
    """Packing-only extensions to the existing loopback UI and JSON server."""

    def _get(self):
        target = self._target()
        if target.path not in ("/batch.js", "/api/packing/batch", "/api/packing/batch/proof",
                               "/api/packing/batch/estimate"):
            return super()._get()
        self._check_origin()
        if target.path == "/batch.js" and not target.query:
            self._send(HTTPStatus.OK, (HERE / "batch.js").read_bytes(), "text/javascript; charset=utf-8")
            return
        query = parse_qs(target.query, keep_blank_values=True)
        if target.path.endswith("/estimate"):
            if (set(query) != {"pack", "rounds", "parallel"} or len(query["rounds"]) != 1
                    or len(query["parallel"]) != 1):
                raise ValueError("请选择关卡、轮数和并行路数。")
            result = self.server.batches.estimate(query["pack"], int(query["rounds"][0]), int(query["parallel"][0]))
        elif target.path.endswith("/proof"):
            if set(query) != {"batch_id", "pack_id"} or any(len(v) != 1 for v in query.values()):
                raise ValueError("请选择批次与方格关卡。")
            result = self.server.batches.proof(query["batch_id"][0], query["pack_id"][0])
        else:
            if set(query) - {"batch_id"} or any(len(v) != 1 for v in query.values()):
                raise ValueError("批次查询参数无效。")
            result = self.server.batches.status(query.get("batch_id", [None])[0])
        self._json(HTTPStatus.OK, result)

    def _post(self):
        target = self._target()
        if not target.path.startswith("/api/packing/batch/"):
            return super()._post()
        self._check_origin(required=True)
        handlers = {
            "start": (self.server.batches.start, {"packs", "rounds", "parallel", "request_id"}),
            "plan": (self.server.batches.plan, {"batch_id", "thread_id", "plans", "quota"}),
            "stop": (self.server.batches.stop, {"batch_id", "pack_id"}),
        }
        operation = target.path.rsplit("/", 1)[-1]
        if target.query or operation not in handlers:
            raise RequestError(HTTPStatus.NOT_FOUND, "找不到此并行任务操作。")
        handler, keys = handlers[operation]
        payload = self._read_body()
        if not isinstance(payload, dict) or set(payload) != keys:
            raise ValueError("并行任务字段不匹配。")
        self._json(HTTPStatus.OK, handler(**payload))
