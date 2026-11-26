"""Compare subscription game strategies over equal wall-clock windows."""

import argparse
from base64 import b64encode
from collections import Counter
from concurrent.futures import ProcessPoolExecutor, ThreadPoolExecutor
from contextlib import ExitStack, closing
from hashlib import sha256
import gzip
import json
import os
from pathlib import Path
import sqlite3
import sys
from tempfile import TemporaryDirectory
from threading import Event, Lock
from time import monotonic, time

from evidence import _candidate_from_task, rehearsal_candidate
import guided_search
import relay_frontier
import square_frontier
import weekly_meter
from workspace_io import ROOT, check_disk, exclusive, now, read, save
from 本地裁判 import LocalJudge, SearchStopped, get_square_pack
import research_games


SCENARIOS = {
    "single": ("square-26",),
    "same-game": ("square-26", "square-26"),
    "mixed": ("square-26", "relay-49-frontier"),
}
CONCURRENCY_SCENARIOS = {"mixed": SCENARIOS["mixed"], "mixed-four": SCENARIOS["mixed"] * 2}
HIGH_CONCURRENCY_SCENARIOS = {"mixed-four": CONCURRENCY_SCENARIOS["mixed-four"],
                              "mixed-eight": SCENARIOS["mixed"] * 4}
PORTFOLIO_SQUARES = ("square-26", "square-37", "square-41", "square-50")
PORTFOLIO_LANES = {
    lanes: tuple(pack for square in PORTFOLIO_SQUARES for pack in (square, "relay-49-frontier") * copies)
    for lanes, copies in ((16, 2), (24, 3))
}
RELAY_LANES = {lanes: ("relay-49-frontier",) * lanes for lanes in (16, 20, 22, 23, 24)}
DUAL_RELAY_MODE = "relay-dual-target"
CONCURRENCY_COMPARISONS = {
    "concurrency": CONCURRENCY_SCENARIOS,
    "high-concurrency": HIGH_CONCURRENCY_SCENARIOS,
    "sixteen-lane-comparison": {"mixed-eight": HIGH_CONCURRENCY_SCENARIOS["mixed-eight"],
                                "mixed-sixteen": SCENARIOS["mixed"] * 8},
}
PLANS = {"all": SCENARIOS, **CONCURRENCY_COMPARISONS,
         "portfolio-comparison": {"mixed-sixteen": CONCURRENCY_COMPARISONS["sixteen-lane-comparison"]["mixed-sixteen"],
                                  "mixed-portfolio": PORTFOLIO_LANES[16]},
         "portfolio-scale-comparison": {"mixed-portfolio": PORTFOLIO_LANES[16],
                                        "mixed-portfolio-twenty-four": PORTFOLIO_LANES[24]},
         "relay-mix-comparison": {"relay-sixteen": RELAY_LANES[16], "mixed-portfolio": PORTFOLIO_LANES[16]},
         "relay-scale-comparison": {"relay-twenty-four": RELAY_LANES[24], "relay-sixteen": RELAY_LANES[16]},
         "relay-twenty-lane-comparison": {"relay-twenty": RELAY_LANES[20], "relay-sixteen": RELAY_LANES[16]},
         "relay-twenty-two-lane-comparison": {"relay-twenty-two": RELAY_LANES[22], "relay-twenty": RELAY_LANES[20]},
         "relay-twenty-three-lane-comparison": {"relay-twenty-three": RELAY_LANES[23], "relay-twenty-two": RELAY_LANES[22]},
         "relay-target-comparison": {DUAL_RELAY_MODE: RELAY_LANES[22], "relay-twenty-two": RELAY_LANES[22]}}
WINDOWS = {name: packs for cases in PLANS.values() for name, packs in cases.items()}
RAMP_40_TO_80 = "relay-frontier-1152-ramp-40-to-80"
FRONTIER_WINDOWS = {"relay-frontier-pilot": (4, 4), "relay-frontier-22": (22, 32),
                    "relay-frontier-wide": (22, 96), "relay-frontier-192": (22, 192),
                    "relay-frontier-384-probe": (1, 384), "relay-frontier-384": (22, 384),
                    "relay-frontier-1152-probe": (1, 1152), "relay-frontier-1152-eight": (8, 1152),
                    "relay-frontier-1152": (22, 1152), "relay-frontier-1152-40": (40, 1152),
                    RAMP_40_TO_80: (80, 1152), "relay-frontier-1152-112": (112, 1152)}
WINDOWS.update({name: ("relay-49-frontier",) * lanes for name, (lanes, _) in FRONTIER_WINDOWS.items()})
WINDOWS.update({pack: (pack,) for pack in research_games.PACKS})
for square_mode, lanes, branches in (("square-frontier-1152-probe", 1, 1152),
                                      ("square-frontier-1152-40", 40, 1152),
                                      ("square-frontier-1728-40", 40, 1728)):
    FRONTIER_WINDOWS[square_mode] = (lanes, branches)
    WINDOWS[square_mode] = (square_frontier.PACK,) * lanes
