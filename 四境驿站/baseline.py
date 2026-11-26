"""Deterministic one-station neighborhood search for the relay-49 seed."""

import argparse
from collections import Counter
from dataclasses import dataclass
from itertools import combinations

from packs import get_pack


@dataclass(frozen=True)
class Swap:
    index: int
    removed: int
    added: int
    replacement: tuple[int, ...]
    gained: int
    lost: int

    @property
    def delta(self):
        return self.gained - self.lost


def pair(a, b):
    return (a, b) if a < b else (b, a)


def scan_neighbors(blocks, station_count):
    counts = Counter(edge for block in blocks for edge in combinations(block, 2))
    occupied = set(blocks)
    changes = Counter()
    best = None

    # The judge supplies sorted blocks and stations. The first tied move wins.
    for index, block in enumerate(blocks):
        unused = [station for station in range(1, station_count + 1) if station not in block]
        for removed in block:
            kept = tuple(station for station in block if station != removed)
            lost = sum(counts[pair(station, removed)] == 1 for station in kept)
            for added in unused:
                replacement = tuple(sorted((*kept, added)))
                if replacement in occupied:
                    continue
                gained = sum(counts[pair(station, added)] == 0 for station in kept)
                delta = gained - lost
                changes[delta] += 1
                if best is None or delta > best.delta:
                    best = Swap(index, removed, added, replacement, gained, lost)

    return len(counts), changes, best


def main():
    parser = argparse.ArgumentParser(description="复算 relay-49 固定种子的单站替换邻域。")
    parser.add_argument("--steps", type=int, default=1, help="严格提分的贪心步数上限（默认 1 步）")
    args = parser.parse_args()
    if args.steps < 1:
        parser.error("--steps 必须为正整数")

    pack = get_pack("relay-49")
    verified = pack.evaluate(pack.seed())
    total = verified["total"]
    scores = [verified["score"]]

    for step in range(args.steps):
        blocks = tuple(map(tuple, verified["solution"]["blocks"]))
        score, changes, swap = scan_neighbors(blocks, pack.n)
        if swap is None:
            raise RuntimeError("relay-49 的当前解没有合法替换")
        candidate = [list(block) for block in blocks]
        candidate[swap.index] = list(swap.replacement)
        result = pack.evaluate({"blocks": candidate})
        if score != scores[-1] or result["score"] != score + swap.delta:
            raise RuntimeError("增量计算与原裁判计分不一致")

        if step == 0:
            improved = sum(count for delta, count in changes.items() if delta > 0)
            declined = sum(count for delta, count in changes.items() if delta < 0)
            print(f"枚举 {sum(changes.values())} 个合法邻居 · 提升 {improved} · 持平 {changes[0]} · 降低 {declined}")
            print(f"种子 {score}/{total} · 一步最优 {result['score']}/{total} · 并列 {changes[swap.delta]} 个")
            print(f"最佳替换：第 {swap.index + 1} 组，{swap.removed} → {swap.added}"
                  f"（新增 {swap.gained} 对，失去 {swap.lost} 对）")

        if swap.delta <= 0:
            print(f"第 {step + 1} 步停止：没有严格提分的单站替换。")
            break
        verified = result
        scores.append(verified["score"])

    print("贪心分数（满分 " + str(total) + "）：" + " → ".join(map(str, scores)))


if __name__ == "__main__":
    main()
