"""Build compact, replayable local-search branches for the 49-station frontier."""

from collections import Counter
from hashlib import sha256
from heapq import heappush, heapreplace
from itertools import combinations
import json
from random import Random

from 本地裁判 import get_pack


PACK_ID = "relay-49-frontier"
MAX_BRANCHES = 192
LARGE_BRANCH_COUNTS = (384, 768, 1152)
MAX_REPAIR_HINTS = 2
MAX_PROMPT_BYTES = 256 * 1024
LARGE_PROMPT_BYTES = {384: 640 * 1024, 768: 1152 * 1024, 1152: 1600 * 1024}


def schema(pack=PACK_ID):
    """Return a strict structured-output schema for branch selection and one swap."""
    if pack != PACK_ID:
        raise ValueError(f"relay_frontier 仅适用于 {PACK_ID}。")
    properties = {
        "pack_id": {"type": "string", "enum": [PACK_ID]},
        "parent_hash": {"type": "string"},
        "branch_id": {"type": "string"},
        "block": {"type": "integer", "minimum": 1, "maximum": 26},
        "remove": {"type": "integer", "minimum": 1, "maximum": 49},
        "add": {"type": "integer", "minimum": 1, "maximum": 49},
        "handoff": {"type": "string"},
    }
    return {"type": "object", "properties": properties,
            "required": list(properties), "additionalProperties": False}


def _validated_parent(pack, parent):
    if pack != PACK_ID:
        raise ValueError(f"relay_frontier 仅适用于 {PACK_ID}。")
    if not isinstance(parent, dict) or not isinstance(parent.get("hash"), str) or not parent["hash"]:
        raise ValueError("父档须提供非空 hash。")
    rules = get_pack(pack)
    measured = rules.evaluate(parent.get("solution"))
    return rules, measured


def _seed(pack, parent_hash, lane, turn, count):
    material = json.dumps([pack, parent_hash, str(lane), turn, count],
                          ensure_ascii=False, separators=(",", ":"))
    return sha256(material.encode("utf-8")).hexdigest()


def _pair_counts(blocks):
    counts = Counter()
    for row in blocks:
        counts.update(combinations(row, 2))
    return counts


def _fragile(counts):
    return [list(pair) for pair, amount in sorted(counts.items()) if amount == 1]


def _fragile_set(counts):
    return {pair for pair, amount in counts.items() if amount == 1}


def _branch_rows(seed, branch_number, base_rows):
    rng = Random(int(seed, 16) ^ (branch_number * 0x9E3779B97F4A7C15))
    rows = [row[:] for row in base_rows]
    edits = []
    steps = 2 + rng.randrange(3)
    for _ in range(steps):
        indices = list(range(len(rows)))
        rng.shuffle(indices)
        applied = False
        for index in indices:
            old = rows[index]
            incoming = [station for station in range(1, 50) if station not in old]
            rng.shuffle(incoming)
            for new_station in incoming:
                for old_station in rng.sample(old, len(old)):
                    new_row = sorted((set(old) - {old_station}) | {new_station})
                    if any(other == new_row for i, other in enumerate(rows) if i != index):
                        continue
                    rows[index] = new_row
                    edits.append({"block": index + 1, "remove": old_station, "add": new_station})
                    applied = True
                    break
                if applied:
                    break
            if applied:
                break
    return rows, edits


