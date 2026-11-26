"""Export and replay the metering and mathematical results of a complete comparison."""

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import hmac
import json
import math
from pathlib import Path
import re
from secrets import token_bytes
import sys

from scoreboard import ROOT, _rules
import research_games

sys.path.insert(0, str(ROOT / "自动运行"))
from benchmark import LIVE_WINDOW_SECONDS, PLANS, USAGE_FIELDS, _overlap_seconds, metrics, select_winner
from geometry import symmetry_key
from relay_store import _verify_chain
from workspace_io import read


FORMAT = "research-games-throughput/v1"
HASH = re.compile(r"[0-9a-f]{64}")
MEASUREMENTS = (
    *USAGE_FIELDS, "total_tokens", "uncached_input_tokens", "tokens_per_minute", "output_per_minute",
    "model_rounds", "metered_rounds", "unique_candidates", "unique_candidates_by_pack", "model_overlap_seconds",
)


def _key(pack, solution):
    signature = symmetry_key(solution) if pack.startswith("square-") else solution
    return sha256(json.dumps(signature, separators=(",", ":")).encode()).hexdigest()


def _validate_layout(pack, layout):
    result = _rules(pack).evaluate(layout)
    if result.get("certified") is False:
        raise ValueError("Lorenz 数值结果未通过认证复核，不能计为有效候选。")
    return _key(pack, result["solution"]), result["score"]


def _comparisons(scenarios):
    return any(scenarios.keys() == cases.keys() and all(
        tuple(scenarios[name]["packs"]) == packs for name, packs in cases.items()
    ) for cases in PLANS.values())


def _verify_window(window, seconds, profile, baselines, seen_ids, *, comparison=True):
    packs = window["packs"]
    candidates = window["candidates"]
    scores = {}
    for pack, options in candidates.items():
        if pack not in packs or not options:
            raise ValueError("候选所属题包不在本次窗口。")
        for key, entry in options.items():
            if _validate_layout(pack, entry["solution"]) != (key, entry["score"]):
                raise ValueError("候选指纹或得分未通过原生裁判复算。")
            scores[pack, key] = entry["score"]

    turns = [0] * len(packs)
    ends = [0.0] * len(packs)
    active = [baselines[pack] for pack in packs]
    used = set()
    records = []
    for receipt in window["receipts"]:
        lane, turn, response_id = receipt["lane"], receipt["turn"], receipt["id"]
        if type(lane) is not int or not 1 <= lane <= len(packs) or type(turn) is not int:
            raise ValueError("通道或轮次无效。")
        if not isinstance(response_id, str) or not HASH.fullmatch(response_id) or response_id in seen_ids:
            raise ValueError("匿名回包指纹缺失或重复。")
        seen_ids.add(response_id)
        index = lane - 1
        start, end = receipt["provider_started"], receipt["received"]
        if (type(start) not in (int, float) or type(end) not in (int, float)
                or not ends[index] <= start < end <= seconds or turn != turns[index] + 1):
            raise ValueError("回包不在窗口内，或同一通道的请求时间与轮次不连续。")
        turns[index], ends[index] = turn, end
        pack, key = packs[index], receipt["key"]
        if key is not None:
            parent, parent_score = active[index]
            if key == parent or (pack, key) not in scores:
                raise ValueError("去重候选须可复算且有别于该通道当前父档。")
            used.add((pack, key))
            if scores[pack, key] > parent_score:
                active[index] = key, scores[pack, key]
        records.append({**receipt, "pack": pack})

    if (comparison and not all(turns)) or used != scores.keys():
        raise ValueError("存在无计量通道或未被回执引用的候选。")
    result = metrics(records, seconds, live=True)
    overlap = round(_overlap_seconds(records, minimum=max(2, len(packs))), 3)
    if result["metered_rounds"] != len(records) or (comparison and len(packs) > 1 and overlap <= 0):
        raise ValueError("回执计量缺失或未观察到所有通道同时在途。")
    result.update(model_overlap_seconds=overlap, window_seconds=seconds, live=True, stop="window",
                  comparable=True, models=[profile["model"]], reasoning_efforts=[profile["effort"]])
    result.pop("project_records")
    for field in MEASUREMENTS:
        if result[field] != window["summary"].get(field):
            raise ValueError(f"窗口统计与回执复算不一致。{field}")
    return result


