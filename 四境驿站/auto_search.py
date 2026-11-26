"""Bounded, reproducible candidate search using only the active local checkpoint."""

from fractions import Fraction
from itertools import combinations
from pathlib import Path
from random import Random
import sys

if __package__:
    from .packs import get_pack
else:
    from packs import get_pack

# The games can be started as separate scripts, without the project root on sys.path.
_project_root = str(Path(__file__).resolve().parent.parent)
sys.path.insert(0, _project_root)
try:
    from 方格子容纳 import geometry
except ModuleNotFoundError as error:
    if error.name != "方格子容纳":
        raise
    geometry = None
finally:
    sys.path.pop(0)


class SearchStopped(RuntimeError):
    """A stop request arrived before the candidate was ready."""


def _check_stop(stopped):
    if stopped():
        raise SearchStopped("本地搜索已停止")


def next_candidate(pack_id, record, turn, stopped=lambda: False) -> dict:
    """Search without changing the store and return one candidate for its native judge."""
    _check_stop(stopped)
    if type(turn) is not int or turn < 1:
        raise ValueError("搜索轮号须为正整数")
    if record["state"]["pack_id"] != pack_id:
        raise ValueError("搜索题包与当前存档不符")
    rng = Random(f"{pack_id}:{record['hash']}:{turn}")
    if pack_id.startswith("square-"):
        if geometry is None:
            raise ValueError("找不到方格子容纳的原生裁判")
        solution, detail = _search_squares(pack_id, record, turn, rng, stopped)
    else:
        solution, detail = _search_relay(pack_id, record, turn, rng, stopped)
    _check_stop(stopped)
    return {"pack_id": pack_id, "parent_hash": record["hash"],
            "solution": solution, "handoff": f"本机自动搜索第 {turn} 轮。{detail}"}


def _relay_moves(blocks, n, stopped):
    """Enumerate all legal one-station replacements, using exact coverage deltas."""
    counts = [[0] * (n + 1) for _ in range(n + 1)]
    bit = [1 << station for station in range(n + 1)]
    masks = [sum(bit[station] for station in block) for block in blocks]
    occupied = set(masks)
    for block in blocks:
        for first, second in combinations(block, 2):
            counts[first][second] += 1
            counts[second][first] += 1

    moves = []
    for index, block in enumerate(blocks):
        _check_stop(stopped)
        mask = masks[index]
        outside = [station for station in range(1, n + 1) if not mask & bit[station]]
        losses = {station: sum(counts[station][other] == 1 for other in block)
                  for station in block}
        gains = {station: sum(counts[station][other] == 0 for other in block)
                 for station in outside}
        for removed in block:
            kept_mask = mask ^ bit[removed]
            for added in outside:
                if (kept_mask | bit[added]) not in occupied:
                    gain = gains[added] - (counts[added][removed] == 0)
                    moves.append((gain - losses[removed], index, removed, added))
    moves.sort(key=lambda move: -move[0])
    return moves


def _relay_child(blocks, move):
    _, index, removed, added = move
    updated = tuple(sorted([station for station in blocks[index] if station != removed] + [added]))
    return tuple(sorted((*blocks[:index], updated, *blocks[index + 1:])))