PLANS["square-relay-frontier-comparison"] = {
    name: WINDOWS[name] for name in ("square-frontier-1152-40", "relay-frontier-1152-40")}
MAX_LANES = max(map(len, WINDOWS.values()))
LIVE_WINDOW_SECONDS = 600
USAGE_FIELDS = ("input_tokens", "cached_input_tokens", "output_tokens")
SQUARE_DIRECTIONS = (
    "改变方格的局部接触关系并压紧整体",
    "先试另一处方格边界或交界，再复用可行结构",
    "优先试转动相互贴近的少数方格，并关注转动是否创造收紧空间",
    "先对称地收紧两侧方格，再检查角落接触是否允许第二次压缩",
    "先试方格的横向排布变化，检查横向收缩时能否保持竖向边界",
    "试着协调角落方格与相邻斜方格的位移，减少边界间隙",
    "改变一组贴近的方格的倾角，检验外围方格能否顺势内移",
)
RELAY_DIRECTIONS = (
    "选择不同的未覆盖站对，并检查补齐会影响哪些已覆盖站对",
    "优先尝试让缺口较多的站进入新名单，同时保持原有覆盖",
    "从与上一轮不同的未覆盖站对出发，检查多次换组能否同时保住其他配对",
    "优先尝试能同时处理两个缺口的站，核对名单更换前后的唯一覆盖",
    "优先挪动有较多重复覆盖的站，保留只出现一次的配对",
    "先补难以与其他缺口共用名单的站对，再核对其他配对是否保留",
    "选择少尝试的缺口站对，优先保住恰好被覆盖一次的配对",
)
RESEARCH_DIRECTIONS = (
    "提出一个与当前方案不同、能由本机裁判复算的构造，并指出它针对的性质。",
    "尝试改善一个可核验的组合量或数值指标，保留可复现的表示。",
    "检查当前方案的结构，提出一处小而明确的改动并说明预期效果。",
)


class LaneInterrupted(RuntimeError):
    """This lane has an unconfirmed request and cannot continue in this window."""


class LaneConnectionError(RuntimeError):
    """An unconfirmed transport failure requires a cutoff check."""


def metrics(records, seconds, *, live):
    """Normalize aggregate counters by one shared window, including idle time."""
    candidates = {(r["pack"], r["key"]) for r in records if r.get("key")}
    totals = {key: 0 for key in (*USAGE_FIELDS, "total_tokens")}
    measured = 0
    for record in records:
        usage = record.get("usage")
        if not isinstance(usage, dict):
            continue
        if any(type(usage.get(key)) is not int or usage[key] < 0 for key in USAGE_FIELDS):
            continue
        if usage["cached_input_tokens"] > usage["input_tokens"]:
            continue
        measured += 1
        for key in USAGE_FIELDS:
            totals[key] += usage[key]
        total = usage.get("total_tokens")
        totals["total_tokens"] += (total if type(total) is int and total >= 0
                                   else usage["input_tokens"] + usage["output_tokens"])
    uncached = totals["input_tokens"] - totals["cached_input_tokens"]
    return {
        **totals,
        "uncached_input_tokens": uncached,
        "tokens_per_minute": round((uncached + totals["output_tokens"]) * 60 / seconds, 2),
        "output_per_minute": round(totals["output_tokens"] * 60 / seconds, 2),
        "rounds": len(records),
        "model_rounds": len(records) if live else 0,
        "metered_rounds": measured,
        "unique_candidates": len(candidates),
        "unique_candidates_by_pack": dict(sorted(Counter(pack for pack, _ in candidates).items())),
        "project_records": sum(record.get("project_improved", False) for record in records),
        "rankable": live and measured == len(records) and bool(candidates),
    }


def select_winner(results):
    """Rank complete measured scenarios that used the same conditions."""
    if not any(results.keys() == cases.keys() for cases in PLANS.values()):
        return None
    first = next(iter(results.values()))
    if len(first["models"]) != 1 or len(first["reasoning_efforts"]) != 1:
        return None
    baselines = {}
    eligible = []
    for name, result in results.items():
        if (not result["live"] or result["stop"] != "window" or not result.get("comparable")
                or result["metered_rounds"] != result["rounds"]
                or any(result[key] != first[key] for key in ("window_seconds", "models", "reasoning_efforts"))):
            return None
        for pack, fingerprint in result["initial_hashes"].items():
            if baselines.setdefault(pack, fingerprint) != fingerprint:
                return None
        if result["rankable"]:
            eligible.append(name)
    return max(eligible, key=lambda name: results[name]["tokens_per_minute"], default=None)