def verify(proof):
    if not isinstance(proof, dict) or proof.get("format") != FORMAT:
        raise ValueError("吞吐复验文件版本无效。")
    if "single_window" in proof:
        required = {"format", "profile", "seconds", "scenario", "baselines", "single_window"}
        profile = proof.get("profile")
        window = proof.get("single_window")
        if (proof.keys() != required or proof["seconds"] != LIVE_WINDOW_SECONDS
                or proof["scenario"] not in research_games.PACKS
                or not isinstance(profile, dict)
                or not all(isinstance(profile.get(field), str) and profile[field]
                           for field in ("model", "effort"))
                or not isinstance(window, dict)):
            raise ValueError("单题窗口证明格式或配置无效。")
        packs = window.get("packs", [])
        if packs != [proof["scenario"]] or set(proof["baselines"]) != set(packs):
            raise ValueError("单题窗口题包或起始解不一致。")
        baselines = {pack: _validate_layout(pack, entry["solution"])
                     for pack, entry in proof["baselines"].items()}
        result = _verify_window(window, LIVE_WINDOW_SECONDS, profile, baselines, set(), comparison=False)
        return {"scenario": proof["scenario"], "window": result}
    seconds, profile, scenarios = proof["seconds"], proof["profile"], proof["scenarios"]
    if (seconds != LIVE_WINDOW_SECONDS or not _comparisons(scenarios)
            or not all(isinstance(profile.get(field), str) and profile[field] for field in ("model", "effort"))):
        raise ValueError("不支持的对照、窗口时长或模型配置。")
    baselines = {pack: _validate_layout(pack, entry["solution"]) for pack, entry in proof["baselines"].items()}
    if baselines.keys() != {pack for window in scenarios.values() for pack in window["packs"]}:
        raise ValueError("起始布局与对照题包不一致。")
    seen = set()
    results = {}
    for name, window in scenarios.items():
        result = _verify_window(window, seconds, profile, baselines, seen)
        result["initial_hashes"] = {pack: baselines[pack][0] for pack in set(window["packs"])}
        results[name] = result
    winner = select_winner(results)
    if not winner or winner != proof.get("winner"):
        raise ValueError("吞吐胜者与回执复算不一致。")
    return {"winner": winner, "scenarios": results}


def _collect_window(folder, packs, summary, baselines, seconds, profile, secret, *, comparison=True):
    timing = read(folder / "window.json")
    if ((comparison and (not summary["comparable"] or summary["errors"])) or summary["stop"] != "window"
            or summary["wall_seconds"] < seconds
            or not timing["live"] or timing["seconds"] != seconds or timing["packs"] != packs
            or summary["initial_hashes"] != {pack: baselines[pack]["tip"] for pack in set(packs)}):
        raise ValueError(f"{folder.name} 尚未形成可比较的完整窗口。")
    candidates = {}
    receipts = []
    for lane, pack in enumerate(packs, start=1):
        lane_dir = folder / f"lane-{lane}"
        for file in sorted(lane_dir.glob("[0-9]*.json")):
            row = read(file)
            if not row["in_window"]:
                continue
            if (row["pack"] != pack or row["initial_hash"] != baselines[pack]["tip"]
                    or row["request"]["lane"] != lane_dir.name or row["model"] != profile["model"]
                    or row["reasoning_effort"] != profile["effort"] or not row["response_id"]):
                raise ValueError("原始回执与通道、起始存档或模型配置不符。")
            key = row["key"]
            if key:
                if not row["verification"]["valid"]:
                    raise ValueError("未通过裁判的记录不能计作候选。")
                solution = row["candidate"]["solution"] if row["candidate"] else json.loads(row["response"])["solution"]
                entries = candidates.setdefault(pack, {})
                entry = {"solution": solution, "score": row["verification"]["score"]}
                if entries.setdefault(key, entry) != entry:
                    if _validate_layout(pack, solution) != (key, entry["score"]):
                        raise ValueError("同一候选指纹下的实际布局与得分不一致。")
            usage = {field: row["usage"][field] for field in USAGE_FIELDS}
            if type(row["usage"].get("total_tokens")) is int:
                usage["total_tokens"] = row["usage"]["total_tokens"]
            receipts.append({"lane": lane, "turn": row["turn"], "key": key, "usage": usage,
                             "id": hmac.new(secret, row["response_id"].encode(), "sha256").hexdigest(),
                             "provider_started": row["provider_started"], "received": row["received"]})
    return {"packs": packs, "summary": {key: summary[key] for key in MEASUREMENTS},
            "receipts": receipts, "candidates": candidates}