def _search_relay(pack_id, record, turn, rng, stopped, *, focus_pair=None, report=None):
    pack = get_pack(pack_id)
    parent = pack.evaluate(record["solution"])
    root = tuple(map(tuple, parent["solution"]["blocks"]))
    root_moves = _relay_moves(root, pack.n, stopped)
    if not root_moves:
        raise ValueError("当前存档没有合法的单站替换")
    evaluated = len(root_moves)
    if focus_pair is not None:
        if list(focus_pair) not in parent["missing"]:
            raise ValueError("模型选择的站对不在当前缺口中")
        first, second = focus_pair
        root_moves = [move for move in root_moves if (
            (move[3] == first and second in root[move[1]] and move[2] != second)
            or (move[3] == second and first in root[move[1]] and move[2] != first))]
        if not root_moves:
            raise ValueError("选定站对没有可用的首步替换")

    first_score = parent["score"]
    best, best_score = root, first_score
    visited = {root}
    top_delta = root_moves[0][0]
    by_delta = {}
    for move in root_moves:
        by_delta.setdefault(move[0], []).append(move)
    root_choices = [root_moves[(turn - 1) % len(by_delta[top_delta])]]
    for depth_band in (0, 0, 1, 0, 2, 1, 0, 2, 1, 0, 2):
        root_choices.append(rng.choice(by_delta.get(top_delta - depth_band, by_delta[top_delta])))

    for start in root_choices:
        _check_stop(stopped)
        blocks = _relay_child(root, start)
        score = first_score + start[0]
        if blocks in visited:
            continue
        for depth in range(7):
            visited.add(blocks)
            if score > best_score:
                best, best_score = blocks, score
            if score == parent["total"] or depth == 6:
                break
            moves = _relay_moves(blocks, pack.n, stopped)
            evaluated += len(moves)
            if not moves:
                break
            threshold = moves[0][0] - (depth % 3 == 1)
            preferred = [move for move in moves if move[0] >= threshold]
            rng.shuffle(preferred)
            for move in preferred:
                child = _relay_child(blocks, move)
                if child not in visited:
                    blocks, score = child, score + move[0]
                    break
            else:
                break
        if best_score == parent["total"]:
            break

    expected = best_score
    outcome = "找到更高覆盖"
    if best_score == first_score:
        # Each round at this parent falls back to a different legal replacement.
        fallback = root_moves[(turn - 1) % len(root_moves)]
        best = _relay_child(root, fallback)
        expected += fallback[0]
        outcome = "未找到提分，回交不同的合法替换供裁判登记"
    result = pack.evaluate({"blocks": [list(block) for block in best]})
    if result["score"] != expected:
        raise RuntimeError("邻域增量分数与原生覆盖裁判不一致")
    if report is not None:
        report(evaluated, "检查单站替换与连续换组")
    return (result["solution"],
            f"首步穷举 {len(root_moves)} 种合法替换，最多连续调整七步，"
            f"共评估 {evaluated} 次替换；{outcome}，覆盖 {first_score} → {result['score']} 对。")


def _square_radius(pose):
    p, q = pose[2:]
    return Fraction(geometry.SCALE * (q * q - p * p + 2 * p * q), 2 * (q * q + p * p))


def _square_spans(poses):
    radii = [_square_radius(pose) for pose in poses]
    return tuple(
        max(pose[axis] + radius for pose, radius in zip(poses, radii))
        - min(pose[axis] - radius for pose, radius in zip(poses, radii))
        for axis in (0, 1)
    )


def _square_merit(poses):
    spans = _square_spans(poses)
    return max(spans), sum(spans)


def _square_apart(poses):
    squares = [geometry._Square.from_pose(pose) for pose in poses]
    return all(
        geometry._has_separating_axis(first, second) or geometry._has_separating_axis(second, first)
        for first, second in combinations(squares, 2)
    )


def _largest_safe_move(poses, step_limit, transform, stopped,
                       on_check=lambda: None, available=lambda: True):
    """Keep the last verified step even if rounding interrupts a feasible interval."""
    best = poses
    lower, upper = 1, step_limit
    while lower <= upper and available():
        _check_stop(stopped)
        step = (lower + upper) // 2
        trial = transform(step)
        feasible = all(max(pose[:2]) <= geometry.MAX_CENTER for pose in trial) and _square_apart(trial)
        on_check()
        if feasible:
            best = trial
            lower = step + 1
        else:
            upper = step - 1
    return best