def snapshot_baselines(workspace, packs):
    """Export the same verified starting checkpoints for every comparison."""
    with TemporaryDirectory(prefix="benchmark-baselines-") as scratch:
        target = Path(scratch)
        check_disk(target)
        for folder, filename in (("方格子容纳", "packing.sqlite3"), ("四境驿站", "relay.sqlite3"),
                                (research_games.DATABASE.parent, research_games.DATABASE.name)):
            source = (workspace / folder / filename).resolve()
            if source.is_file():
                with closing(sqlite3.connect(source.as_uri() + "?mode=ro", uri=True)) as original:
                    with closing(sqlite3.connect(target / filename)) as copy:
                        original.backup(copy)
        baselines = {}
        for pack in sorted(set(packs)):
            judge = LocalJudge(target, pack)
            baselines[pack] = judge.store.export(judge.metric()["hash"])
        return baselines


def _verify(judge, response):
    checked = judge.inspect_candidate(response)
    receipt = judge.grade(response, "Codex 玩法对照")
    return {"valid": True, **checked, "message": receipt["message"], "receipt": receipt}


def _overlap_seconds(records, *, minimum=2):
    windows = []
    for record in records:
        sent = record.get("provider_started")
        end = record["received"]
        if isinstance(sent, (float, int)) and 0 <= sent < end:
            windows.extend(((sent, 1), (end, -1)))
    active = 0
    previous = 0
    covered = 0
    for at, delta in sorted(windows):
        if active >= minimum:
            covered += at - previous
        active += delta
        previous = at
    return round(covered, 3)


