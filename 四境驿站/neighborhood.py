"""Issue and independently replay exact single-station replacement certificates."""

import argparse
import json
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

from baseline import scan_neighbors
from packs import get_pack
from relay import read_json
from relay_store import Store


FORMAT = "relay-neighborhood/v1"
SCOPE = (
    "仅核验此存档的单站替换。每次从一份名单移出一站，换入该名单之外的一站，"
    "且 26 份名单仍互不重复。多步调整和其他构造须另行验证。"
)


def audit(record):
    """Enumerate every legal move from one validated 49-station checkpoint."""
    state = record["state"]
    pack = get_pack(state["pack_id"])
    if (pack.n, pack.k, pack.count) != (49, 11, 26):
        raise ValueError("单站替换证书适用于四十九站题包")
    result = pack.evaluate(record["solution"])
    blocks = tuple(map(tuple, result["solution"]["blocks"]))
    score, changes, best = scan_neighbors(blocks, pack.n)
    if score != result["score"] or score != state["score"]:
        raise ValueError("存档得分与复算得分不符")
    total = sum(changes.values())
    better = sum(count for delta, count in changes.items() if delta > 0)
    equal = changes[0]
    return {
        "format": FORMAT,
        "pack_id": state["pack_id"],
        "parent_hash": record["hash"],
        "score": score,
        "total_neighbors": total,
        "better": better,
        "equal": equal,
        "worse": total - better - equal,
        "best_delta": best.delta if best is not None else None,
        "title": "单站替换已排尽" if better == 0 else "已复算单换邻域",
        "scope": SCOPE,
    }


def verify(certificate):
    """Replay the embedded save into an isolated store, then recompute all counts."""
    if not isinstance(certificate, dict) or certificate.get("format") != FORMAT or "checkpoint" not in certificate:
        raise ValueError("证书格式无效")
    with TemporaryDirectory(prefix="relay-neighborhood-") as folder:
        store = Store(Path(folder) / "verify.sqlite3")
        record = store.ingest(certificate["checkpoint"])
    expected = audit(record)
    claimed = {key: value for key, value in certificate.items() if key != "checkpoint"}
    if json.dumps(claimed, sort_keys=True) != json.dumps(expected, sort_keys=True):
        raise ValueError("证书汇总、范围或父档指纹与本机复算不符")
    return expected


def main():
    parser = argparse.ArgumentParser(description="独立核验四镜驿站的单站替换证书")
    commands = parser.add_subparsers(dest="action", required=True)
    verify_cmd = commands.add_parser("verify", help="重放证书中的存档并逐一重新计分")
    verify_cmd.add_argument("certificate", type=Path)
    args = parser.parse_args()
    result = verify(read_json(args.certificate, 16 * 1024 * 1024))
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    try:
        main()
    except (OSError, RecursionError, ValueError) as error:
        sys.exit(f"核验未通过：{error}")
