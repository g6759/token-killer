"""Stdlib Lorenz divergence pack: two numerical methods and visible uncertainty.

This is a numerical game certificate, not an interval-arithmetic proof. Version
1 fixes the parameters, 101 initial conditions, threshold, and score resolution.
Animation samples are display data and never determine the certified score.
"""
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import math

PACK_ID = "lorenz-divergence"
SIGMA, RHO, BETA = 10.0, 28.0, 8.0 / 3.0
END_TIME, THRESHOLD, PERTURBATION = 40.0, 1.0, 0.00001
TOTAL = 4000
INITIAL_X_TICKS = tuple(range(100, 201))


def _normalize(solution):
    if not isinstance(solution, dict) or solution.keys() != {"initial_x_tick"}:
        raise ValueError("方案必须只包含 initial_x_tick。")
    tick = solution["initial_x_tick"]
    if type(tick) is not int or not 100 <= tick <= 200:
        raise ValueError("initial_x_tick 必须是 100 至 200 的整数，共 101 个初值。")
    return {"initial_x_tick": tick}


def _initial(tick):
    x = tick / 100.0
    return (x, 1.0, 1.0, x + PERTURBATION, 1.0, 1.0)


def _rhs(state):
    x, y, z, u, v, w = state
    return (SIGMA * (y - x), x * (RHO - z) - y, x * y - BETA * z,
            SIGMA * (v - u), u * (RHO - w) - v, u * v - BETA * w)


def _distance(state):
    return math.sqrt(sum((state[i] - state[i + 3]) ** 2 for i in range(3)))


def _rk4(state, h):
    """Classical fourth-order method; independent from the embedded RK tableau."""
    return _rk4_triplet(*state[:3], h) + _rk4_triplet(*state[3:], h)


def _rk4_triplet(x, y, z, h):
    # Explicit scalar stages avoid allocations in the fine fixed-step audit.
    half = h / 2
    ax, ay, az = SIGMA * (y - x), x * (RHO - z) - y, x * y - BETA * z
    u, v, w = x + half * ax, y + half * ay, z + half * az
    bx, by, bz = SIGMA * (v - u), u * (RHO - w) - v, u * v - BETA * w
    u, v, w = x + half * bx, y + half * by, z + half * bz
    cx, cy, cz = SIGMA * (v - u), u * (RHO - w) - v, u * v - BETA * w
    u, v, w = x + h * cx, y + h * cy, z + h * cz
    dx, dy, dz = SIGMA * (v - u), u * (RHO - w) - v, u * v - BETA * w
    return (x + h * (ax + 2 * bx + 2 * cx + dx) / 6,
            y + h * (ay + 2 * by + 2 * cy + dy) / 6,
            z + h * (az + 2 * bz + 2 * cz + dz) / 6)


# Dormand-Prince 5(4), including the final stage for its embedded estimator.
_A = ((), (1/5,), (3/40, 9/40), (44/45, -56/15, 32/9),
      (19372/6561, -25360/2187, 64448/6561, -212/729),
      (9017/3168, -355/33, 46732/5247, 49/176, -5103/18656),
      (35/384, 0, 500/1113, 125/192, -2187/6784, 11/84))
_B5 = (35/384, 0, 500/1113, 125/192, -2187/6784, 11/84, 0)
_B4 = (5179/57600, 0, 7571/16695, 393/640, -92097/339200, 187/2100, 1/40)


def _dopri(state, h):
    stages = [_rhs(state)]
    for row in _A[1:]:
        stages.append(_rhs(tuple(state[i] + h * sum(c * k[i] for c, k in zip(row, stages))
                                 for i in range(6))))
    result = tuple(state[i] + h * sum(c * k[i] for c, k in zip(_B5, stages))
                   for i in range(6))
    error = tuple(h * sum((a - b) * k[i] for a, b, k in zip(_B5, _B4, stages))
                  for i in range(6))
    return result, error