def _run_lane(lane, judge, seed, direction, infer, start, seconds, live, max_turns, abort, publish, relay_targets,
              frontier_count, builders, deck=None, counter=None):
    records = []
    feedback = ""
    error = None
    disk_check = start
    lane_seed = lane.name
    if frontier_count > 192:
        suite = lane.parent.parent
        earlier = sorted((suite / "attempts").glob(lane.parent.name + "-[0-9]*"))
        run_identity = suite.name
        if earlier:
            run_identity += "/after-" + earlier[-1].name
            deck = None
        lane_seed = f"{run_identity}/{lane.name}"
    if live and lane.parent.name in (RAMP_40_TO_80, "relay-frontier-1152-112"):
        lane_number = int(lane.name.removeprefix("lane-"))
        delay = (max(0, lane_number - 40) * 0.75 if lane.parent.name == RAMP_40_TO_80
                 else (lane_number - 1) * 0.35)
        if abort.wait(max(0.0, start + min(delay, seconds) - monotonic())) or monotonic() >= start + seconds:
            return records, error
    while not abort.is_set() and (max_turns is None or len(records) < max_turns):
        if monotonic() >= disk_check:
            check_disk(lane)
            disk_check = monotonic() + 45
        task = judge.task()
        if not task:
            break
        candidate = _candidate_from_task(task)
        prompt = task + "\n本轮方向：" + direction + "。只返回完整候选 JSON，手记写实际改动。"
        if feedback:
            prompt += "\n上一轮精确裁判回执：" + feedback
        if live and judge.pack not in research_games.PACKS:
            prompt += "\n" + guided_search.instructions(judge.pack, relay_targets=relay_targets)
        turn = len(records) + 1
        frontier = None
        if live and frontier_count:
            parent = judge.store.best(judge.pack)
            if abort.is_set() or monotonic() >= start + seconds:
                break
            build_start = monotonic()
            try:
                loaded = None
                if deck is not None:
                    from prepared_frontiers import load_if_matching
                    loaded = load_if_matching(deck, lane.parent.parent, lane.parent.name,
                                              lane.name, turn, parent, count=frontier_count, pack=judge.pack)
                if loaded:
                    prompt, frontier = loaded
                else:
                    build = square_frontier.build if judge.pack == square_frontier.PACK else relay_frontier.build
                    future = builders.submit(build, judge.pack, parent, lane_seed, turn, frontier_count)
                    prompt, frontier = future.result()
            except (OSError, RuntimeError, ValueError) as exc:
                error = f"本地分支题面生成失败，当前轮未发送。{exc}"
                save(lane / "interruption.json", {"turn": turn, "message": error,
                     "at": now(), "elapsed": monotonic() - start})
                abort.set()
                break
            if abort.is_set():
                break
        started_at = monotonic()
        if started_at >= start + seconds:
            break
        request = {"pack": judge.pack, "turn": turn, "lane": lane.name, "candidate": candidate, "prompt": prompt}
        recorded_request = request
        if frontier is not None:
            request["frontier"] = frontier
            recorded_request = {key: value for key, value in request.items() if key != "prompt"}
            recorded_request["builder_lane"] = lane_seed
            recorded_request["prepared_locally_in_advance"] = loaded is not None
            recorded_request["build_wait_seconds"] = round(started_at - build_start, 3)
            prompt_bytes = prompt.encode()
            recorded_request["prompt_sha256"] = sha256(prompt_bytes).hexdigest()
            recorded_request["prompt_gzip_base64"] = b64encode(
                gzip.compress(prompt_bytes, compresslevel=1, mtime=0)
            ).decode("ascii")
        if relay_targets == 2:
            request["relay_targets"] = relay_targets
        pending = lane / f"pending-{turn:04d}.json"
        journal = {"request": recorded_request, "state": "prepared", "at": now()}

        def mark_sent():
            journal.update(state="possibly-sent", at=now())
            save(pending, journal)

        try:
            if live:
                save(pending, journal)
                elapsed_at_dispatch = monotonic() - start
                if elapsed_at_dispatch >= seconds:
                    pending.unlink()
                    save(lane / "deadline.json", {"turn": turn, "at": now(), "elapsed": elapsed_at_dispatch})
                    break
                reply = infer(request, seconds - elapsed_at_dispatch, abort, on_sent=mark_sent)
            else:
                reply = infer(request, start + seconds - started_at, abort)
        except (OSError, RuntimeError, ValueError) as exc:
            failed_at = monotonic() - start
            if abort.is_set():
                break
            if isinstance(exc, (TimeoutError, LaneInterrupted, LaneConnectionError)) and failed_at >= seconds:
                save(lane / "deadline.json", {"turn": turn, "at": now(), "elapsed": failed_at})
                break
            error = str(exc)
            save(lane / "interruption.json", {"turn": turn, "message": error, "at": now(), "elapsed": failed_at})
            retired_lane = isinstance(exc, LaneInterrupted) or (
                frontier_count > 192 and isinstance(exc, LaneConnectionError))
            if not (live and retired_lane):
                abort.set()
            break
        received_at = monotonic()
        if counter is not None and received_at <= start + seconds:
            counter.add(reply)
        if live:
            journal.update(state="received", reply=reply, received_elapsed=received_at - start, at=now())
            save(pending, journal)
        found = None
        search_ended = received_at
        try:
            if frontier is not None and judge.pack == square_frontier.PACK:
                found = square_frontier.resolve(judge.pack, parent, frontier, reply.get("text"))
                next_candidate = found["candidate"]
                search_ended = monotonic()
            elif frontier is not None:
                found = relay_frontier.resolve(judge.pack, parent, frontier, reply.get("text"),
                                               **({"presented_prompt": prompt} if frontier_count > 192 else {}))
                next_candidate = found["candidate"]
                search_ended = monotonic()
            elif reply.get("guided"):
                stop_search = min(start + seconds, received_at + 4)
                try:
                    found = guided_search.resolve(judge, request, reply.get("text"),
                                                  lambda: abort.is_set() or monotonic() >= stop_search)
                    next_candidate = found["candidate"]
                finally:
                    search_ended = monotonic()
            else:
                next_candidate = json.loads(reply.get("text"))
            reviewed = _verify(judge, next_candidate)
        except (TypeError, ValueError, SearchStopped) as exc:
            reviewed = {"valid": False, "message": str(exc), "key": None, "receipt": None}
        verified_at = monotonic()
        if live and not reply.get("model"):
            error = "回执缺少实际模型标识"
            abort.set()
        detail = (found or {}).get("outcome")
        feedback = " ".join(value for value in (detail, reviewed["message"]) if value)
        receipt = reviewed.get("receipt") or {}
        improved = False
        in_window = received_at <= start + seconds
        verified_in_window = verified_at <= start + seconds
        if live and verified_in_window and not abort.is_set() and publish and receipt.get("new_result"):
            try:
                journal.update(state="verified", checkpoint=receipt["checkpoint"], at=now())
                save(pending, journal)
                improved = publish(judge.pack, receipt["checkpoint"])
            except (OSError, ValueError, sqlite3.Error):
                error = "新纪录的复核回执已保留，写入项目存档失败，本次实跑已停止。"
                abort.set()
        provider_started = reply.get("provider_started")
        trial = {key: value for key, value in found.items() if key != "candidate"} if found is not None else None
        record = {
            "pack": judge.pack, "initial_hash": seed, "turn": turn,
            "request": recorded_request, "response": reply.get("text"), "verification": reviewed,
            "key": reviewed["key"] if verified_in_window else None,
            "usage": reply.get("usage"), "model": reply.get("model"),
            "reasoning_effort": reply.get("reasoning_effort"),
            "observed_reasoning_effort": reply.get("observed_reasoning_effort"),
            "thread_id": reply.get("thread_id"), "started": started_at - start,
            "response_id": reply.get("response_id"),
            "received": received_at - start, "in_window": in_window, "project_improved": improved,
            "provider_started": provider_started - start if provider_started is not None else None,
            "local_search_seconds": round(search_ended - received_at, 3),
            "verify_seconds": round(verified_at - search_ended, 3), "search": trial,
            "candidate": next_candidate if found is not None else None,
        }
        if reply.get("rate_limit_headers"):
            record["rate_limit_headers"] = reply["rate_limit_headers"]
        save(lane / f"{turn:04d}.json", record)
        if frontier is not None:
            # The exact prompt is archived above. In-memory statistics only use the receipt.
            recorded_request.pop("prompt_gzip_base64", None)
        if live:
            pending.unlink()
        records.append(record)
    return records, error