def _repair_hints(rows, missing, counts, limit=MAX_REPAIR_HINTS):
    top = []
    missing_set = {tuple(pair) for pair in missing}
    existing_rows = {frozenset(row) for row in rows}
    row_masks = [sum(1 << station for station in row) for row in rows]
    existing_masks = frozenset(row_masks)
    missing_neighbors = [set() for _ in range(50)]
    for first, second in missing_set:
        missing_neighbors[first].add(second)
        missing_neighbors[second].add(first)
    for index, row in enumerate(rows):
        present = frozenset(row)
        other_rows = existing_rows - {present}
        other_masks = existing_masks - {row_masks[index]}
        incoming_stations = set().union(*(missing_neighbors[station] for station in present)) - present
        gain_partners = {}
        for incoming in sorted(incoming_stations):
            gain_partners[incoming] = frozenset(other for other in present
                if counts[(min(incoming, other), max(incoming, other))] == 0)
        loss_partners = {}
        for outgoing in present:
            loss_partners[outgoing] = frozenset(
                other for other in present if other != outgoing
                and counts[(min(outgoing, other), max(outgoing, other))] == 1
            )
        for incoming in sorted(incoming_stations):
            partners = gain_partners[incoming]
            for outgoing in sorted(present):
                new_mask = (row_masks[index] ^ (1 << outgoing)) | (1 << incoming)
                if new_mask in other_masks:
                    continue
                available_gains = partners - {outgoing}
                gain = len(available_gains)
                loss = len(loss_partners[outgoing])
                rank = (gain - loss, gain, -loss, -(index + 1), -outgoing, -incoming)
                record = (rank, index + 1, outgoing, incoming, gain, loss, gain - loss)
                if len(top) < limit:
                    heappush(top, record)
                elif rank > top[0][0]:
                    heapreplace(top, record)
    hints = []
    for _, block, outgoing, incoming, gain, loss, net in sorted(top, reverse=True):
        present = frozenset(rows[block - 1])
        gain_pairs = [tuple(sorted((incoming, other))) for other in sorted(present - {outgoing})
                      if counts[tuple(sorted((incoming, other)))] == 0]
        loss_pairs = [tuple(sorted((outgoing, other))) for other in sorted(present - {outgoing})
                      if counts[tuple(sorted((outgoing, other)))] == 1]
        hints.append({"block": block, "remove": outgoing, "add": incoming,
                      "gain": gain, "loss": loss, "net": net,
                      "fills": [list(pair) for pair in gain_pairs if pair in missing_set],
                      "exposes": [list(pair) for pair in loss_pairs]})
    return hints


def _sampled_repair_hints(rows, missing, new_missing, counts,
                          missing_masks, fragile_masks, parent_hints, parent_removals, limit=6):
    """Score a bounded, useful repair pool against this branch's exact pair counts."""
    def pair(first, second):
        return (first, second) if first < second else (second, first)

    row_masks = [sum(1 << station for station in row) for row in rows]
    masks = set(row_masks)
    candidates = {(h["block"] - 1, h["remove"], h["add"])
                  for h in parent_hints[:8]}
    seen_blocks = set()
    for hint in parent_hints:
        if hint["block"] not in seen_blocks and len(seen_blocks) < 12:
            seen_blocks.add(hint["block"])
            candidates.add((hint["block"] - 1, hint["remove"], hint["add"]))
        if len(seen_blocks) >= 12:
            break

    # The parent ordering chooses candidates; current counts still score them exactly.
    for first, second in sorted(new_missing)[:4]:
        for anchor, incoming in ((first, second), (second, first)):
            options = []
            for index, present in enumerate(rows):
                if anchor not in present or incoming in present:
                    continue
                outgoing = next((station for station in parent_removals[index]
                                 if station in present and station != anchor), None)
                if outgoing is None:
                    outgoing = min(station for station in present if station != anchor)
                options.append((index, outgoing))
            for index, outgoing in options[:1]:
                candidates.add((index, outgoing, incoming))

    top = []
    for index, outgoing, incoming in candidates:
        present = rows[index]
        if outgoing not in present or incoming in present:
            continue
        new_mask = (row_masks[index] ^ (1 << outgoing)) | (1 << incoming)
        if new_mask in masks:
            continue
        partners_mask = row_masks[index] ^ (1 << outgoing)
        gain = (missing_masks[incoming] & partners_mask).bit_count()
        loss = (fragile_masks[outgoing] & partners_mask).bit_count()
        rank = (gain - loss, gain, -loss, -(index + 1), -outgoing, -incoming)
        record = (rank, index + 1, outgoing, incoming, gain, loss, gain - loss)
        if len(top) < limit:
            heappush(top, record)
        elif rank > top[0][0]:
            heapreplace(top, record)
    missing_set = set(missing)
    hints = []
    for _, block, outgoing, incoming, gain, loss, net in sorted(top, reverse=True):
        present = set(rows[block - 1]) - {outgoing}
        hints.append({"block": block, "remove": outgoing, "add": incoming,
                      "gain": gain, "loss": loss, "net": net,
                      "fills": [list(p) for p in sorted(pair(incoming, other) for other in present)
                                if p in missing_set],
                      "exposes": [list(p) for p in sorted(pair(outgoing, other) for other in present)
                                  if counts[p] == 1]})
    return hints