def _scaled_poses(poses, axes, step):
    scale = geometry.SCALE
    trial = [pose[:] for pose in poses]
    for pose in trial:
        for axis in axes:
            pose[axis] = (pose[axis] * (scale + step) + scale // 2) // scale
    return trial


def _edge_move(poses, axis, edge, outermost, step):
    trial = [pose[:] for pose in poses]
    for index in outermost:
        trial[index][axis] -= edge * step
    min_center = min(pose[axis] for pose in trial)
    for pose in trial:
        pose[axis] -= min_center
    return trial


def _search_squares(pack_id, record, turn, rng, stopped, *,
                    work_limit=None, strategy="mixed", report=None):
    pack = geometry.get_square_pack(pack_id)
    parent = pack.evaluate(record["solution"])
    original = parent["solution"]["poses"]
    best, merit = original, _square_merit(original)
    probes = 0

    def available():
        return work_limit is None or probes < work_limit

    def checked(stage):
        nonlocal probes
        probes += 1
        if report is not None:
            report(probes, stage)

    def attempt(transform, limit, stage):
        nonlocal best, merit, probes
        moved = _largest_safe_move(best, limit, transform, stopped,
                                   lambda: checked(stage), available)
        moved_merit = _square_merit(moved)
        if moved_merit < merit:
            best, merit = moved, moved_merit

    scale = geometry.SCALE
    axes = [(0,), (1,)]
    rng.shuffle(axes)
    edges = [(axis, edge, count) for axis in (0, 1) for edge in (-1, 1) for count in (1, 2, 3, 5)]
    if strategy != "boundary":
        for selected in [(0, 1), *axes]:
            attempt(lambda step: _scaled_poses(best, selected, -step), scale // 5, "压缩整体间距")
    for _ in range(2 if work_limit is None else 1):
        rng.shuffle(edges)
        for axis, edge, count in edges:
            if not available():
                break

            def edge_coordinate(index):
                return edge * best[index][axis] + _square_radius(best[index])

            outermost = sorted(range(len(best)), key=edge_coordinate, reverse=True)[:count]
            limit = scale // 10 if work_limit is None else rng.randrange(scale // 25, scale // 8)
            attempt(lambda step: _edge_move(best, axis, edge, outermost, step), limit, "联动移动边界方格")

    # Keep validating actual candidates against a fixed work budget. The seed
    # changes the move directions, magnitudes and tile choices on each round.
    while work_limit is not None and available():
        _check_stop(stopped)
        trial, stage = _packing_trial(best, rng, strategy)
        feasible = _square_apart(trial)
        checked(stage)
        if feasible:
            candidate_merit = _square_merit(trial)
            if candidate_merit < merit:
                best, merit = trial, candidate_merit
    verified = pack.evaluate({"poses": best})
    before, after = parent["side_ticks"], verified["side_ticks"]
    if after < before:
        note = f"精确缩小包围边长 {before} → {after} 微单位"
    elif work_limit is None:
        # On a plateau, try a small outward spacing trial. Increasing the
        # spacing by round makes the resulting canonical trials distinguishable.
        for multiplier in (1, 2, 4, 8):
            _check_stop(stopped)
            spaced = _scaled_poses(original, (0, 1), 16 * turn * multiplier)
            try:
                fallback = pack.evaluate({"poses": spaced})
            except ValueError:
                continue
            if fallback["solution"] != parent["solution"]:
                verified = fallback
                break
        else:
            raise ValueError("本轮未缩小包围框，也没有找到可提交的不同合法布局")
        note = (f"未缩小包围框，回交本轮合法间距试移供裁判登记，边长 "
                f"{before} → {verified['side_ticks']} 微单位")
    else:
        note = "本轮最优合法布局未缩小包围框"
    return verified["solution"], f"按父档收缩坐标，尝试边界方格联动；共精确检查 {probes} 次，{note}。"


def _packing_trial(poses, rng, strategy):
    axis = rng.randrange(2)
    step = rng.randrange(1, rng.choice((25, 300, 3000, 30_000)))
    edge = rng.choice((-1, 1))
    if strategy == "compact" and rng.randrange(4) == 0:
        selected = (0, 1) if rng.randrange(3) == 0 else (axis,)
        return _scaled_poses(poses, selected, -step), "尝试不同尺度的整体压缩"

    if strategy == "boundary" or rng.randrange(4) == 0:
        indices = sorted(range(len(poses)), key=lambda i: poses[i][axis] * edge, reverse=True)
        window = rng.randrange(2, min(8, len(poses)))
        selected = rng.sample(indices[:window], rng.randrange(1, window + 1))
        return _edge_move(poses, axis, edge, selected, step), "重排边界方格"

    trial = [pose[:] for pose in poses]
    index = rng.randrange(len(trial))
    if strategy == "mixed" and rng.randrange(3) == 0:
        p, q = trial[index][2:]
        angle = Fraction(p, q) + Fraction(edge * rng.randrange(1, 6), rng.choice((200, 2000, 10000)))
        if angle < 0:
            angle = (1 + angle) / (1 - angle)
        elif angle >= 1:
            angle = (angle - 1) / (angle + 1)
        angle = angle.limit_denominator(geometry.MAX_DENOMINATOR)
        if angle == 1:
            angle = Fraction(0)
        trial[index][2:] = [angle.numerator, angle.denominator]
        return trial, "微调方格倾角"
    return _edge_move(trial, axis, edge, [index], step), "微调方格位置"


def search_packing_round(pack_id, parent, turn, strategy, seed, limit, report, stopped):
    """Run a bounded search in an isolated process. The server judges the result."""
    return _search_squares(pack_id, parent, turn, Random(seed), stopped,
                           work_limit=limit, strategy=strategy, report=report)


def search_relay_pair(pack_id, parent, turn, pair, seed, report, stopped):
    """Start the existing neighborhood search by trying to cover a selected gap."""
    return _search_relay(pack_id, parent, turn, Random(seed), stopped, focus_pair=pair, report=report)