def run_window(directory, packs, baselines, infer, *, seconds=LIVE_WINDOW_SECONDS, live, max_turns=None, abort=None,
               publish=None, relay_targets=1, frontier_count=0, deck=None, sampled_usage=None):
    if seconds <= 0 or not 1 <= len(packs) <= MAX_LANES or (live and max_turns is not None):
        raise ValueError(f"对照须配置正时长及 1 至 {MAX_LANES} 条游戏会话。")
    directory.mkdir(parents=True, exist_ok=False)
    lanes = []
    previous_lanes = Counter()
    for index, pack in enumerate(packs, start=1):
        lane = directory / f"lane-{index}"
        judge = LocalJudge(lane, pack)
        baseline = baselines[pack]
        judge.store.ingest(baseline)
        actual = judge.metric()["hash"]
        if actual != baseline["tip"]:
            raise ValueError(f"{pack} 起始存档与对照配置不一致。")
        directions = (SQUARE_DIRECTIONS if pack.startswith("square-") else
                      RESEARCH_DIRECTIONS if pack in research_games.PACKS else RELAY_DIRECTIONS)
        direction = directions[previous_lanes[pack] % len(directions)]
        previous_lanes[pack] += 1
        lanes.append((lane, judge, actual, direction))
    save(directory / "inputs.json", {pack: baselines[pack] for pack in sorted(set(packs))})
    save(directory / "window.json", {"started_at": time(), "seconds": seconds, "packs": list(packs), "live": live})
    start = monotonic()
    abort = abort if abort is not None else Event()
    with ExitStack() as stack:
        counter = None
        if sampled_usage is not None:
            snapshot, emit = sampled_usage
            counter = weekly_meter.WindowCounter()
            stack.enter_context(weekly_meter.observe_window(start, seconds, snapshot, emit, counter))
        builders = None
        if live and frontier_count:
            workers = min(len(lanes), max(1, (os.cpu_count() or 2) - 2), 6)
            builders = stack.enter_context(ProcessPoolExecutor(max_workers=workers))
        pool = stack.enter_context(ThreadPoolExecutor(max_workers=len(lanes)))
        futures = [pool.submit(_run_lane, *lane, infer, start, seconds, live, max_turns, abort, publish, relay_targets,
                               frontier_count, builders, deck, counter)
                   for lane in lanes]
        outcomes = [future.result() for future in futures]
    errors = [error for _, error in outcomes if error]
    all_lanes_interrupted = len(errors) == len(lanes)
    while live and not abort.is_set() and not all_lanes_interrupted and monotonic() < start + seconds:
        check_disk(directory)
        abort.wait(min(45, max(0, start + seconds - monotonic())))
    elapsed = monotonic() - start
    records = [record for turns, _ in outcomes for record in turns if record["in_window"]]
    stop = "window" if elapsed >= seconds else "idle"
    if errors and (abort.is_set() or all_lanes_interrupted):
        stop = "error"
    elif abort.is_set():
        stop = "stopped"
    elif max_turns is not None:
        stop = "round-limit"
    models = sorted({r["model"] for r in records if r["model"]})
    efforts = sorted({r["reasoning_effort"] for r in records if r["reasoning_effort"]})
    result = {
        **metrics(records, seconds if live else min(seconds, max(elapsed, 0.001)), live=live),
        "packs": list(packs), "models": models, "reasoning_efforts": efforts,
        "initial_hashes": {pack: baselines[pack]["tip"] for pack in set(packs)},
        "wall_seconds": round(elapsed, 3),
        "window_seconds": seconds, "stop": stop, "errors": errors, "live": live,
        "model_overlap_seconds": _overlap_seconds(records, minimum=max(2, len(packs))),
    }
    result["comparable"] = (live and stop == "window" and not errors and result["metered_rounds"] == len(records)
                            and len(models) == len(efforts) == 1
                            and all(record["model"] and record["reasoning_effort"] for record in records)
                            and (len(packs) == 1 or result["model_overlap_seconds"] > 0)
                            and len({r["response_id"] for r in records if r["response_id"]}) == len(records))
    result["rankable"] = result["rankable"] and result["comparable"]
    if relay_targets == 2:
        result["relay_targets"] = relay_targets
    save(directory / "summary.json", result)
    return result