def _describe_branch(rows, edits, branch_number, baseline_score):
    counts = _pair_counts(rows)
    missing = [list(pair) for pair in combinations(range(1, 50), 2) if pair not in counts]
    score = len(counts)
    branch_id = f"b{branch_number:02d}"
    return {
        "branch_id": branch_id,
        "edits": edits,
        "score": score,
        "delta": score - baseline_score,
        "missing": missing,
        "fragile": _fragile_set(counts),
        "repairs": _repair_hints(rows, missing, counts),
    }


def _generate_branches(seed, count, base_rows, repair_pool=None):
    generated = []
    used = set()
    base_signature = tuple(tuple(row) for row in base_rows)
    for branch_number in range(1, count + 1):
        for attempt in range(24):
            branch_seed = f"{int(seed, 16) + attempt:064x}"[-64:]
            branch_index = branch_number + attempt * count
            if repair_pool is None:
                rows, edits = _branch_rows(branch_seed, branch_index, base_rows)
            elif attempt >= 16:
                rows, edits = _deeper_branch_rows(branch_seed, branch_index, base_rows)
            else:
                rows, edits = _guided_branch_rows(branch_seed, branch_index, base_rows, repair_pool)
            signature = tuple(tuple(row) for row in rows)
            if signature not in used and signature != base_signature:
                used.add(signature)
                generated.append((rows, edits))
                break
        else:
            raise ValueError("无法生成互不相同的随机分支。")
    return generated


def _guided_branch_rows(seed, branch_number, base_rows, repair_pool):
    """Combine independently selected, legal frontier repairs on the actual board."""
    rng = Random(int(seed, 16) ^ (branch_number * 0x9E3779B97F4A7C15))
    rows = [row[:] for row in base_rows]
    masks = {sum(1 << station for station in row) for row in rows}
    edits = []
    preferred, all_repairs, _ = repair_pool
    if not preferred:
        return _deeper_branch_rows(seed, branch_number, base_rows)
    pool = preferred if len(preferred) >= 64 else all_repairs
    sampled = [rng.choice(pool) for _ in range(12)]
    sampled.sort(key=lambda hint: hint["net"], reverse=True)
    choices = [rng.choice(preferred)] + sampled
    target_steps = 2
    for hint in choices:
        index = hint["block"] - 1
        outgoing, incoming = hint["remove"], hint["add"]
        row = rows[index]
        if outgoing not in row or incoming in row:
            continue
        old_mask = sum(1 << station for station in row)
        new_mask = (old_mask ^ (1 << outgoing)) | (1 << incoming)
        if new_mask in masks:
            continue
        masks.remove(old_mask)
        masks.add(new_mask)
        rows[index] = sorted((set(row) - {outgoing}) | {incoming})
        edits.append({"block": index + 1, "remove": outgoing, "add": incoming})
        if len(edits) >= target_steps:
            break
    if len(edits) < 2:
        return _deeper_branch_rows(seed, branch_number, base_rows)
    return rows, edits


def _deeper_branch_rows(seed, branch_number, base_rows):
    """Escape a crowded repair neighborhood with real swaps, then repair it."""
    rows, edits = _branch_rows(seed, branch_number, base_rows)
    counts = _pair_counts(rows)
    missing = [list(pair) for pair in combinations(range(1, 50), 2) if pair not in counts]
    hint = _repair_hints(rows, missing, counts, limit=1)
    if hint:
        move = hint[0]
        row = rows[move["block"] - 1]
        row.remove(move["remove"])
        row.append(move["add"])
        row.sort()
        edits.append({key: move[key] for key in ("block", "remove", "add")})
    return rows, edits


