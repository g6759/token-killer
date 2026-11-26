"""Deterministic station-pair coverage packs for the construction track."""

from dataclasses import dataclass
from itertools import combinations
import json
from math import comb
from pathlib import Path
from random import Random


FRONTIER_PACK_ID = "relay-49-frontier"
FRONTIER_SOURCE = "https://zenodo.org/records/24119701"


@dataclass(frozen=True)
class CoveragePack:
    title: str
    version: str
    n: int
    k: int
    count: int
    _seed_blocks: tuple[tuple[int, ...], ...]

    def seed(self) -> dict[str, list[list[int]]]:
        return {"blocks": [list(block) for block in self._seed_blocks]}

    def evaluate(self, solution: object) -> dict:
        """Validate JSON data and score the distinct unordered station pairs."""
        if not isinstance(solution, dict) or solution.keys() != {"blocks"}:
            raise ValueError("方案必须是只含 blocks 字段的 JSON 对象。")

        blocks = solution["blocks"]
        if not isinstance(blocks, list) or len(blocks) != self.count:
            raise ValueError(f"blocks 必须含 {self.count} 份名单。")

        rows = set()
        for index, block in enumerate(blocks, start=1):
            if not isinstance(block, list) or len(block) != self.k:
                raise ValueError(f"第 {index} 份名单必须含 {self.k} 个站点。")
            if any(type(station) is not int or not 1 <= station <= self.n for station in block):
                raise ValueError(f"第 {index} 份名单的站点编号必须是 1 至 {self.n} 的整数。")
            if len(set(block)) != self.k:
                raise ValueError(f"第 {index} 份名单含重复站点。")
            row = tuple(sorted(block))
            if row in rows:
                raise ValueError(f"第 {index} 份名单与前面的名单重复。")
            rows.add(row)

        covered = {pair for row in rows for pair in combinations(row, 2)}
        return {
            "solution": {"blocks": [list(row) for row in sorted(rows)]},
            "score": len(covered),
            "total": comb(self.n, 2),
            "missing": [
                list(pair)
                for pair in combinations(range(1, self.n + 1), 2)
                if pair not in covered
            ],
        }


def _research_seed_blocks() -> tuple[tuple[int, ...], ...]:
    rng = Random(49)
    blocks = []
    for _ in range(26):
        # Random.random() with a fixed seed gives a replayable order.
        ranked_stations = sorted(range(1, 50), key=lambda _: rng.random())
        blocks.append(tuple(sorted(ranked_stations[:11])))
    return tuple(sorted(blocks))


def _frontier_seed_blocks() -> tuple[tuple[int, ...], ...]:
    data = json.loads(Path(__file__).with_name("frontier_49.json").read_text(encoding="utf-8"))
    removed = data["removed_block_1_based"] - 1
    blocks = data["blocks"]
    return tuple(tuple(block) for block in blocks[:removed] + blocks[removed + 1:])


_PACKS = {
    "relay-7": CoveragePack(
        title="七站教程",
        version="1",
        n=7,
        k=3,
        count=7,
        _seed_blocks=(
            (1, 2, 4),
            (1, 4, 5),
            (1, 5, 6),
            (2, 4, 6),
            (2, 5, 7),
            (3, 4, 7),
            (3, 5, 6),
        ),
    ),
    "relay-49": CoveragePack(
        title="四十九站研究练习",
        version="1",
        n=49,
        k=11,
        count=26,
        _seed_blocks=_research_seed_blocks(),
    ),
    FRONTIER_PACK_ID: CoveragePack(
        title="四十九站全覆盖",
        version="1",
        n=49,
        k=11,
        count=26,
        _seed_blocks=_frontier_seed_blocks(),
    ),
}


def get_pack(pack_id: str) -> CoveragePack:
    if not isinstance(pack_id, str) or pack_id not in _PACKS:
        raise ValueError(f"未知题包：{pack_id!r}。")
    return _PACKS[pack_id]