def _rehearse(request, seconds, abort):
    # The offline transport exercises the same verification and writes no token estimates.
    candidate = rehearsal_candidate(request["pack"], request["turn"] + 2, request["prompt"])
    return {"text": json.dumps(candidate, ensure_ascii=False), "model": None, "usage": None}


def _complete(result, live):
    return result is not None and result["stop"] == ("window" if live else "round-limit")


def _reusable(result, live):
    return _complete(result, live) and (not live or result.get("comparable", not result.get("errors")))


def _saved_window(directory):
    path = directory / "summary.json"
    return read(path) if path.is_file() else None


def _save_suite_report(directory, results, scenarios, live, abort):
    incomplete = any(not _reusable(results.get(name), live) for name in scenarios)
    report = {"scenarios": results, "winner": None if incomplete else select_winner(results),
              "live": live, "incomplete": incomplete, "stopped": bool(abort and abort.is_set())}
    save(directory / "summary.json", report)
    return report


def _archive_attempt(directory, name):
    attempt_root = directory / "attempts"
    source = directory / name
    if source.resolve() != source or attempt_root.resolve() != attempt_root:
        raise ValueError("对照存档目录不在当前批次内。")
    attempt_root.mkdir(exist_ok=True)
    index = 1
    while (archived := attempt_root / f"{name}-{index:03d}").exists():
        index += 1
    source.rename(archived)


def _run_suite(workspace, directory, scenarios, infer, *, seconds, live, max_turns=None,
               abort=None, publish=None, resume=False, profile=None, weekly_snapshot=None, deck=None):
    directory = directory.resolve()
    if any(name not in WINDOWS for name in scenarios):
        raise ValueError("对照场景不在支持列表内。")
    plan = {"version": 1, "scenarios": list(scenarios), "packs": {n: list(p) for n, p in scenarios.items()},
            "seconds": seconds, "live": live, "max_turns": max_turns, "profile": profile}
    with exclusive(directory):
        manifest_path = directory / "suite.json"
        if resume:
            if not manifest_path.is_file():
                raise ValueError("找不到可继续的对照配置。")
            manifest = read(manifest_path)
            if manifest["plan"] != plan:
                raise ValueError("原始方案、模型或授权账号与当前对照不一致。")
            baseline = manifest["baselines"]
        else:
            if any(path.name != ".lock" for path in directory.iterdir()):
                raise ValueError("此目录已用于对照，请继续原任务或选择新目录。")
            baseline = snapshot_baselines(workspace, (pack for packs in scenarios.values() for pack in packs))
            save(manifest_path, {"plan": plan, "baselines": baseline, "created_at": now()})
        results = {}
        for name, packs in scenarios.items():
            current = directory / name
            previous = _saved_window(current)
            if _reusable(previous, live):
                initial = {pack: baseline[pack]["tip"] for pack in set(packs)}
                if (previous["initial_hashes"] != initial or previous["window_seconds"] != seconds
                        or previous["packs"] != list(packs) or previous["live"] != live
                        or (live and previous["models"] and previous["models"] != [profile["model"]])):
                    raise ValueError(f"{name} 的已完成数据与原始对照配置不符。")
                results[name] = previous
                continue
            if abort is not None and abort.is_set():
                break
            if current.exists():
                _archive_attempt(directory, name)
            sampled_usage = None
            if weekly_snapshot:
                before = weekly_snapshot()
                quota_file = directory / f"{name}-周限额-{time():.3f}.json"
                quota = {"game_window": name, "before": before, "samples": []}
                quota_lock = Lock()
                finished_sampling = Event()
                save(quota_file, quota)
                if seconds == LIVE_WINDOW_SECONDS:
                    def emit(sample, *, lock=quota_lock, finished=finished_sampling, baseline_reading=before,
                             log=quota, destination=quota_file):
                        with lock:
                            if not finished.is_set():
                                sample["since_start"] = weekly_meter.compare_snapshots(
                                    baseline_reading, sample["account_snapshot"])
                                log["samples"].append(sample)
                                log["effective_game_tokens"] = sample["receipt_counter"]["effective_game_tokens"]
                                save(destination, log)
                    sampled_usage = weekly_snapshot, emit
            try:
                result = run_window(current, packs, baseline, infer, seconds=seconds,
                                    live=live, max_turns=max_turns, abort=abort, publish=publish,
                                    relay_targets=2 if name == DUAL_RELAY_MODE else 1,
                                    frontier_count=FRONTIER_WINDOWS.get(name, (0, 0))[1], deck=deck,
                                    sampled_usage=sampled_usage)
            finally:
                if weekly_snapshot:
                    with quota_lock:
                        finished_sampling.set()
                    after = weekly_snapshot()
                    quota.update(after=after, **weekly_meter.compare_snapshots(before, after))
                    timing_path = current / "window.json"
                    if timing_path.is_file():
                        timing = read(timing_path)
                        quota.update(game_window_started_at=timing["started_at"],
                                     game_window_seconds=timing["seconds"])
                    save(quota_file, quota)
            if weekly_snapshot:
                quota["effective_game_tokens"] = result["uncached_input_tokens"] + result["output_tokens"]
                quota["game_worker_wall_seconds"] = result["wall_seconds"]
                save(quota_file, quota)
            results[name] = result
            _save_suite_report(directory, results, scenarios, live, abort)
            if not _complete(result, live):
                break
        return _save_suite_report(directory, results, scenarios, live, abort)