def _updated_counts(base_rows, baseline_counts, edits):
    """Replay swaps on their rows and update only the touched station pairs."""
    rows = [row[:] for row in base_rows]
    counts = baseline_counts.copy()
    touched = set()
    for edit in edits:
        row = rows[edit["block"] - 1]
        outgoing, incoming = edit["remove"], edit["add"]
        for station in row:
            if station == outgoing:
                continue
            removed = tuple(sorted((outgoing, station)))
            added = tuple(sorted((incoming, station)))
            counts[removed] -= 1
            counts[added] += 1
            touched.add(removed)
            touched.add(added)
        row.remove(outgoing)
        row.append(incoming)
        row.sort()
    return counts, touched


def _valid_count(count):
    return type(count) is int and (1 <= count <= MAX_BRANCHES or count in LARGE_BRANCH_COUNTS)


def _repair_pool(base_rows, baseline_counts, baseline_missing, count):
    if count <= MAX_BRANCHES:
        return None
    hints = _repair_hints(base_rows, baseline_missing, baseline_counts, limit=10000)
    preferred = [hint for hint in hints if hint["net"] >= -1] or hints[:32]
    parent_removals = []
    for row in base_rows:
        loss = {station: 0 for station in row}
        for first, second in combinations(row, 2):
            if baseline_counts[(first, second)] == 1:
                loss[first] += 1
                loss[second] += 1
        parent_removals.append(sorted(row, key=lambda station: (loss[station], station)))
    return preferred, hints, parent_removals


def build(pack, parent, lane, turn, count):
    """Create independent deterministic branches and compact evidence for choosing one."""
    rules, baseline = _validated_parent(pack, parent)
    if type(turn) is not int or turn < 0:
        raise ValueError("turn 须为非负整数。")
    if not _valid_count(count):
        raise ValueError(f"count 须为 1 至 {MAX_BRANCHES}，或 {LARGE_BRANCH_COUNTS} 中的整数。")
    seed = _seed(pack, parent["hash"], lane, turn, count)
    base_rows = baseline["solution"]["blocks"]
    baseline_counts = _pair_counts(base_rows)
    baseline_missing = {tuple(pair) for pair in baseline["missing"]}
    baseline_fragile = _fragile_set(baseline_counts)
    baseline_missing_masks = [0] * 50
    baseline_fragile_masks = [0] * 50
    if count > MAX_BRANCHES:
        for first, second in baseline_missing:
            baseline_missing_masks[first] |= 1 << second
            baseline_missing_masks[second] |= 1 << first
        for first, second in baseline_fragile:
            baseline_fragile_masks[first] |= 1 << second
            baseline_fragile_masks[second] |= 1 << first
    repair_pool = _repair_pool(base_rows, baseline_counts, baseline["missing"], count)
    branches = []
    for branch_number, (rows, edits) in enumerate(
            _generate_branches(seed, count, base_rows, repair_pool), start=1):
        counts, touched = _updated_counts(base_rows, baseline_counts, edits)
        gap_add = []
        gap_filled = []
        fragile_add = []
        fragile_drop = []
        missing_masks = baseline_missing_masks.copy()
        fragile_masks = baseline_fragile_masks.copy()
        for pair in touched:
            first, second = pair
            if counts[pair] <= 0:
                if pair not in baseline_missing:
                    gap_add.append(pair)
                missing_masks[first] |= 1 << second
                missing_masks[second] |= 1 << first
            else:
                if pair in baseline_missing:
                    gap_filled.append(pair)
                missing_masks[first] &= ~(1 << second)
                missing_masks[second] &= ~(1 << first)
            if counts[pair] == 1:
                if pair not in baseline_fragile:
                    fragile_add.append(pair)
                fragile_masks[first] |= 1 << second
                fragile_masks[second] |= 1 << first
            else:
                if pair in baseline_fragile:
                    fragile_drop.append(pair)
                fragile_masks[first] &= ~(1 << second)
                fragile_masks[second] &= ~(1 << first)
        missing = (baseline_missing | set(gap_add)) - set(gap_filled)
        score = baseline["score"] + len(gap_filled) - len(gap_add)
        branch = {
            "branch_id": f"b{branch_number:02d}", "edits": edits,
            "score": score, "delta": score - baseline["score"],
            "repairs": (_sampled_repair_hints(rows, missing, gap_add, counts,
                                               missing_masks, fragile_masks,
                                               repair_pool[1], repair_pool[2], limit=5)
                        if count > MAX_BRANCHES else _repair_hints(rows, missing, counts)),
        }
        branch["gap_add"] = [list(pair) for pair in sorted(gap_add)]
        branch["gap_filled"] = [list(pair) for pair in sorted(gap_filled)]
        branch["fragile_add"] = [list(pair) for pair in sorted(fragile_add)]
        branch["fragile_drop"] = [list(pair) for pair in sorted(fragile_drop)]
        branches.append(branch)

    task_intro = ("从独立的真实多步修复分支中选一个，再提交一次额外的单站替换。"
                  "大档 repairs 是父档优质动作与分支新缺口动作的抽样，按当前分支精确计分；"
                  "仍可自行提出其他合法单站编辑。"
                  if count > MAX_BRANCHES else
                  "从独立的随机多步分支中选一个，再提交一次额外的单站替换。")
    payload = {
        "task": (task_intro +
                 "block 使用父档名单顺序的 1 至 26 编号；remove 必须在该分支对应名单内，"
                 "add 必须不在其中。gap_add/gap_filled 与 fragile_add/fragile_drop 是相对 baseline 的精确差集；"
                 "repairs 按净收益排序，含可替换位置和真实新增/损失覆盖对。"),
        "baseline": {"score": baseline["score"], "blocks": base_rows,
                     "missing": baseline["missing"],
                     "fragile": _fragile(baseline_counts)},
        "branches": branches,
        "reply_shape": {"pack_id": PACK_ID, "parent_hash": parent["hash"],
                        "branch_id": "b01", "block": 1, "remove": 1, "add": 49,
                        "handoff": "一句可检验判断"},
    }
    context = {"seed": seed, "count": count, "parent_hash": parent["hash"]}
    prompt_text = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
    max_bytes = LARGE_PROMPT_BYTES.get(count, MAX_PROMPT_BYTES)
    if len(prompt_text.encode("utf-8")) > max_bytes:
        raise ValueError(f"题面超过 {max_bytes} UTF-8 字节；请减少 count。")
    return prompt_text, context