def collect_run(folder):
    """Read only complete current windows and discard prompts, notes and provider identifiers."""
    saved = read(folder / "summary.json")
    manifest = read(folder / "suite.json")
    plan, baselines = manifest["plan"], manifest["baselines"]
    seconds = plan["seconds"]
    scenarios = plan["scenarios"]
    if (saved.get("live") and not saved.get("incomplete") and plan.get("live")
            and seconds == LIVE_WINDOW_SECONDS and len(scenarios) == 1
            and scenarios[0] in research_games.PACKS
            and plan["packs"].get(scenarios[0]) == [scenarios[0]]
            and set(saved["scenarios"]) == {scenarios[0]}):
        name = scenarios[0]
        window_summary = saved["scenarios"][name]
        if (window_summary.get("live") is not True or window_summary.get("stop") != "window"
                or window_summary.get("errors")):
            raise ValueError(f"{name} 含错误或未形成完整窗口。")
        profile = {field: plan["profile"][field] for field in ("model", "effort")}
        proof = {"format": FORMAT, "profile": profile, "seconds": seconds, "scenario": name,
                 "baselines": {pack: {"solution": _verify_chain(bundle, _rules)[-1]["solution"]}
                               for pack, bundle in baselines.items()},
                 "single_window": _collect_window(folder / name, plan["packs"][name],
                                                   window_summary, baselines, seconds,
                                                   profile, token_bytes(32), comparison=False)}
        verify(proof)
        return proof
    if (not saved["live"] or saved["incomplete"] or not saved["winner"] or not plan["live"]
            or seconds != LIVE_WINDOW_SECONDS
            or not _comparisons({name: {"packs": packs} for name, packs in plan["packs"].items()})
            or saved["scenarios"].keys() != plan["packs"].keys()):
        raise ValueError("只有已完整结束且出现胜者的正式对照可以导出。")
    profile = {field: plan["profile"][field] for field in ("model", "effort")}
    secret = token_bytes(32)
    proof = {"format": FORMAT, "profile": profile, "seconds": seconds, "winner": saved["winner"],
             "baselines": {pack: {"solution": _verify_chain(bundle, _rules)[-1]["solution"]}
                           for pack, bundle in baselines.items()},
             "scenarios": {name: _collect_window(folder / name, packs, saved["scenarios"][name],
                                                  baselines, seconds, profile, secret)
                           for name, packs in plan["packs"].items()}}
    verify(proof)
    return proof