def resumable_run(results_directory):
    pending = []
    for directory in results_directory.glob("吞吐对照-实跑-*"):
        path = directory / "suite.json"
        if not path.is_file():
            continue
        try:
            manifest = read(path)
            plan = manifest["plan"]
            if plan["live"] and any(not _reusable(_saved_window(directory / name), True)
                                     for name in plan["scenarios"] if name in WINDOWS):
                pending.append((manifest.get("created_at", ""), directory))
        except (OSError, ValueError, KeyError, TypeError):
            continue
    return max(pending, default=("", None))[1]


def replay_suite(workspace, directory, scenarios=SCENARIOS):
    return _run_suite(workspace, directory, scenarios, _rehearse, seconds=10, live=False, max_turns=3)


def current_progress(directory, *, receipts_cache=None):
    if directory is None:
        return None
    floors = 0
    for name, packs in WINDOWS.items():
        current = directory / name
        if (current / "summary.json").is_file():
            floors += read(current / "summary.json")["project_records"]
            continue
        if not (current / "window.json").is_file():
            continue
        window = read(current / "window.json")
        elapsed = min(window["seconds"], max(0.001, time() - window["started_at"]))
        lanes, all_records = [], []
        for index, pack in enumerate(packs, start=1):
            receipts = sorted((current / f"lane-{index}").glob("[0-9]*.json"))
            records = []
            for path in receipts:
                key = (window["started_at"], path)
                row = receipts_cache.get(key) if receipts_cache is not None else None
                if row is None:
                    row = read(path)
                    if receipts_cache is not None:
                        receipts_cache[key] = row
                if row["in_window"]:
                    records.append(row)
            all_records.extend(records)
            last = records[-1] if records else {}
            interruption_path = current / f"lane-{index}" / "interruption.json"
            interruption = read(interruption_path).get("message") if interruption_path.is_file() else None
            trial = last.get("search") or {}
            selected = last.get("candidate")
            polygons = (get_square_pack(pack).drawing(selected["solution"])
                        if pack.startswith("square-") and selected and last["verification"]["valid"] else None)
            lanes.append({"pack": pack, "turns": len(records),
                          "latest": last.get("verification", {}).get("message"),
                          "interruption": interruption,
                          "strategy": trial.get("strategy"), "hypothesis": trial.get("hypothesis"),
                          "checked": trial.get("checked"),
                          "local_seconds": last.get("local_search_seconds"), "verify_seconds": last.get("verify_seconds"),
                          "polygons": polygons,
                          "request_seconds": (round(last["received"] - last["provider_started"], 3)
                                              if last.get("provider_started") is not None else None),
                          **metrics(records, elapsed, live=window["live"])})
        combined = metrics(all_records, elapsed, live=window["live"])
        projected = (round(combined["tokens_per_minute"] * window["seconds"] / 60)
                     if combined["metered_rounds"] >= 2 else None)
        return {"scenario": name, "remaining_seconds": round(max(0, window["seconds"] - elapsed)),
                "window_seconds": window["seconds"], "projected_tokens": projected, "lanes": lanes,
                "floors": floors + combined["project_records"], **combined}
    return None


