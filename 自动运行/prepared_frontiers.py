"""Prepare relay or square-packing frontier prompts offline for a benchmark.

The live runner regenerates a prompt from the selected game's current parent
when its cache entry is missing or stale.
"""

from __future__ import annotations

import argparse
from concurrent.futures import FIRST_COMPLETED, ProcessPoolExecutor, wait
import gzip
from hashlib import sha256
import json
import os
from pathlib import Path
import tempfile
from time import monotonic

import relay_frontier
import square_frontier
from workspace_io import check_disk, exclusive


FORMAT = 1
PACK = relay_frontier.PACK_ID
COUNT = 1152
MAX_WORKERS = 4


def default_deck(suite):
    """Keep preparation beside, never inside, the future suite directory."""
    suite = Path(suite)
    return suite.with_name(suite.name + "-prepared-frontiers")


def _identity(suite, scenario, lane, turn, parent, count, pack):
    suite_name = Path(suite).name
    if not suite_name or suite_name in (".", ".."):
        raise ValueError("suite 须有目录名。")
    if (not isinstance(scenario, str) or scenario in (".", "..")
            or not scenario or Path(scenario).name != scenario):
        raise ValueError("scenario 须为单个目录名。")
    if isinstance(lane, str) and lane.startswith("lane-"):
        lane = lane[5:]
    try:
        lane = int(lane)
    except (TypeError, ValueError) as exc:
        raise ValueError("lane 须为 1 起编号。") from exc
    if lane < 1 or type(turn) is not int or turn < 1:
        raise ValueError("lane 和 turn 须为正整数。")
    if pack == square_frontier.PACK:
        valid_count = type(count) is int and 1 <= count <= square_frontier.MAX_BRANCHES
    else:
        valid_count = pack == PACK and relay_frontier._valid_count(count)
    if not valid_count:
        raise ValueError("不支持的分支题型或分支数。")
    if not isinstance(parent, dict) or not isinstance(parent.get("hash"), str) or not parent["hash"]:
        raise ValueError("父档须提供非空 hash。")
    if "solution" not in parent:
        raise ValueError("父档缺少原生 solution。")
    native = {"hash": parent["hash"], "solution": parent["solution"]}
    parent_bytes = json.dumps(native, ensure_ascii=False, sort_keys=True,
                              separators=(",", ":"), allow_nan=False).encode("utf-8")
    return {
        "format": FORMAT, "suite": suite_name, "scenario": scenario,
        "lane": lane, "turn": turn, "pack": pack, "count": count,
        "builder_lane": f"{suite_name}/lane-{lane}",
        "parent_hash": parent["hash"],
        "parent_sha256": sha256(parent_bytes).hexdigest(),
    }


def _entry_path(deck, identity):
    return Path(deck) / identity["scenario"] / f"lane-{identity['lane']:04d}" / f"turn-{identity['turn']:04d}.json.gz"


def load_if_matching(deck, suite, scenario, lane, turn, parent, *, count=COUNT, pack=PACK):
    """Return a verified (prompt, context), or None for a missing/stale cache.

    Caller should then build against its current parent. Cache corruption never
    becomes a model request. The parent fingerprint covers the native solution
    and hash, rather than a suite's incidental metadata.
    """
    try:
        identity = _identity(suite, scenario, lane, turn, parent, count, pack)
        with gzip.open(_entry_path(deck, identity), "rt", encoding="utf-8") as stream:
            entry = json.load(stream)
        if not isinstance(entry, dict) or any(entry.get(key) != value for key, value in identity.items()):
            return None
        prompt, context = entry.get("prompt"), entry.get("context")
        if not isinstance(prompt, str) or not isinstance(context, dict):
            return None
        if entry.get("prompt_sha256") != sha256(prompt.encode("utf-8")).hexdigest():
            return None
        engine = square_frontier if pack == square_frontier.PACK else relay_frontier
        expected_seed = engine._seed(pack, parent["hash"], identity["builder_lane"], turn, count)
        if context != {"seed": expected_seed, "count": count, "parent_hash": parent["hash"]}:
            return None
        return prompt, context
    except (OSError, EOFError, ValueError, TypeError, KeyError, UnicodeError):
        return None


def _write_entry(deck, identity, prompt, context):
    path = _entry_path(deck, identity)
    path.parent.mkdir(parents=True, exist_ok=True)
    entry = {**identity, "prompt_sha256": sha256(prompt.encode("utf-8")).hexdigest(),
             "prompt": prompt, "context": context}
    body = gzip.compress(json.dumps(entry, ensure_ascii=False, separators=(",", ":"),
                                    allow_nan=False).encode("utf-8"), compresslevel=6, mtime=0)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=path.name + "-", suffix=".tmp",
                                         delete=False) as stream:
            temporary = Path(stream.name)
            stream.write(body)
            stream.flush()
            os.fsync(stream.fileno())
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _build(pack, parent, builder_lane, turn, count):
    engine = square_frontier if pack == square_frontier.PACK else relay_frontier
    return engine.build(pack, parent, builder_lane, turn, count)