def _validate_context(parent, context):
    if (not isinstance(context, dict) or context.keys() != {"seed", "count", "parent_hash"}
            or context["parent_hash"] != parent["hash"]
            or not isinstance(context["seed"], str) or len(context["seed"]) != 64
            or not _valid_count(context["count"])):
        raise ValueError("分支上下文与父档不匹配。")


def _replay_branches(rules, parent, context):
    _validate_context(parent, context)
    base_rows = rules.evaluate(parent["solution"])["solution"]["blocks"]
    baseline_counts = _pair_counts(base_rows)
    baseline_missing = [list(pair) for pair in combinations(range(1, 50), 2)
                        if pair not in baseline_counts]
    repair_pool = _repair_pool(base_rows, baseline_counts, baseline_missing, context["count"])
    return {f"b{index:02d}": rows for index, (rows, _) in enumerate(
        _generate_branches(context["seed"], context["count"], base_rows, repair_pool), start=1)}


def _presented_branch(rules, parent, context, presented_prompt, branch_id):
    """Replay only one branch from the exact prompt already sent to the model."""
    _validate_context(parent, context)
    try:
        payload = json.loads(presented_prompt)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("已发题面须为合法 JSON。") from error
    if not isinstance(payload, dict) or not isinstance(payload.get("branches"), list):
        raise ValueError("已发题面缺少分支。")
    branches = payload["branches"]
    if len(branches) != context["count"]:
        raise ValueError("已发题面分支总数与上下文不符。")
    if (not isinstance(payload.get("reply_shape"), dict)
            or payload["reply_shape"].get("pack_id") != PACK_ID
            or payload["reply_shape"].get("parent_hash") != parent["hash"]):
        raise ValueError("已发题面父档与本轮不符。")
    baseline = rules.evaluate(parent["solution"])
    if (not isinstance(payload.get("baseline"), dict)
            or payload["baseline"].get("blocks") != baseline["solution"]["blocks"]
            or payload["baseline"].get("score") != baseline["score"]):
        raise ValueError("已发题面基线与父档不符。")
    if (not isinstance(branch_id, str) or not branch_id.startswith("b")
            or not branch_id[1:].isdigit()):
        raise ValueError("所选分支编号无效。")
    number = int(branch_id[1:])
    if not 1 <= number <= context["count"] or branch_id != f"b{number:02d}":
        raise ValueError("所选分支编号无效。")
    branch = branches[number - 1]
    if not isinstance(branch, dict) or branch.get("branch_id") != branch_id:
        raise ValueError("已发题面分支编号与选择不符。")
    edits = branch.get("edits")
    if not isinstance(edits, list) or not edits:
        raise ValueError("已发题面分支缺少真实编辑。")
    rows = [row[:] for row in baseline["solution"]["blocks"]]
    for edit in edits:
        if (not isinstance(edit, dict) or edit.keys() != {"block", "remove", "add"}
                or any(type(edit[key]) is not int for key in ("block", "remove", "add"))):
            raise ValueError("已发题面分支编辑格式无效。")
        index, outgoing, incoming = edit["block"] - 1, edit["remove"], edit["add"]
        if not 0 <= index < len(rows) or not 1 <= incoming <= rules.n:
            raise ValueError("已发题面分支编辑越界。")
        row = rows[index]
        if outgoing not in row or incoming in row:
            raise ValueError("已发题面分支编辑不合法。")
        new_row = sorted((set(row) - {outgoing}) | {incoming})
        if any(other == new_row for i, other in enumerate(rows) if i != index):
            raise ValueError("已发题面分支编辑产生重复名单。")
        rows[index] = new_row
    if rules.evaluate({"blocks": rows})["score"] != branch.get("score"):
        raise ValueError("已发题面分支分数与编辑不符。")
    return rows


