"""Exact validation and scoring for packing unit squares in an axis-aligned square."""

import hashlib
import json
from dataclasses import dataclass
from fractions import Fraction
from itertools import combinations
from math import ceil, gcd
from pathlib import Path

if __package__:
    from .packing_catalog import PACK_COUNTS
else:
    from packing_catalog import PACK_COUNTS


SCALE = 1_000_000
MAX_CENTER = 100 * SCALE
MAX_DENOMINATOR = 10_000
# The span is at most 100 units plus two half-diagonals.
SCORE_LIMIT = 102 * SCALE
_LAYOUT_PATH = Path(__file__).with_name("reference_layouts.json")


@dataclass(frozen=True, slots=True)
class _Square:
    x: int
    y: int
    cos_num: int
    sin_num: int
    denominator: int

    @classmethod
    def from_pose(cls, pose: list[int]) -> "_Square":
        x, y, p, q = pose
        # t = p/q = tan(theta/2), so the rotation matrix is rational.
        return cls(x, y, q * q - p * p, 2 * p * q, q * q + p * p)

    def vertices(self) -> list[tuple[Fraction, Fraction]]:
        half_unit = Fraction(SCALE, 2 * self.denominator)
        return [
            (
                self.x + half_unit * (u * self.cos_num - v * self.sin_num),
                self.y + half_unit * (u * self.sin_num + v * self.cos_num),
            )
            for u, v in ((-1, -1), (1, -1), (1, 1), (-1, 1))
        ]


def _has_separating_axis(owner: _Square, other: _Square) -> bool:
    dx, dy = other.x - owner.x, other.y - owner.y
    for ax, ay in ((owner.cos_num, owner.sin_num), (-owner.sin_num, owner.cos_num)):
        # The axes are scaled by owner.denominator. Compare the center projection
        # with the sum of projected half-widths, clearing the remaining denominator.
        other_width_num = abs(ax * other.cos_num + ay * other.sin_num) + abs(
            -ax * other.sin_num + ay * other.cos_num
        )
        center_distance = 2 * abs(ax * dx + ay * dy) * other.denominator
        sum_of_radii = SCALE * (owner.denominator * other.denominator + other_width_num)
        if center_distance >= sum_of_radii:
            return True
    return False


def _bounds(polygons: list[list[tuple[Fraction, Fraction]]]) -> tuple[Fraction, ...]:
    xs, ys = zip(*(point for polygon in polygons for point in polygon))
    return min(xs), min(ys), max(xs), max(ys)


def symmetry_key(solution: dict) -> tuple[tuple[int, int, int, int], ...]:
    """Compare validated layouts up to translation, quarter turns, and reflection."""
    poses = [tuple(pose) for pose in solution["poses"]]
    mirrored = []
    for x, y, p, q in poses:
        if p:
            # Reflect theta to -theta, then add 90 degrees for the square's angle range.
            p, q = q - p, q + p
            divisor = gcd(p, q)
            p, q = p // divisor, q // divisor
        mirrored.append((-x, y, p, q))

    representations = []
    for orientation in (poses, mirrored):
        for _ in range(4):
            min_x = min(x for x, _, _, _ in orientation)
            min_y = min(y for _, y, _, _ in orientation)
            representations.append(
                tuple(sorted((x - min_x, y - min_y, p, q) for x, y, p, q in orientation))
            )
            orientation = [(-y, x, p, q) for x, y, p, q in orientation]
    return min(representations)


@dataclass(frozen=True, slots=True)
class SquarePack:
    n: int
    version = "1"

    def seed(self) -> dict:
        """Return fresh canonical poses for the coarse public construction."""
        layouts = json.loads(_LAYOUT_PATH.read_text(encoding="utf-8"))
        return layouts[f"square-{self.n}"]["seed"]

    def _validated_poses(self, solution: dict) -> list[list[int]]:
        if not isinstance(solution, dict) or solution.keys() != {"poses"}:
            raise ValueError("方案应为只包含 poses 字段的 JSON 对象。")
        poses = solution["poses"]
        if not isinstance(poses, list) or len(poses) != self.n:
            raise ValueError(f"poses 应为恰好包含 {self.n} 个方块的列表。")
        for index, pose in enumerate(poses, start=1):
            if (
                not isinstance(pose, list)
                or len(pose) != 4
                or any(type(value) is not int for value in pose)
            ):
                raise ValueError(f"第 {index} 个方块须填写四个整数 [cx, cy, p, q]。")
            x, y, p, q = pose
            if not (0 <= x <= MAX_CENTER and 0 <= y <= MAX_CENTER):
                raise ValueError(f"第 {index} 个方块的中心坐标须在 0 到 {MAX_CENTER} 微单位之间（含端点）。")
            if not (0 <= p < q <= MAX_DENOMINATOR and gcd(p, q) == 1):
                raise ValueError(f"第 {index} 个方块的角度参数须满足 0 ≤ p < q ≤ {MAX_DENOMINATOR}，且 p/q 须约至最简。")
        return poses

    def _validated_squares(self, solution: dict) -> tuple[dict, list[_Square]]:
        poses = self._validated_poses(solution)
        squares = [_Square.from_pose(pose) for pose in poses]
        for (i, first), (j, second) in combinations(enumerate(squares, start=1), 2):
            if not (_has_separating_axis(first, second) or _has_separating_axis(second, first)):
                raise ValueError(f"第 {i} 个方块与第 {j} 个方块重叠。")

        min_x = min(pose[0] for pose in poses)
        min_y = min(pose[1] for pose in poses)
        canonical = {"poses": sorted([[x - min_x, y - min_y, p, q] for x, y, p, q in poses])}
        return canonical, squares

    def evaluate(self, solution: dict) -> dict:
        """Validate the placement and return its canonical solution and score."""
        canonical, squares = self._validated_squares(solution)
        left, bottom, right, top = _bounds([square.vertices() for square in squares])
        exact_ticks = max(right - left, top - bottom)
        side_ticks = ceil(exact_ticks)
        payload = json.dumps(canonical, sort_keys=True, separators=(",", ":")).encode("utf-8")
        return {
            "solution": canonical,
            "score": SCORE_LIMIT - side_ticks,
            "total": SCORE_LIMIT,
            "side_ticks": side_ticks,
            "exact_side": str(exact_ticks / SCALE),
            "solution_hash": hashlib.sha256(payload).hexdigest(),
        }

    def drawing(self, solution: dict) -> list[list[list[float]]]:
        """Return four [x, y] vertices per square in input order and in original units.

        The minimum vertex coordinate on each axis is translated to zero.
        """
        _, squares = self._validated_squares(solution)
        polygons = [square.vertices() for square in squares]
        left, bottom, _, _ = _bounds(polygons)
        return [
            [[float((x - left) / SCALE), float((y - bottom) / SCALE)] for x, y in polygon]
            for polygon in polygons
        ]


def get_square_pack(pack_id: str) -> SquarePack:
    try:
        count = PACK_COUNTS[pack_id]
    except (KeyError, TypeError):
        raise ValueError(f"找不到方格子容纳题目 {pack_id!r}。") from None
    return SquarePack(count)