def prepare(deck, suite, scenario, parent, lanes, turns, *, workers=1, count=COUNT, pack=PACK, progress=print):
    """Build missing prompts with a bounded process pool and resumable files."""
    if type(lanes) is not int or lanes < 1 or type(turns) is not int or turns < 1:
        raise ValueError("lanes 和 turns 须为正整数。")
    if type(workers) is not int or not 1 <= workers <= MAX_WORKERS:
        raise ValueError(f"workers 须为 1 至 {MAX_WORKERS}。")
    deck = Path(deck).resolve()
    check_disk(deck)
    total = lanes * turns
    done = 0
    generated = 0
    last_disk_check = monotonic()

    def disk_due():
        nonlocal last_disk_check
        # The wait below can add five seconds, so start the check at 40.
        if monotonic() - last_disk_check >= 40:
            check_disk(deck)
            last_disk_check = monotonic()

    with exclusive(deck):
        missing = []
        for turn in range(1, turns + 1):
            for lane in range(1, lanes + 1):
                disk_due()
                identity = _identity(suite, scenario, lane, turn, parent, count, pack)
                if load_if_matching(deck, suite, scenario, lane, turn, parent, count=count, pack=pack) is not None:
                    done += 1
                    progress(f"[{done}/{total}] 已验证，跳过 lane-{lane} turn-{turn}")
                else:
                    missing.append(identity)
        if not missing:
            return {"total": total, "skipped": done, "generated": 0, "deck": str(deck)}
        skipped = done
        pool = ProcessPoolExecutor(max_workers=min(workers, len(missing)))
        pending = {}
        source = iter(missing)
        try:
            def submit_one():
                identity = next(source, None)
                if identity is None:
                    return
                future = pool.submit(_build, pack, parent, identity["builder_lane"],
                                     identity["turn"], count)
                pending[future] = identity

            for _ in range(min(workers, len(missing))):
                submit_one()
            while pending:
                disk_due()
                completed, _ = wait(pending, timeout=5, return_when=FIRST_COMPLETED)
                for future in completed:
                    identity = pending.pop(future)
                    prompt, context = future.result()
                    disk_due()
                    _write_entry(deck, identity, prompt, context)
                    done += 1
                    generated += 1
                    progress(f"[{done}/{total}] 已备题 lane-{identity['lane']} turn-{identity['turn']}")
                    submit_one()
        except BaseException:
            for future in pending:
                future.cancel()
            processes = tuple((getattr(pool, "_processes", None) or {}).values())
            pool.shutdown(wait=False, cancel_futures=True)
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            raise
        else:
            pool.shutdown(wait=True)
        return {"total": total, "skipped": skipped, "generated": generated, "deck": str(deck)}


def _read_parent(path, pack):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if isinstance(data, dict) and pack in data:
        data = data[pack]
    if isinstance(data, dict) and "chain" in data:
        chain = data["chain"]
        if not isinstance(chain, list) or not chain:
            raise ValueError("原生父档 chain 为空。")
        data = chain[-1]
    if not isinstance(data, dict):
        raise ValueError("找不到原生父档。")
    return data


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--suite", required=True, type=Path, help="未来独立 suite 的目录名或路径")
    parser.add_argument("--scenario", required=True, help="将使用备题的场景名")
    parser.add_argument("--pack", choices=(PACK, square_frontier.PACK), default=PACK)
    parser.add_argument("--count", type=int, default=COUNT, help="分支数，须与实跑场景一致")
    source = parser.add_mutually_exclusive_group(required=True)
    source.add_argument("--inputs", type=Path, help="现有 inputs.json，取该 pack 的 chain[-1]")
    source.add_argument("--parent", type=Path, help="原生父档 JSON，或含 chain 的导出 JSON")
    parser.add_argument("--lanes", required=True, type=int)
    parser.add_argument("--turns", required=True, type=int)
    parser.add_argument("--workers", type=int, default=1, help=f"生成进程数，1 至 {MAX_WORKERS}")
    parser.add_argument("--deck", type=Path, help="缓存目录；默认位于 suite 同级")
    args = parser.parse_args(argv)
    parent = _read_parent(args.inputs or args.parent, args.pack)
    result = prepare(args.deck or default_deck(args.suite), args.suite, args.scenario,
                     parent, args.lanes, args.turns, workers=args.workers, pack=args.pack, count=args.count,
                     progress=lambda line: print(line, flush=True))
    print(json.dumps(result, ensure_ascii=False), flush=True)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