def live_suite(workspace, directory, scenarios=SCENARIOS, *, model=None, abort=None, resume=False,
               window_seconds=LIVE_WINDOW_SECONDS, deck=None, before_infer=None):
    from subscription_worker import (DriverError, PreflightError, ResponseConnectionError, ResponseStreamEnded,
                                     SubscriptionDriver, peek_setup_status)

    if before_infer is not None:
        before_infer()
    setup = peek_setup_status()
    if not setup["ready"]:
        raise PreflightError("请先用 ChatGPT 授权研究游戏，并确认应用周上限低于 100%。")
    if resume:
        manifest_path = directory / "suite.json"
        if not manifest_path.is_file():
            raise ValueError("找不到可继续的对照配置。")
        earlier = read(manifest_path)["plan"]
        if not earlier.get("live") or not earlier.get("profile"):
            raise ValueError("此目录没有可继续的订阅实跑。")
        model = model or earlier["profile"]["model"]
        scenarios = {name: tuple(earlier["packs"][name]) for name in earlier["scenarios"]}
    prepared_model = SubscriptionDriver(model=model).prepare_model()
    profile = {"model": prepared_model[0], "effort": "high",
               "account": sha256(prepared_model[1].encode()).hexdigest()}
    paced = "relay-frontier-1152-112" in scenarios
    dispatch_lock = Lock()
    next_dispatch = 0.0

    def infer(request, seconds, stopping, *, on_sent):
        deadline = monotonic() + seconds
        submitted_at = None

        def cancelled():
            return stopping.is_set() or monotonic() >= deadline

        def record_submission():
            nonlocal submitted_at, next_dispatch
            if paced:
                with dispatch_lock:
                    slot = max(next_dispatch, monotonic())
                    next_dispatch = slot + 0.30
                stopping.wait(max(0, min(slot, deadline) - monotonic()))
                if cancelled():
                    raise TimeoutError("本组已停止，排队请求未发出。")
            if before_infer is not None:
                before_infer()
            if cancelled():
                raise TimeoutError("本组已停止，请求未发出。 / Run stopped before dispatch.")
            on_sent()
            submitted_at = monotonic()

        driver = SubscriptionDriver(model=model, timeout=min(seconds, 180))
        try:
            if "frontier" in request:
                if request["pack"] == square_frontier.PACK:
                    schema = square_frontier.schema(request["frontier"]["count"])
                else:
                    schema = relay_frontier.schema()
            else:
                schema = guided_search.schema(request["pack"], relay_targets=request.get("relay_targets", 1))
            reply = driver.infer(request["prompt"], cancelled=cancelled, on_ready=record_submission,
                                 schema=schema,
                                 prepared_model=prepared_model,
                                 **({"large_game_prompt": True} if request.get("frontier", {}).get("count", 0) > 192 else {}))
            return {**reply, "guided": True, "provider_started": submitted_at}
        except ResponseStreamEnded as exc:
            if monotonic() >= deadline:
                raise TimeoutError("本组计时结束。") from None
            raise LaneInterrupted(str(exc)) from None
        except ResponseConnectionError as exc:
            raise LaneConnectionError(str(exc)) from None
        except DriverError as exc:
            if monotonic() >= deadline and str(exc).startswith(("已取消", "已停止本轮连接", "模型请求超时")):
                raise TimeoutError("本组计时结束。") from None
            raise

    def publish(pack, proof):
        folder = (research_games.DATABASE.parent if pack in research_games.PACKS else
                  "方格子容纳" if pack.startswith("square-") else "四境驿站")
        return LocalJudge(workspace / folder, pack).store.ingest(proof, improvement_only=True) is not None

    return _run_suite(workspace, directory, scenarios, infer, seconds=window_seconds, live=True,
                      abort=abort if abort is not None else Event(), publish=publish,
                      resume=resume, profile=profile, deck=deck,
                      weekly_snapshot=lambda: weekly_meter.collect(app_weekly_limit_percent=setup.get("weekly_limit_percent")))


def main():
    # The web runner already adds the standalone game's modules to its path.
    sys.path.append(str(ROOT / "四境驿站"))
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--replay", action="store_true",
                        help="Use known candidates to check the local comparison workflow.")
    mode.add_argument("--live", action="store_true", help="Run with this app's authorized ChatGPT plan.")
    parser.add_argument("--model", help="Use the same eligible model for every live comparison.")
    parser.add_argument("--resume", action="store_true", help="Continue the live comparison at --out.")
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--pilot-seconds", type=int, help="Use a shorter live frontier probe window.")
    parser.add_argument("--deck", type=Path, help="Load matching locally prepared mathematical branches.")
    parser.add_argument("--scenario", choices=(*WINDOWS, *PLANS), default="all")
    args = parser.parse_args()
    scenarios = PLANS[args.scenario] if args.scenario in PLANS else {args.scenario: WINDOWS[args.scenario]}
    try:
        if args.resume and not args.live:
            parser.error("--resume 需配合 --live。")
        if args.pilot_seconds is not None and (args.scenario not in FRONTIER_WINDOWS or not args.live
                                               or not 1 <= args.pilot_seconds < LIVE_WINDOW_SECONDS):
            parser.error("--pilot-seconds 仅适用于不足十分钟的分支实跑。")
        if args.deck is not None and (not args.live or args.scenario not in FRONTIER_WINDOWS
                                      or not args.deck.is_dir()):
            parser.error("--deck 需要已备题目录和单项分支游戏实跑。")
        report = (live_suite(ROOT, args.out, scenarios, model=args.model, resume=args.resume, deck=args.deck,
                             window_seconds=args.pilot_seconds or LIVE_WINDOW_SECONDS)
                  if args.live else replay_suite(ROOT, args.out, scenarios))
    except (ValueError, RuntimeError, OSError) as error:
        parser.exit(1, str(error) + "\n")
    print(json.dumps(report, ensure_ascii=False))
    return 1 if any(result["errors"] for result in report["scenarios"].values()) else 0


if __name__ == "__main__":
    raise SystemExit(main())
