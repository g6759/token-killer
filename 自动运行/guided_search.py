"""Translate bounded model decisions into native, reproducible game candidates."""

import json

from 本地裁判 import get_pack, search_packing_round, search_relay_pair
import research_games


SQUARE_STRATEGIES = ("compact", "boundary", "mixed")
MAX_PROBES = 480


def schema(pack, *, relay_targets=1):
    if pack in research_games.PACKS:
        rules = research_games.get_pack(pack)
        if getattr(rules, "kind", None) == "cap-set":
            point = {"type": "array", "items": {"type": "integer", "enum": [0, 1, 2]},
                     "minItems": rules.dimension, "maxItems": rules.dimension}
            solution = {"type": "object", "properties": {"points": {"type": "array", "items": point,
                         "minItems": 1, "maxItems": min(3 ** rules.dimension, 1024)}},
                        "required": ["points"], "additionalProperties": False}
        elif getattr(rules, "kind", None) == "golomb-ruler":
            solution = {"type": "object", "properties": {"marks": {"type": "array",
                         "items": {"type": "integer", "minimum": 0, "maximum": 1_000_000},
                         "minItems": rules.mark_count, "maxItems": rules.mark_count}},
                        "required": ["marks"], "additionalProperties": False}
        else:
            solution = {"type": "object", "properties": {"initial_x_tick": {"type": "integer",
                         "minimum": 100, "maximum": 200}}, "required": ["initial_x_tick"],
                        "additionalProperties": False}
        properties = {"pack_id": {"type": "string"}, "parent_hash": {"type": "string"},
                      "solution": solution, "handoff": {"type": "string"}}
        return {"type": "object", "properties": properties, "required": list(properties),
                "additionalProperties": False}
    field = "poses" if pack.startswith("square-") else "blocks"
    rows = {"type": "array", "items": {"type": "array", "items": {"type": "integer"}}}
    solution = {"type": "object", "properties": {field: rows}, "required": [field],
                "additionalProperties": False}
    actions = SQUARE_STRATEGIES if field == "poses" else (
        ("cover_two_pairs", "cover_pair") if relay_targets == 2 else ("cover_pair",)
    )
    properties = {
        "pack_id": {"type": "string"}, "parent_hash": {"type": "string"},
        "solution": {"anyOf": [solution, {"type": "null"}]},
        "strategy": {"type": "string", "enum": ["submit", *actions]},
        "pair": {"anyOf": [{"type": "array", "items": {"type": "integer"}}, {"type": "null"}]},
        "handoff": {"type": "string"},
    }
    return {"type": "object", "properties": properties, "required": list(properties),
            "additionalProperties": False}


def instructions(pack, *, relay_targets=1):
    if pack in research_games.PACKS:
        return ("直接提交一个符合题面结构的 solution。保留 pack_id 和 parent_hash，"
                "handoff 写一句可由本机裁判验证的研究理由。")
    direct = "如果已有具体布局或名单，选 submit，填完整 solution，pair 填 null。"
    if pack.startswith("square-"):
        choice = ("也可选 compact（整体压缩）、boundary（重排边界）或 mixed（调整位置与倾角）"
                  "交给本地裁判旁的求解器。使用求解器时 solution 和 pair 都填 null。")
    elif relay_targets == 2:
        choice = ("也可选 cover_two_pairs。挑选两对不同的当前缺口，将四个站号按 [a,b,c,d]"
                  "填进 pair，solution 填 null。本机分别从原存档试修，再由裁判选覆盖更高的试案。"
                  "若只剩一对缺口，可用 cover_pair 并填两个站号。手记简述两次试修能检验的取舍。")
    else:
        choice = ("也可选 cover_pair，从当前未覆盖的站对里挑一对填 pair（两个站号），solution 填 null。"
                  "求解器先尝试补上这对，再用精确计数继续调整。")
    return (direct + choice
            + "本轮用扩展回执格式，保留题包、父档，添加 strategy 和 pair。"
              "handoff 只写与这一改变相关的一句可检验判断，下一轮会给你实际裁判反馈。")