def _crossing_in_step(state, h, advance):
    """Refine the detected upward crossing by re-integrating from its left end."""
    low, high = 0.0, h
    for _ in range(28):
        middle = (low + high) / 2
        if _distance(advance(state, middle)) >= THRESHOLD:
            high = middle
        else:
            low = middle
    return (low + high) / 2


def _fixed_crossing(tick, step):
    state, t, steps = _initial(tick), 0.0, 0
    while t < END_TIME:
        h = min(step, END_TIME - t)
        result = _rk4(state, h)
        steps += 1
        if _distance(state) < THRESHOLD <= _distance(result):
            return t + _crossing_in_step(state, h, _rk4), steps
        state, t = result, min(END_TIME, steps * step)
    return None, steps


def _adaptive(tick, rtol, targets=None):
    """Adaptive DP5 event integration, or exact requested display sample times."""
    state, t, h, accepted, attempts = _initial(tick), 0.0, 0.002, 0, 0
    samples = [state] if targets is not None else None
    target_index = 1
    while t < END_TIME:
        target = targets[target_index] if targets is not None else END_TIME
        h = min(h, 0.02, target - t)
        if h <= 0 or attempts >= 500000:
            raise ArithmeticError("积分步长或计算预算不足")
        result, error = _dopri(state, h)
        norm = max(abs(e) / (rtol * (0.01 + max(abs(a), abs(b))))
                   for a, b, e in zip(state, result, error))
        if not math.isfinite(norm) or any(not math.isfinite(v) for v in result):
            raise ArithmeticError("积分产生非有限数值")
        attempts += 1
        if norm <= 1:
            accepted += 1
            if targets is None and _distance(state) < THRESHOLD <= _distance(result):
                event = _crossing_in_step(state, h, lambda s, d: _dopri(s, d)[0])
                return t + event, accepted
            state = result
            t += h
            if targets is not None and abs(t - target) <= 1e-12:
                t = target
                samples.append(state)
                target_index += 1
                if target_index == len(targets):
                    return samples, accepted
        h *= 5.0 if norm == 0 else min(5.0, max(0.2, 0.9 * norm ** -0.2))
        if h < 1e-12:
            raise ArithmeticError("积分精度超出浮点可用范围")
    return None, accepted


def _round_tick(time):
    return int(math.floor(time * 100 + 0.5))


def _audit(method, measurements, tolerance):
    times = [entry["crossing_time"] for entry in measurements]
    converged = (len(times) >= 2 and times[-1] is not None and times[-2] is not None
                 and abs(times[-1] - times[-2]) <= tolerance)
    return {"method": method, "measurements": measurements, "converged": converged,
            "crossing_time": times[-1] if times else None,
            "refinement_delta": abs(times[-1] - times[-2]) if converged else None}