def audit_goal_window(folder, target_per_minute=5_000_000):
    """Recompute the ten-minute game target directly from an individual window's receipts."""
    if type(target_per_minute) is not int or target_per_minute <= 0:
        raise ValueError("每分钟目标须为正整数。")
    manifest = read(folder.parent / "suite.json")
    plan = manifest["plan"]
    seconds = plan["seconds"]
    if not plan["live"] or seconds != LIVE_WINDOW_SECONDS or folder.name not in plan["scenarios"]:
        raise ValueError("目标核验需要一个连续十分钟的真实游戏窗口。")
    packs = plan["packs"][folder.name]
    baselines = {pack: manifest["baselines"][pack] for pack in set(packs)}
    profile = plan["profile"]
    window = _collect_window(folder, packs, read(folder / "summary.json"), baselines,
                             seconds, profile, token_bytes(32), comparison=False)
    initial = {pack: _validate_layout(pack, _verify_chain(bundle, _rules)[-1]["solution"])
               for pack, bundle in baselines.items()}
    tally = _verify_window(window, seconds, profile, initial, set(), comparison=False)
    bins = [0] * 10
    for receipt in window["receipts"]:
        usage = receipt["usage"]
        minute = min(int(receipt["received"] // 60), 9)
        bins[minute] += usage["input_tokens"] - usage["cached_input_tokens"] + usage["output_tokens"]
    effective = sum(bins)
    target = target_per_minute * (seconds // 60)
    return {"scenario": folder.name, "model": profile["model"], "reasoning_effort": profile["effort"],
            "lanes": len(packs), "window_seconds": seconds, "completed_receipts": tally["metered_rounds"],
            "uncached_input_tokens": tally["uncached_input_tokens"], "output_tokens": tally["output_tokens"],
            "cached_input_tokens": tally["cached_input_tokens"],
            "unique_candidates_by_pack": tally["unique_candidates_by_pack"],
            "effective_tokens_per_minute_bucket": bins, "effective_tokens": effective,
            "average_tokens_per_minute": effective / (seconds / 60), "target_tokens": target,
            "target_per_minute": target_per_minute, "target_met": effective >= target}


def audit_series(folders):
    """Independently verify and total two or more complete, non-overlapping windows."""
    folders = [Path(folder) for folder in folders]
    if len(folders) < 2:
        raise ValueError("总量赛道至少需要两个完整窗口。")

    ordered = []
    for folder in folders:
        timing = read(folder / "window.json")
        summary = read(folder / "summary.json")
        started_at = timing.get("started_at")
        wall_seconds = summary.get("wall_seconds")
        if (type(started_at) not in (int, float) or not math.isfinite(started_at)
                or type(wall_seconds) not in (int, float) or not math.isfinite(wall_seconds)
                or wall_seconds < LIVE_WINDOW_SECONDS):
            raise ValueError(f"{folder.name} 缺少有效的完整窗口时间记录。")
        ordered.append((started_at, started_at + wall_seconds, folder, timing, summary))
    ordered.sort(key=lambda item: (item[0], str(item[2]).casefold()))
    for previous, current in zip(ordered, ordered[1:]):
        if current[0] < previous[1]:
            raise ValueError("窗口之间存在时间重叠。")

    secret = token_bytes(32)
    seen_ids = set()
    windows = []
    for index, (started_at, ended_at, folder, timing, summary) in enumerate(ordered, start=1):
        manifest = read(folder.parent / "suite.json")
        plan = manifest["plan"]
        seconds = plan["seconds"]
        if (not plan["live"] or seconds != LIVE_WINDOW_SECONDS or not timing["live"]
                or timing["seconds"] != LIVE_WINDOW_SECONDS):
            raise ValueError(f"{folder.name} 不是完整的真实 600 秒窗口。")
        if summary.get("live") is not True or summary.get("stop") != "window" or summary.get("errors"):
            raise ValueError(f"{folder.name} 含错误或未形成完整窗口。")
        if folder.name not in plan["scenarios"]:
            raise ValueError(f"{folder.name} 不属于其运行档中的场景。")
        packs = plan["packs"][folder.name]
        if timing["packs"] != packs:
            raise ValueError(f"{folder.name} 的题包与运行档不一致。")
        baseline_bundles = manifest["baselines"]
        if set(packs) != set(baseline_bundles):
            raise ValueError(f"{folder.name} 的起始存档与题包不一致。")
        profile = {field: plan["profile"][field] for field in ("model", "effort")}
        baselines = {pack: _verify_chain(bundle, _rules)[-1] for pack, bundle in baseline_bundles.items()}
        baselines = {pack: _validate_layout(pack, row["solution"]) for pack, row in baselines.items()}
        if set(baseline_bundles) != set(packs):
            raise ValueError(f"{folder.name} 含不属于当前窗口的起始存档。")
        window = _collect_window(folder, packs, summary, baseline_bundles, seconds,
                                 profile, secret, comparison=False)
        tally = _verify_window(window, seconds, profile, baselines, seen_ids, comparison=False)
        effective_tokens = tally["uncached_input_tokens"] + tally["output_tokens"]
        windows.append({
            "window": index, "name": folder.name, "started_at": started_at, "ended_at": ended_at,
            "elapsed_seconds": summary["wall_seconds"], "packs": list(packs), "model": profile["model"],
            "reasoning_effort": profile["effort"], "effective_tokens": effective_tokens,
            "metered_rounds": tally["metered_rounds"],
            "tokens_per_minute": effective_tokens / (seconds / 60),
            "unique_candidates": tally["unique_candidates"],
            "unique_candidates_by_pack": tally["unique_candidates_by_pack"],
            "uncached_input_tokens": tally["uncached_input_tokens"],
            "cached_input_tokens": tally["cached_input_tokens"], "output_tokens": tally["output_tokens"],
        })
    start, end = ordered[0][0], ordered[-1][1]
    return {
        "window_count": len(windows),
        "effective_tokens": sum(window["effective_tokens"] for window in windows),
        "metered_rounds": sum(window["metered_rounds"] for window in windows),
        "start_utc": datetime.fromtimestamp(start, timezone.utc).isoformat(),
        "end_utc": datetime.fromtimestamp(end, timezone.utc).isoformat(),
        "elapsed_seconds": round(end - start, 3),
        "peak_tokens_per_minute": max(window["tokens_per_minute"] for window in windows),
        "windows": windows,
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("proof", type=Path, nargs="?")
    parser.add_argument("--series", type=Path, nargs="+", help="复验并合计两个或更多完整窗口目录。")
    parser.add_argument("--goal-window", action="store_true", help="Verify one ten-minute game window directory.")
    parser.add_argument("--target-per-minute", type=int, default=5_000_000)
    args = parser.parse_args()
    try:
        if args.series is not None:
            if args.proof is not None or args.goal_window:
                parser.error("--series 不能与证明文件位置参数或 --goal-window 同时使用。")
            print(json.dumps(audit_series(args.series), ensure_ascii=False))
            return
        if args.proof is None:
            parser.error("请指定证明文件、--goal-window 目录或 --series 窗口目录。")
        if args.goal_window:
            print(json.dumps(audit_goal_window(args.proof, args.target_per_minute), ensure_ascii=False))
            return
        result = verify(read(args.proof))
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"吞吐复核失败。{error}\n")
    if "window" in result:
        window = result["window"]
        print(f"{result['scenario']} · {window['tokens_per_minute']:,.2f} 有效 token/分钟"
              f" · 总计 {window['total_tokens']:,} · {window['metered_rounds']:,} 个回包"
              f" · {window['unique_candidates']} 个去重候选")
        print("单题窗口复算通过")
        return
    for name, window in result["scenarios"].items():
        print(f"{name} · {window['tokens_per_minute']:,.2f} 有效 token/分钟"
              f" · 总计 {window['total_tokens']:,} · 缓存 {window['cached_input_tokens']:,}"
              f" · 输出 {window['output_per_minute']:,.2f}/分钟"
              f" · {window['metered_rounds']:,} 个回包 · {window['unique_candidates']} 个去重候选")
    print(f"复算胜者 · {result['winner']}")


if __name__ == "__main__":
    main()