def _dual_pair_repair(pack, parent, turn, pair, seed, stopped):
    if not isinstance(pair, list) or len(pair) != 4 or any(type(x) is not int for x in pair):
        raise ValueError("双目标策略须用四个站号表示两个不同的缺口。")
    targets = [sorted(pair[:2]), sorted(pair[2:])]
    rules = get_pack(pack)
    initial = rules.evaluate(parent["solution"])
    if targets[0] == targets[1] or any(target not in initial["missing"] for target in targets):
        raise ValueError("双目标策略须选两个不同的当前缺口。")

    checked = 0

    def report(count, stage):
        nonlocal checked
        checked = count

    trials, solutions = [], []
    for focus in targets:
        checked = 0
        first, second = focus
        solution, _ = search_relay_pair(pack, parent, turn, (first, second),
                                        f"{seed}:{first}-{second}", report, stopped)
        result = rules.evaluate(solution)
        trials.append({"pair": focus, "score": result["score"], "checked": checked,
                       "targets_covered": sum(target not in result["missing"] for target in targets)})
        solutions.append(result["solution"])
    winner = max(range(len(trials)), key=lambda index: (trials[index]["score"], trials[index]["targets_covered"]))
    recap = "、".join(f"{item['pair'][0]}-{item['pair'][1]} 得 {item['score']} 对" for item in trials)
    return solutions[winner], "双目标从同一父档分别试修，" + recap + "。", trials


def resolve(judge, request, response, stopped):
    proposed = json.loads(response)
    pack = request["pack"]
    if pack in research_games.PACKS:
        if not isinstance(proposed, dict) or proposed.keys() != {"pack_id", "parent_hash", "solution", "handoff"}:
            raise ValueError("研究题回执须包含 pack_id、parent_hash、solution 和 handoff。")
        parent = judge.store.best(pack)
        note = proposed["handoff"]
        if proposed["pack_id"] != pack or proposed["parent_hash"] != parent["hash"]:
            raise ValueError("研究题回执的题包或父档与本轮不符。")
        if not isinstance(note, str) or not note.strip() or len(note) > 300:
            raise ValueError("请用 1 至 300 字写出可检验的研究判断。")
        candidate = {name: proposed[name] for name in ("pack_id", "parent_hash", "solution", "handoff")}
        return {"strategy": "submit", "checked": 0, "candidate": candidate, "hypothesis": note}
    relay_targets = request.get("relay_targets", 1)
    expected_fields = set(schema(pack, relay_targets=relay_targets)["required"])
    if not isinstance(proposed, dict) or proposed.keys() != expected_fields:
        raise ValueError("模型回执缺少候选或策略字段")
    parent = judge.store.best(pack)
    if proposed["pack_id"] != pack or proposed["parent_hash"] != parent["hash"]:
        raise ValueError("模型策略的题包或父档与本轮不符")
    note = proposed["handoff"]
    if not isinstance(note, str) or not note.strip() or len(note) > 300:
        raise ValueError("请用 1 至 300 字写出可检验的研究判断")
    candidate = {name: proposed[name] for name in ("pack_id", "parent_hash", "solution", "handoff")}
    strategy = proposed["strategy"]
    pair = proposed["pair"]
    checked = 0
    outcome = None
    trials = None

    def report(count, stage):
        nonlocal checked
        checked = count

    if strategy == "submit" and pair is None:
        return {"strategy": strategy, "checked": checked, "candidate": candidate, "hypothesis": note}
    if proposed["solution"] is not None:
        raise ValueError("本机搜索策略需要将 solution 留空")
    turn = request["turn"]
    seed = f"{pack}:{parent['hash']}:{request['lane']}:{turn}"
    if pack.startswith("square-") and strategy in SQUARE_STRATEGIES and pair is None:
        solution, outcome = search_packing_round(pack, parent, turn, strategy, seed, MAX_PROBES, report, stopped)
    elif not pack.startswith("square-") and strategy == "cover_two_pairs" and relay_targets == 2:
        solution, outcome, trials = _dual_pair_repair(pack, parent, turn, pair, seed, stopped)
        checked = sum(trial["checked"] for trial in trials)
    elif not pack.startswith("square-") and strategy == "cover_pair":
        if not isinstance(pair, list) or len(pair) != 2 or any(type(x) is not int for x in pair):
            raise ValueError("补齐站对的策略须提供两个不同站号")
        if relay_targets == 2 and len(get_pack(pack).evaluate(parent["solution"])["missing"]) > 1:
            raise ValueError("还有多个缺口，双目标玩法须选两个不同的站对。")
        solution, outcome = search_relay_pair(pack, parent, turn, tuple(sorted(pair)), seed, report, stopped)
    else:
        raise ValueError("所选搜索策略与题包不匹配")
    candidate.update(solution=solution, handoff=note + "。本机实测：" + outcome)
    return {"strategy": strategy, "checked": checked, "candidate": candidate,
            "hypothesis": note, "outcome": outcome, "pair": pair, **({"trials": trials} if trials else {})}