def resolve(pack, parent, context, response, *, presented_prompt=None):
    """Apply one legal station swap to a replayed branch and re-score with pack rules."""
    rules, baseline = _validated_parent(pack, parent)
    try:
        proposed = json.loads(response)
    except (TypeError, json.JSONDecodeError) as error:
        raise ValueError("回执须为合法 JSON。") from error
    if not isinstance(proposed, dict) or proposed.keys() != set(schema(pack)["required"]):
        raise ValueError("模型回执字段与严格 schema 不匹配。")
    if proposed["pack_id"] != pack:
        raise ValueError("回执题包与本轮不符。")
    if proposed["parent_hash"] != parent["hash"]:
        raise ValueError("回执父档与本轮不符。")
    note = proposed["handoff"]
    if not isinstance(note, str) or not note.strip() or len(note) > 300:
        raise ValueError("handoff 须为 1 至 300 字的可检验判断。")
    branch_id = proposed["branch_id"]
    if presented_prompt is None:
        branches = _replay_branches(rules, parent, context)
        if not isinstance(branch_id, str) or branch_id not in branches:
            raise ValueError("所选分支编号无效。")
        branch_rows = branches[branch_id]
    else:
        branch_rows = _presented_branch(rules, parent, context, presented_prompt, branch_id)
    block_index, outgoing, incoming = proposed["block"], proposed["remove"], proposed["add"]
    if any(type(value) is not int for value in (block_index, outgoing, incoming)):
        raise ValueError("单站替换须使用整数编号。")
    rows = [row[:] for row in branch_rows]
    if not 1 <= block_index <= len(rows):
        raise ValueError("block 编号超出父档名单范围。")
    row = rows[block_index - 1]
    if outgoing not in row or not 1 <= incoming <= rules.n or incoming in row or incoming == outgoing:
        raise ValueError("单站编辑不合法：remove 须在名单内，add 须为不同的缺席站号。")
    row.remove(outgoing)
    row.append(incoming)
    checked = context["count"] + 1
    final = rules.evaluate({"blocks": rows})
    candidate = {"pack_id": pack, "parent_hash": parent["hash"],
                 "solution": final["solution"], "handoff": note}
    outcome = (f"branch={branch_id}; score={final['score']}; baseline={baseline['score']}; "
               f"missing={len(final['missing'])}; extra_swap={block_index}:{outgoing}->{incoming}")
    return {"strategy": "branch_repair", "checked": checked, "candidate": candidate,
            "hypothesis": note, "outcome": outcome}