def evaluate_solution(solution, *, max_refinements=5):
    """Audit a candidate; max_refinements is a diagnostic budget, not pack config.

    Certification requires both independent methods to converge, mutual agreement
    below 0.001, and a conservative observed-error envelope wholly in one 0.01
    rounding cell. The envelope is empirical, not a rigorous mathematical bound.
    Uncertain candidates score zero and must never be advertised as discoveries.
    """
    normalized = _normalize(solution)
    if type(max_refinements) is not int or not 0 <= max_refinements <= 5:
        raise ValueError("max_refinements 必须为 0 至 5。")
    tick = normalized["initial_x_tick"]
    histories = [[], []]
    result = {"solution": normalized, "score": 0, "total": TOTAL,
              "crossing_time": None, "status": "uncertain", "certified": False,
              "public_status": "未定：尚未通过双方法收敛复核", "audits": [],
              "reason": "需要两种方法及各自的精度收紧复算。",
              "resolution": 0.01, "numerical_interval": None,
              "certification_scope": "双方法数值一致性，非严格误差界或全局最优证明"}
    if max_refinements == 0:
        result["audits"] = [_audit("Dormand-Prince 5(4)", [], 0.00025),
                            _audit("classical RK4", [], 0.00025)]
        return result
    for level in range(max_refinements + 1):
        rtol, step = 1e-9 / (10 ** level), 0.005 / (2 ** level)
        try:
            time_a, steps_a = _adaptive(tick, rtol)
            time_b, steps_b = _fixed_crossing(tick, step)
        except (ArithmeticError, OverflowError) as exc:
            result["reason"] = str(exc)
            return result
        histories[0].append({"rtol": rtol, "crossing_time": time_a, "steps": steps_a})
        histories[1].append({"step": step, "crossing_time": time_b, "steps": steps_b})
        result["audits"] = [_audit("Dormand-Prince 5(4)", histories[0], 0.00025),
                            _audit("classical RK4", histories[1], 0.00025)]
        if time_a is not None:
            result["crossing_time"] = time_a
        if level == 0:
            continue
        if all(entry["crossing_time"] is None for history in histories for entry in history):
            result.update(status="no_crossing", public_status="40 模拟时间内未检测到越线",
                          reason="两种方法及收紧精度均未检测到事件；这不是不存在越线的严格证明。")
            return result
        if not all(a["converged"] for a in result["audits"]):
            result["reason"] = "各方法尚未同时达到 0.00025 模拟时间的收敛阈值。"
            continue
        difference = abs(time_a - time_b)
        error = max(a["refinement_delta"] for a in result["audits"]) * 4 + 2e-7
        low, high = min(time_a, time_b) - error, max(time_a, time_b) + error
        result["numerical_interval"] = [low, high]
        result["method_difference"] = difference
        if difference <= 0.001 and _round_tick(low) == _round_tick(high):
            centisecond = _round_tick(time_a)
            result.update(score=TOTAL - centisecond, status="certified", certified=True,
                          public_status="已认证：双方法收敛且百分之一时间计分一致",
                          reason="两方法及精度收紧的观察误差包络位于同一计分格。",
                          crossing_time_tick=centisecond, display_crossing_time=centisecond / 100)
            return result
        result["reason"] = "两方法差异过大或观察误差跨越 0.01 计分边界，暂不认证。"
    result["public_status"] = "未定：复算预算内未通过收敛或计分边界检查"
    return result


@lru_cache(maxsize=101)
def _evaluated(tick):
    return evaluate_solution({"initial_x_tick": tick})


def sample_trajectory(solution, sample_count=801):
    """Return bounded animation arrays at exact uniform times from 0 through 40.

    A = (x,1,1), B = (x+1e-5,1,1). Display sampling is never reused as an
    event judge. The adaptive integration stops at each requested sampling time.
    """
    normalized = _normalize(solution)
    if type(sample_count) is not int or not 2 <= sample_count <= 2001:
        raise ValueError("sample_count 必须是 2 至 2001 的整数。")
    times = [END_TIME * i / (sample_count - 1) for i in range(sample_count)]
    states, steps = _adaptive(normalized["initial_x_tick"], 1e-11, times)
    return {"solution": normalized, "times": times,
            "trajectory_a": [list(state[:3]) for state in states],
            "trajectory_b": [list(state[3:]) for state in states],
            "distances": [_distance(state) for state in states],
            "threshold": THRESHOLD, "end_time": END_TIME,
            "parameters": {"sigma": SIGMA, "rho": RHO, "beta": BETA},
            "perturbation": PERTURBATION, "method": "Dormand-Prince 5(4)",
            "rtol": 1e-11, "steps": steps, "render_only": True}


@dataclass(frozen=True)
class LorenzPack:
    pack_id: str = PACK_ID
    id: str = PACK_ID
    version: str = "1"
    title: str = "超维递归模拟器 · 两条轨迹何时分开"

    def seed(self):
        return {"initial_x_tick": 200}

    def evaluate(self, solution):
        tick = _normalize(solution)["initial_x_tick"]
        result = deepcopy(_evaluated(tick))
        result["metrics"] = {name: result[name] for name in
                             ("certified", "status", "crossing_time", "resolution")}
        return result

    def sample(self, solution, sample_count=801):
        return sample_trajectory(solution, sample_count)


_PACK = LorenzPack()


def get_pack(pack_id=PACK_ID):
    if pack_id != PACK_ID:
        raise ValueError(f"未知题包：{pack_id!r}")
    return _PACK
