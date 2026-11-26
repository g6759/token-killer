"""Offline rehearsal candidates and reports for the two original game judges."""

from decimal import Decimal, InvalidOperation
from itertools import islice
import json
from pathlib import Path
from urllib.parse import quote

from 本地裁判 import ROOT, get_pack, get_square_pack
import research_games
from research_games.discrete import (cap_set_8d_401_reference, cap_set_improvement_example,
                                     golomb_ruler_improvement_example)


_FIELDS = {'pack_id', 'parent_hash', 'solution', 'handoff'}
_TOKEN_KEYS = (
    ('input_tokens', 'inputTokens', '输入'),
    ('cached_input_tokens', 'cachedInputTokens', '缓存输入'),
    ('output_tokens', 'outputTokens', '输出'),
    ('reasoning_output_tokens', 'reasoningOutputTokens', '推理输出'),
    ('total_tokens', 'totalTokens', '总计'),
)


def _candidate_from_task(prompt):
    for line in reversed(prompt.splitlines()):
        if not line.lstrip().startswith('{'):
            continue
        try:
            candidate = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(candidate, dict) and candidate.keys() == _FIELDS:
            return candidate
    raise ValueError('原生题面缺少候选 JSON。')


def _square_candidate(pack, turn):
    rules = get_square_pack(pack)
    file = ROOT / '方格子容纳' / 'reference_layouts.json'
    reference = json.loads(file.read_text(encoding='utf-8'))[pack]
    padding = {1: 2000, 2: 1000, 3: 500, 4: 250, 5: 0}[turn]
    poses = [[x + x * padding // 1_000_000, y + y * padding // 1_000_000, p, q]
             for x, y, p, q in reference['finer']['poses']]
    validated = rules.evaluate({'poses': poses})
    detail = f'将公开精细复现的中心坐标放大 {padding} ppm 后按微单位取整。' if padding else '采用公开精细复现。'
    return validated['solution'], f'{detail} 来源 {reference["source"]["url"]}'


def _station_candidate(pack, turn, base):
    rules = get_pack(pack)
    parent = rules.evaluate(base)
    blocks = tuple(map(tuple, parent['solution']['blocks']))
    existing = set(blocks)

    def legal_swaps():
        for index, block in enumerate(blocks):
            for removed in block:
                for added in range(1, rules.n + 1):
                    if added not in block:
                        changed = tuple(sorted(added if station == removed else station for station in block))
                        if changed not in existing:
                            yield index, removed, added, changed

    move = next(islice(legal_swaps(), turn - 1, turn), None)
    if move is None:
        raise ValueError('父档的合法单站替换数少于演练轮次。')
    index, removed, added, changed = move
    solution = {'blocks': [list(block) for block in blocks]}
    solution['blocks'][index] = list(changed)
    scored = rules.evaluate(solution)
    return scored['solution'], (f'第 {index + 1} 份名单以 {added} 号换下 {removed} 号，'
                                f'覆盖从 {parent["score"]} 对变为 {scored["score"]} 对。')


def rehearsal_candidate(pack, turn, prompt):
    """Produce a reproducible candidate locally, preserving the native task's parent."""
    candidate = _candidate_from_task(prompt)
    pack = {'18': 'square-18', '19': 'square-19', 'x=18': 'square-18', 'x=19': 'square-19'}.get(pack, pack)
    if candidate['pack_id'] != pack:
        raise ValueError(f'原生题面与题包 {pack} 不符。')
    if type(turn) is not int or not 1 <= turn <= 5:
        raise ValueError('离线演练仅包含第 1 至 5 轮。')
    if pack in research_games.PACKS:
        rules = research_games.get_pack(pack)
        if pack.startswith("cap-set"):
            solution = (cap_set_8d_401_reference() if pack == "cap-set-8d"
                        else cap_set_improvement_example(pack))
            detail = "复现固定有限域构造，由原生裁判逐对核验。"
        elif pack.startswith("golomb-ruler"):
            solution = golomb_ruler_improvement_example(pack)
            detail = "提交固定的唯一差值刻度，由原生裁判完整复算。"
        else:
            current = candidate["solution"]["initial_x_tick"]
            solution = {"initial_x_tick": 100 if current == 200 else min(200, current + 1)}
            detail = "改变 Lorenz 初值，由双积分器复核越线时间和认证状态。"
        solution = rules.evaluate(solution)["solution"]
    elif pack.startswith('square-'):
        solution, detail = _square_candidate(pack, turn)
    else:
        solution, detail = _station_candidate(pack, turn, candidate['solution'])
    candidate.update(solution=solution, handoff=f'离线演练第 {turn} 轮。{detail}')
    return candidate


def _decimal(value):
    if value is None or isinstance(value, bool):
        return None
    try:
        number = Decimal(str(value))
    except InvalidOperation:
        return None
    return number if number.is_finite() else None


def _value(value):
    return '—' if value is None else str(value)


def _delta(before, after, unit=''):
    start, end = _decimal(before), _decimal(after)
    if start is None or end is None:
        return '不可计算'
    difference = end - start
    return f'{difference:+f}{unit}' if difference else f'0{unit}'


def _metric(metric):
    if not metric:
        return '—'
    if 'side' in metric and 'area' in metric:
        return f'边长 {metric["side"]}，面积 {metric["area"]}'
    if 'score' in metric and 'total' in metric:
        return f'{metric["score"]:,}/{metric["total"]:,} 对站'
    return '—'


def _metric_change(before, after):
    if not before or not after:
        return '—'
    if 'area' in before and 'area' in after:
        return f'边长 {_delta(before.get("side"), after.get("side"))}，面积 {_delta(before["area"], after["area"])}'
    if 'score' in before and 'score' in after:
        return _delta(before['score'], after['score'], ' 对站')
    return '—'


def _improved(grade):
    if not grade.get('accepted'):
        return False
    before, after = grade.get('before') or {}, grade.get('after') or {}
    if 'side_ticks' in before and 'side_ticks' in after:
        return after['side_ticks'] < before['side_ticks']
    return 'score' in before and 'score' in after and after['score'] > before['score']


def _title(pack):
    if pack.startswith('square-'):
        return f'方格子容纳（{pack.rsplit("-", 1)[1]} 块）'
    return f'四镜驿站（{get_pack(pack).n} 站）' if pack.startswith('relay-') else pack


def _tokens(record, snake, camel):
    usage = (record.get('model') or {}).get('usage') or {}
    return _decimal(usage.get(snake, usage.get(camel)))


def _usage_summary(records):
    values = []
    for snake, camel, label in _TOKEN_KEYS:
        measured = [value for record in records if (value := _tokens(record, snake, camel)) is not None]
        if measured:
            values.append(f'{label} {sum(measured):,}（{len(measured)} 轮）')
        else:
            values.append(f'{label}未记录')
    return '，'.join(values)


def _output_rate(records):
    samples = []
    for record in records:
        tokens = _tokens(record, 'output_tokens', 'outputTokens')
        seconds = _decimal(record.get('model_seconds'))
        if tokens is not None and seconds is not None and seconds > 0:
            samples.append((tokens, seconds))
    if not samples:
        return '—'
    numerator = sum(tokens for tokens, _ in samples)
    denominator = sum(seconds for _, seconds in samples)
    return f'{60 * numerator / denominator:,.2f}（{len(samples)} 轮）'


def _windows(snapshot):
    return {(item.get('minutes'), index if item.get('minutes') is None else None): item
            for index, item in enumerate(snapshot.get('windows') or [], start=1)}


def _percentage(value):
    return '—' if value is None else f'{value}%'


def _snapshot(rows, before, after):
    before, after = before or {}, after or {}
    if not before and not after:
        return False
    rows.append(f'- 观测时间 {_value(before.get("at"))} → {_value(after.get("at"))}。')
    windows_before, windows_after = _windows(before), _windows(after)
    week_key = (10080, None)
    for reading, windows in ((before, windows_before), (after, windows_after)):
        if week_key not in windows and reading.get('weekly_used') is not None:
            windows[week_key] = {'used': reading['weekly_used'], 'reset': None}
    reset = False
    for key in dict.fromkeys((*windows_before, *windows_after)):
        minutes, index = key
        start, end = windows_before.get(key, {}), windows_after.get(key, {})
        start_reset, end_reset = start.get('reset'), end.get('reset')
        used_start, used_end = start.get('used'), end.get('used')
        window = '周套餐' if minutes == 10080 else f'{minutes} 分钟窗口'
        if minutes is None:
            window = f'第 {index} 个窗口（窗口时长未知）'
        detail = f'重置时间标识 {_value(start_reset)} → {_value(end_reset)}'
        if start_reset is None or end_reset is None:
            change = '缺少重置标识，差值不可确认'
        elif start_reset != end_reset:
            reset = True
            change = '窗口重置，差值不可比较'
        elif minutes is None:
            change = '窗口类型未确认，差值不可确认'
        else:
            change = f'差值 {_delta(used_start, used_end, " 个百分点")}'
        rows.append(f'- {window}已用 {_percentage(used_start)} → {_percentage(used_end)}。{detail}，{change}。')
    credit_before, credit_after = before.get('credits'), after.get('credits')
    rows.append(f'- 充值余额（credits） {_value(credit_before)} → {_value(credit_after)}，'
                f'差值 {_delta(credit_before, credit_after)}。')
    return reset


def _cell(value):
    return str(value).replace('|', '\\|').replace('\n', ' ')


def _link(directory, filename):
    if (directory / filename).is_file():
        return f'[{filename}]({quote(filename)})'
    return f'`{filename}`'


def build_report(directory: Path, records: list, plan: dict, note: str) -> str:
    """Render the observed round data and retain unknowns instead of inferring charges."""
    live = plan['mode'] == 'run'
    label = 'Codex 订阅实跑' if live else '离线演练'
    if live and all(record.get('status') == 'ready' for record in records):
        label = '启动核验，尚未发出模型请求'
    rounds = plan['rounds']
    rows = [f'# 游戏每题 {rounds} 轮对照 · {label}', '']
    if live:
        rows += ['用量读数来自 Codex 账户共享窗口。读数差可能包含账户的其他活动和记账延时。']
    else:
        rows += ['离线演练使用历史存档、公开复现和本地候选。未调用模型，未查询额度或充值余额，未测得 token。']
    if note:
        rows += ['', note]
    duration_title = '模型用时合计' if live else '本地候选用时合计'
    rows += ['', f'| 游戏 | 交卷 | 全局严格提升 | 起点 | 终点 | 成果变化 | {duration_title} | 每分钟输出 token |',
             '| --- | ---: | ---: | --- | --- | --- | ---: | ---: |']

    groups = []
    for pack in plan['packs']:
        game = sorted((record for record in records if record['pack'] == pack), key=lambda record: record['turn'])
        grades = [record['grade'] for record in game if record.get('grade') is not None]
        elapsed = [value for record in game if (value := _decimal(record.get('model_seconds'))) is not None]
        places = 2 if live else 4
        timing = f'{sum(elapsed):,.{places}f} 秒（{len(elapsed)} 轮）' if elapsed else '—'
        before = grades[0].get('before') if grades else (game[0].get('metric_before') if game else None)
        after = grades[-1].get('after') if grades else None
        cells = (_title(pack), f'{len(grades)}/{rounds}', sum(map(_improved, grades)),
                 _metric(before), _metric(after), _metric_change(before, after), timing, _output_rate(game) if live else '—')
        rows.append('| ' + ' | '.join(map(str, cells)) + ' |')
        groups.append((pack, game, grades))

    reset_seen = False
    for pack, game, grades in groups:
        rows += ['', f'## {_title(pack)}', '']
        if not game:
            rows.append('没有本次回合记录。')
            continue
        started = next((record['started'] for record in game if record.get('started')), None)
        ended = next((record['ended'] for record in reversed(game) if record.get('ended')), None)
        if started or ended:
            rows += [f'已记录的回合起止时间 {_value(started)} → {_value(ended)}。', '']
        if live:
            rows += [f'各类 token 按有读数的回合加总。{_usage_summary(game)}。', '', '### 额度起止', '']
            if game[0].get('before') or game[-1].get('after'):
                reset_seen |= _snapshot(rows, game[0].get('before'), game[-1].get('after'))
            else:
                rows.append('缺少首轮前和末轮后的额度读数。')
            rows += ['']
        rows += ['| 轮次 | 状态 | 裁判回执 | 局面前 → 后 | 回合记录 |', '| ---: | --- | --- | --- | --- |']
        for record in game:
            turn = record['turn']
            grade = record.get('grade') or {}
            statuses = {'done': '完成', 'uncertain': '中断或结果待确认', 'ready': '未发出',
                        'inflight': '已发出或结果待确认', 'responded': '已取得候选', 'skipped': '跳过'}
            status = statuses.get(record.get('status'), record.get('status', '—'))
            artifact = _link(directory, f'{pack}-{turn:02d}.json')
            result = f'{_metric(grade.get("before"))} → {_metric(grade.get("after"))}' if grade else '—'
            message = grade.get('message') or record.get('error') or '—'
            rows.append('| ' + ' | '.join(map(_cell, (turn, status, message, result, artifact))) + ' |')

        if live:
            for record in game:
                rows += ['', f'### 第 {record["turn"]} 轮用量', '']
                before, after = record.get('before'), record.get('after')
                if before or after:
                    reset_seen |= _snapshot(rows, before, after)
                else:
                    rows.append('- 缺少该轮的前后额度读数。')
                if record.get('delayed_after'):
                    rows.append('- 轮后读数经延时补采。')
                rows.append(f'- Token 回执。{_usage_summary([record])}。')

        checkpoint = f'{pack}-最新存档.json'
        if (directory / checkpoint).is_file():
            rows += ['', f'可换库独立复核的最新存档 {_link(directory, checkpoint)}。']
        elif any(grade.get('checkpoint') for grade in grades):
            rows += ['', '有存档的裁判回执在 `grade.checkpoint` 内含提交的存档和完整祖先链。']
        if grades:
            rows += ['', '回合 JSON 保存题面、候选和本地裁判回执。可用原生裁判按父档重算覆盖或方格几何。']

    rows += ['', '## 对照结论', '']
    if live:
        rows.append('每分钟输出 token 用对应回合的输出 token 总和除以这些回合的模型分钟数。'
                    '输入缓存和推理输出按回执单列。样本量有限，两款游戏的成果单位不同，并且账户额度共享，'
                    '本次只报告观测差异。')
        if reset_seen:
            rows.append('发生窗口重置的区间无法比较套餐扣量。')
        if any(record.get('status') != 'done' for record in records) or any(len(grades) < rounds for _, _, grades in groups):
            rows.append('存在未完成或未确认的回合，额度与效率比较只能覆盖已取得的样本。')
    else:
        rows.append('演练结果仅用于复查交卷、裁判和存档。它不能衡量订阅扣量、模型速度或两款游戏对订阅的使用效率。')
    return '\n'.join(rows) + '\n'
