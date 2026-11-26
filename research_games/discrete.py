"""Exact finite judges for Cap Set and Golomb ruler research packs.

All inputs are ordinary JSON-compatible data. No candidate code is imported or
executed. Public scoring is monotone and integer-valued; detailed game measures
are returned in ``metrics`` for display and archival.
"""

from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
from itertools import product


VERSION = "1"
MAX_CAP_POINTS = 1024
MAX_MARK_POSITION = 1_000_000
GOLUMB_TOTAL = MAX_MARK_POSITION

_CAP_DIMENSIONS = {
    "cap-set": 4,
    "cap-set-5d": 5,
    "cap-set-6d": 6,
    "cap-set-7d": 7,
    "cap-set-8d": 8,
}
_GOLUMB_MARKS = {
    "golomb-ruler": 8,
    "golomb-ruler-12": 12,
    "golomb-ruler-16": 16,
}

# A checked 18-point cap in F_3^4. Its first 16 lexicographically sorted
# points form the default parent; the complete set is a reproducible +2 sample.
_CAP_4_IMPROVEMENT = (
    (0, 1, 0, 0), (0, 1, 1, 2), (0, 2, 2, 1), (1, 0, 0, 2),
    (1, 0, 2, 1), (1, 1, 0, 1), (1, 1, 0, 2), (1, 1, 2, 0),
    (1, 1, 2, 1), (1, 2, 1, 2), (1, 2, 2, 0), (2, 0, 0, 0),
    (2, 0, 0, 2), (2, 0, 2, 0), (2, 1, 1, 1), (2, 2, 1, 0),
    (2, 2, 1, 1), (2, 2, 2, 1),
)


def _fail(code, zh, en):
    raise ValueError(f"{code}: {zh} / {en}")


@lru_cache(maxsize=1)
def _cap_reference_points_8d_401():
    """Generate the historical 401-point F_3^8 construction.

    The modular equations are the documented project construction. This is a
    reference witness, not a claim that it is novel or optimal.
    """
    points = []
    for point in product(range(3), repeat=8):
        a, b, c, d, u, v, s, t = point
        head = (a, b, c, d)
        tail = (u, v, s, t)
        if not any(head) and not any(tail):
            points.append(point)
            continue
        if not any(head) or not any(tail):
            continue
        if (a * b + c * c + d * d) % 3:
            continue
        first_nonzero = next(value for value in head if value)
        if first_nonzero == 1:
            tail_ok = (u * u + v * v + s * s - t * t) % 3 == 0
        else:
            tail_ok = (u * u + v * v + 2 * s * t) % 3 == 0
        if tail_ok:
            points.append(point)
    return tuple(sorted(points))


def cap_set_8d_401_reference():
    """Return a fresh JSON solution for the historical 401-point reference."""
    points = _cap_reference_points_8d_401()
    if len(points) != 401:
        raise RuntimeError("CAP_REFERENCE_401: historical reference generation mismatch")
    return {"points": [list(point) for point in points]}


def cap_set_improvement_example(pack_id="cap-set"):
    """Return a deterministic cap strictly larger than that pack's seed."""
    pack = get_pack(pack_id)
    if pack.kind != "cap-set":
        _fail("PACK_KIND", "该题包不是 Cap Set。", "This pack is not a Cap Set.")
    points = []
    for base in _CAP_4_IMPROVEMENT:
        for suffix in product((0, 1), repeat=pack.dimension - 4):
            points.append(base + suffix)
    return {"points": [list(point) for point in sorted(points)]}


def _is_golomb(marks):
    differences = [marks[j] - marks[i]
                   for i in range(len(marks))
                   for j in range(i + 1, len(marks))]
    return len(differences) == len(set(differences))


@lru_cache(maxsize=None)
def _greedy_ruler(mark_count):
    """The small deterministic greedy rulers used as improvement examples."""
    marks = [0]
    for candidate in range(1, MAX_MARK_POSITION + 1):
        if _is_golomb(marks + [candidate]):
            marks.append(candidate)
            if len(marks) == mark_count:
                return tuple(marks)
    raise RuntimeError("GOLOMB_SEED: could not construct a bounded ruler")


def golomb_ruler_improvement_example(pack_id="golomb-ruler"):
    """Return a valid shorter ruler than the pack's default parent."""
    pack = get_pack(pack_id)
    if pack.kind != "golomb-ruler":
        _fail("PACK_KIND", "该题包不是 Golomb 尺。", "This pack is not a Golomb ruler.")
    return {"marks": list(_greedy_ruler(pack.mark_count))}


@dataclass(frozen=True)
class DiscretePack:
    id: str
    kind: str
    dimension: int = 0
    mark_count: int = 0
    version: str = VERSION

    @property
    def pack_id(self):
        """Alias used by existing Four Realms storage and compatibility code."""
        return self.id

    @property
    def title(self):
        if self.kind == "cap-set":
            return f"Cap Set · F3^{self.dimension}"
        return f"Golomb ruler · {self.mark_count} marks / Golomb 尺 · {self.mark_count} 刻度"

    def seed(self):
        if self.kind == "cap-set":
            example = cap_set_improvement_example(self.id)
            return {"points": example["points"][: 2 ** self.dimension]}
        marks = list(_greedy_ruler(self.mark_count))
        # Move the final tick to the nearest larger valid position. This gives
        # each pack a legal, bounded parent that the deterministic sample beats.
        for candidate in range(marks[-1] + 1, min(MAX_MARK_POSITION, marks[-1] + 5000) + 1):
            if _is_golomb(marks[:-1] + [candidate]):
                marks[-1] = candidate
                break
        else:
            raise RuntimeError("GOLOMB_SEED: no improvable parent found")
        return {"marks": marks}

    def evaluate(self, solution):
        if self.kind == "cap-set":
            return self._evaluate_cap(solution)
        return self._evaluate_golomb(solution)

    def _evaluate_cap(self, solution):
        if not isinstance(solution, dict) or set(solution) != {"points"}:
            _fail("CAP_SCHEMA", "方案必须是只含 points 字段的对象。",
                  "Solution must be an object containing only the points field.")
        raw_points = solution["points"]
        maximum = min(3 ** self.dimension, MAX_CAP_POINTS)
        if not isinstance(raw_points, list) or not 1 <= len(raw_points) <= maximum:
            _fail("CAP_POINT_COUNT", f"点数必须为 1 至 {maximum}。",
                  f"Point count must be between 1 and {maximum}.")

        points = []
        seen = set()
        for index, raw in enumerate(raw_points, start=1):
            if not isinstance(raw, list) or len(raw) != self.dimension:
                _fail("CAP_DIMENSION", f"第 {index} 点必须恰有 {self.dimension} 个坐标。",
                      f"Point {index} must have exactly {self.dimension} coordinates.")
            if any(type(value) is not int or value not in (0, 1, 2) for value in raw):
                _fail("CAP_COORDINATE", f"第 {index} 点的坐标必须是 0、1 或 2 的整数。",
                      f"Coordinates of point {index} must be integers 0, 1, or 2.")
            point = tuple(raw)
            if point in seen:
                _fail("CAP_DUPLICATE_POINT", f"第 {index} 点与已有点重复。",
                      f"Point {index} duplicates an existing point.")
            seen.add(point)
            points.append(point)

        # For every distinct pair x,y, the third point on their F_3-line is
        # -(x+y). A point set is a cap exactly when that point is absent.
        for index, left in enumerate(points):
            for right in points[index + 1:]:
                third = tuple((-x - y) % 3 for x, y in zip(left, right))
                if third in seen:
                    _fail("CAP_COLLINEAR", "存在三点共线；第三点为逐坐标 -(x+y) mod 3。",
                          "Three distinct points are collinear; the third point is -(x+y) mod 3 coordinatewise.")

        canonical = sorted(points)
        result = {
            "solution": {"points": [list(point) for point in canonical]},
            "score": len(canonical),
            "total": 3 ** self.dimension,
            "metrics": {
                "dimension": self.dimension,
                "point_count": len(canonical),
                "ambient_points": 3 ** self.dimension,
                "violating_triples": 0,
                "matches_historical_401_reference": (
                    self.dimension == 8 and tuple(canonical) == _cap_reference_points_8d_401()
                ),
            },
        }
        return result

    def _evaluate_golomb(self, solution):
        if not isinstance(solution, dict) or set(solution) != {"marks"}:
            _fail("GOL_SCHEMA", "方案必须是只含 marks 字段的对象。",
                  "Solution must be an object containing only the marks field.")
        marks = solution["marks"]
        if not isinstance(marks, list) or len(marks) != self.mark_count:
            _fail("GOL_MARK_COUNT", f"必须恰有 {self.mark_count} 个刻度。",
                  f"Exactly {self.mark_count} marks are required.")
        if any(type(mark) is not int for mark in marks):
            _fail("GOL_MARK_TYPE", "刻度必须是整数，布尔值不算整数。",
                  "Marks must be integers; booleans are not accepted as integers.")
        if marks[0] != 0 or any(mark < 0 or mark > MAX_MARK_POSITION for mark in marks):
            _fail("GOL_MARK_RANGE", f"首刻度必须为 0，其他刻度须在 0 至 {MAX_MARK_POSITION}。",
                  f"The first mark must be 0; all marks must be in 0..{MAX_MARK_POSITION}.")
        if any(left >= right for left, right in zip(marks, marks[1:])):
            _fail("GOL_MARK_ORDER", "刻度必须严格递增且不能重复。",
                  "Marks must be strictly increasing with no duplicates.")
        if not _is_golomb(marks):
            _fail("GOL_DUPLICATE_DIFFERENCE", "不同刻度对产生了相同差值。",
                  "Two distinct mark pairs produce the same distance.")

        reflected = [marks[-1] - mark for mark in reversed(marks)]
        canonical = min(marks, reflected)
        length = canonical[-1]
        pair_count = self.mark_count * (self.mark_count - 1) // 2
        return {
            "solution": {"marks": list(canonical)},
            "score": GOLUMB_TOTAL - length,
            "total": GOLUMB_TOTAL,
            "metrics": {
                "mark_count": self.mark_count,
                "length": length,
                "unique_differences": pair_count,
                "reflection_canonicalized": canonical != marks,
            },
        }


@lru_cache(maxsize=None)
def get_pack(pack_id="cap-set"):
    """Return a versioned JSON solution pack compatible with the relay Store."""
    if not isinstance(pack_id, str):
        _fail("PACK_ID_TYPE", "题包 ID 必须是文本。", "Pack ID must be a string.")
    if pack_id in _CAP_DIMENSIONS:
        return DiscretePack(pack_id, "cap-set", dimension=_CAP_DIMENSIONS[pack_id])
    if pack_id in _GOLUMB_MARKS:
        return DiscretePack(pack_id, "golomb-ruler", mark_count=_GOLUMB_MARKS[pack_id])
    _fail("PACK_UNKNOWN", f"未知离散题包：{pack_id!r}。", f"Unknown discrete pack: {pack_id!r}.")
