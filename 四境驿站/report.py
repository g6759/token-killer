"""Render an offline HTML record of a construction-track relay."""

from html import escape

from packs import FRONTIER_PACK_ID, FRONTIER_SOURCE, get_pack
from relay_store import credited_hashes

__all__ = ["render"]


_STYLE = """
:root {
  color-scheme: light;
  --paper: #f5f4ec;
  --surface: #fffefa;
  --ink: #1c332e;
  --quiet: #4c625a;
  --line: #d5ddd2;
  --accent: #175249;
  --covered: #d7e9de;
  --covered-ink: #285d49;
  --missing: #f6e0d4;
  --missing-ink: #8a402c;
}
* { box-sizing: border-box; }
body {
  margin: 0;
  background: var(--paper);
  color: var(--ink);
  font: 15px/1.56 "Microsoft YaHei", "PingFang SC", "Noto Sans CJK SC", sans-serif;
}
main { width: min(1140px, calc(100% - 48px)); margin-inline: auto; }
h1, h2, p, ul, ol, dl, dd { margin: 0; }
a { color: inherit; }
:focus-visible { outline: 2px solid var(--accent); outline-offset: 3px; }
.masthead {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 1rem;
  min-height: 65px;
  border-bottom: 1px solid var(--line);
  font-weight: 700;
}
.track { color: var(--quiet); font-size: .8rem; font-weight: 600; }
.record {
  display: grid;
  grid-template-columns: minmax(0, 1fr) minmax(240px, .75fr);
  gap: 1.8rem;
  align-items: center;
  padding: 1.85rem 0;
}
.record-label { color: var(--accent); font-size: .82rem; font-weight: 700; }
h1 { margin-block: .2rem .32rem; font-size: clamp(2rem, 4.3vw, 3.4rem); line-height: 1.15; }
.subline { color: var(--quiet); }
.record-readout {
  padding: .8rem 0 .8rem 1.35rem;
  border-left: 1px solid var(--line);
}
.record-stats { display: grid; grid-template-columns: 1.55fr 1fr 1fr; gap: .9rem; }
.record-stats dt { color: var(--quiet); font-size: .8rem; font-weight: 650; }
.record-stats dd {
  font: 700 clamp(1.35rem, 3.1vw, 2.65rem)/1.15 "Segoe UI", sans-serif;
  font-variant-numeric: tabular-nums;
  margin-top: .35rem;
  white-space: nowrap;
}
.record-stats .denominator { color: var(--quiet); font-size: .48em; font-weight: 500; }
.progress { height: 4px; margin-top: .9rem; border-radius: 8px; background: var(--line); }
.progress span { display: block; width: var(--coverage); height: 100%; border-radius: inherit; background: var(--accent); }
.layout { display: grid; grid-template-columns: minmax(0, 1fr); gap: 1rem; align-items: start; }
.layout--tutorial { grid-template-columns: minmax(0, 1.36fr) minmax(0, 1fr); }
.panel { min-width: 0; padding: 1.15rem 1.25rem; border: 1px solid var(--line); background: var(--surface); border-radius: .75rem; }
.side { display: grid; gap: 1rem; min-width: 0; }
.layout--research .side { grid-template-columns: repeat(2, minmax(0, 1fr)); }
h2 { font-size: 1.18rem; line-height: 1.3; }
.matrix-heading { display: flex; flex-wrap: wrap; align-items: baseline; justify-content: space-between; gap: .5rem 1rem; }
.legend { display: flex; flex-wrap: wrap; gap: .35rem .9rem; padding: 0; list-style: none; font-size: .78rem; color: var(--quiet); }
.legend li { display: flex; align-items: center; gap: .32rem; }
.swatch { display: inline-grid; place-items: center; width: 1rem; height: 1rem; border-radius: 3px; font-weight: 700; }
.hit { color: var(--covered-ink); background: var(--covered); }
.miss { color: var(--missing-ink); background: var(--missing); }
.diag { color: var(--quiet); background: #e8ebe4; }
.matrix-scroll { position: relative; max-width: 100%; overflow: auto; margin-top: .85rem; padding-bottom: .45rem; scrollbar-color: #789784 #e8ede5; }
.layout--research .matrix-scroll { max-height: min(56vh, 35rem); }
.scroll-hint { margin-top: .4rem; color: var(--quiet); font-size: .76rem; }
.matrix {
  border-spacing: 2px;
  font: 600 .7rem/1 "Segoe UI", sans-serif;
  font-variant-numeric: tabular-nums;
  text-align: center;
}
.matrix th, .matrix td { width: 18px; min-width: 18px; height: 18px; padding: 0; }
.matrix th { color: var(--quiet); }
.matrix tbody th { position: sticky; left: 0; z-index: 1; background: var(--surface); }
.matrix--research thead th { position: sticky; top: 0; z-index: 2; background: var(--surface); }
.matrix--research thead th:first-child { left: 0; z-index: 3; }
.matrix td { border-radius: 2px; }
.matrix td.miss { font-size: .8rem; }
.matrix td.blank { background: #f6f6f0; }
.matrix--tutorial { margin-inline: auto; border-spacing: 4px; font-size: .84rem; }
.matrix--tutorial th, .matrix--tutorial td { width: min(7vw, 3.05rem); min-width: 0; height: min(7vw, 3.05rem); }
.matrix--tutorial td { border-radius: 5px; }
.matrix--tutorial td.miss { font-size: 1.05rem; }
.sr-only { position: absolute; width: 1px; height: 1px; padding: 0; overflow: hidden; clip-path: inset(50%); white-space: nowrap; }
.missing { margin-top: .9rem; padding-top: 1rem; border-top: 1px solid var(--line); }
.missing h3 { margin: 0 0 .55rem; font-size: .9rem; }
.missing p { color: var(--quiet); }
.pair-scroll { max-height: 10rem; overflow: auto; }
.pairs { display: flex; flex-wrap: wrap; gap: .4rem; list-style: none; padding: 0 .4rem .2rem 0; }
.pairs li {
  padding: .15rem .45rem;
  min-width: 3.35rem;
  text-align: center;
  background: var(--missing);
  color: var(--missing-ink);
  font: 600 .8rem/1.55 "Segoe UI", sans-serif;
  border-radius: .3rem;
}
.chain, .branches { margin-top: .9rem; padding: 0; list-style: none; }
.entry { min-width: 0; padding: .6rem 0 .8rem 1rem; }
.chain .entry { position: relative; border-left: 1px solid var(--line); }
.chain .entry::before {
  content: "";
  position: absolute;
  top: 1.05rem;
  left: -.25rem;
  width: .44rem;
  height: .44rem;
  border-radius: 50%;
  background: var(--accent);
}
.chain .entry:last-child { padding-bottom: 0; }
.branches .entry { padding-left: 0; }
.branches .entry + .entry { border-top: 1px solid var(--line); }
.entry-top { display: flex; align-items: baseline; justify-content: space-between; gap: .8rem; }
.entry-name { min-width: 0; overflow-wrap: anywhere; font-size: .95rem; font-weight: 750; }
.entry-score { white-space: nowrap; font: 700 .88rem/1.4 "Segoe UI", sans-serif; font-variant-numeric: tabular-nums; }
.entry-score small { color: var(--accent); margin-left: .25rem; font-size: .78rem; }
.entry-note { margin-top: .22rem; color: var(--quiet); white-space: pre-wrap; overflow-wrap: anywhere; font-size: .88rem; }
.entry-details { margin-top: .22rem; color: var(--quiet); font-size: .76rem; overflow-wrap: anywhere; }
.entry-label { padding-left: .45rem; color: var(--accent); white-space: nowrap; font-size: .72rem; }
summary { cursor: pointer; }
.empty { margin-top: .48rem; color: var(--quiet); font-size: .88rem; }
footer { margin: 1.1rem 0 1.8rem; color: var(--quiet); font-size: .76rem; }
@media (max-width: 750px) {
  main { width: calc(100% - 32px); }
  .layout--tutorial, .layout--research .side, .record { grid-template-columns: minmax(0, 1fr); }
  .record { gap: .8rem; padding: 1.25rem 0; }
  .record-readout { border: 0; border-top: 1px solid var(--line); padding: .8rem 0 0; }
  .record-stats dd { font-size: 1.65rem; }
  .panel { padding: 1rem; }
  .layout--research .matrix-scroll { max-height: 47vh; }
  .matrix--tutorial th, .matrix--tutorial td { width: min(8vw, 3.05rem); height: min(8vw, 3.05rem); }
}
@media print {
  @page { margin: 10mm; }
  body { background: white; print-color-adjust: exact; -webkit-print-color-adjust: exact; }
  main { width: 100%; }
  .layout--tutorial, .layout--research .side { grid-template-columns: minmax(0, 1fr); }
  .record { padding-block: 1rem; }
  .layout--research .matrix-scroll, .matrix-scroll, .pair-scroll { max-height: none; overflow: visible; }
  .matrix--research { width: 100%; table-layout: fixed; border-spacing: 1px; font-size: .52rem; }
  .matrix--research th, .matrix--research td { width: auto; min-width: 0; height: 12px; }
  .matrix--research td.miss { font-size: .62rem; }
  .matrix-scroll, .entry { break-inside: avoid; }
  .scroll-hint { display: none; }
}
"""


def _matrix(size: int, missing: set[tuple[int, int]]) -> str:
    stations = range(1, size + 1)
    header = "".join(f'<th scope="col">{station}</th>' for station in stations)
    rows = []
    for left in stations:
        cells = []
        for right in stations:
            if left == right:
                cells.append('<td class="diag"><span aria-hidden="true">–</span>'
                             '<span class="sr-only">同站</span></td>')
            elif left > right:
                cells.append('<td class="blank" aria-hidden="true"></td>')
            elif (left, right) in missing:
                cells.append(f'<td class="miss" title="{left}–{right} 遗漏">×</td>')
            else:
                cells.append(f'<td class="hit" title="{left}–{right} 已覆盖">'
                             '<span class="sr-only">已覆盖</span></td>')
        rows.append(f'<tr><th scope="row">{left}</th>{"".join(cells)}</tr>')
    matrix_size = "tutorial" if size == 7 else "research"
    return (
        f'<table class="matrix matrix--{matrix_size}" aria-label="只显示每对站点一次的覆盖矩阵">'
        f'<thead><tr><th scope="col" aria-label="站号"></th>{header}</tr></thead>'
        f'<tbody>{"".join(rows)}</tbody></table>'
    )


def _entry(record: dict, parent: dict | None, label: str, is_branch: bool) -> str:
    state = record["state"]
    author = "题包种子" if state["parent_hash"] is None else escape(state["author"])
    note = f'<p class="entry-note">{escape(record["handoff"])}</p>' if record["handoff"] else ""
    marker = f'<span class="entry-label">{label}</span>' if label else ""
    parent_note = ""
    delta = ""
    if parent:
        parent_state = parent["state"]
        delta = f'<small>+{state["score"] - parent_state["score"]}</small>'
        if is_branch:
            parent_author = (
                "题包种子" if parent_state["parent_hash"] is None else escape(parent_state["author"])
            )
            parent_note = f'<p class="entry-note">接自 {parent_author} · {parent_state["score"]:,} 对</p>'

    return f"""
    <li class="entry">
      <div class="entry-top">
        <p class="entry-name">{author}{marker}</p>
        <p class="entry-score">{state["score"]:,} 对{delta}</p>
      </div>
      {parent_note}{note}
      <details class="entry-details">
        <summary>存档指纹</summary>
        <code>{escape(record["hash"])}</code>
      </details>
    </li>"""


def render(best: dict, history: list[dict]) -> str:
    """Return a self-contained copy of the current board and its verified history."""
    state = best["state"]
    pack = get_pack(state["pack_id"])
    result = pack.evaluate(best["solution"])
    score, total = result["score"], result["total"]
    missing = result["missing"]
    pack_title = escape(pack.title)
    layout = "tutorial" if pack.n == 7 else "research"
    scroll_hint = '<p class="scroll-hint">在矩阵内滚动可查看其余站点。</p>' if layout == "research" else ""
    pairs = "".join(f"<li>{left}–{right}</li>" for left, right in missing)
    omissions = (
        f'<div class="pair-scroll" role="region" tabindex="0" aria-label="遗漏站对列表">'
        f'<ul class="pairs">{pairs}</ul></div>'
        if pairs else "<p>所有站对均已覆盖。</p>"
    )

    by_hash = {entry["hash"]: entry for entry in history}
    lineage = best["lineage"]
    active_hashes = {step["hash"] for step in lineage}
    verified_unique = credited_hashes(history)

    def entry_markup(record, is_branch):
        key = record["hash"]
        labels = []
        if key == best["hash"]:
            labels.append("本机最高")
        if key not in verified_unique:
            labels.append("同解复现")
        parent = by_hash.get(record["state"]["parent_hash"])
        return _entry(record, parent, " · ".join(labels), is_branch)

    chain = "".join(entry_markup(by_hash[step["hash"]], False) for step in lineage)
    branches = "".join(entry_markup(record, True) for record in history if record["hash"] not in active_hashes)
    branch_list = f'<ul class="branches">{branches}</ul>' if branches else '<p class="empty">暂无其他分支。</p>'
    gain = score - lineage[0]["score"]
    percentage = score * 100 / total
    benchmark = ""
    if state["pack_id"] == FRONTIER_PACK_ID:
        benchmark = (
            '<p class="subline">公开基线为 Michael Dambmann 于 2008 年给出的 27 份名单。'
            f'本关目标为 26 份全覆盖。<a href="{FRONTIER_SOURCE}">La Jolla 覆盖设计库归档</a></p>'
        )

    return f"""<!doctype html>
<html lang="zh-CN">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; base-uri 'none'">
  <title>{pack_title} · 四镜驿站战报</title>
  <style>{_STYLE}</style>
</head>
<body>
<main>
  <header class="masthead">
    <div>四镜驿站</div>
    <div class="track">本机复核战报</div>
  </header>
  <section class="record" aria-labelledby="record-title">
    <div>
      <p class="record-label">本机最高覆盖</p>
      <h1 id="record-title">{pack_title}</h1>
      <p class="subline">{pack.n} 座驿站 · {pack.count} 份名单 · 每份 {pack.k} 座</p>
      {benchmark}
    </div>
    <div class="record-readout">
      <dl class="record-stats">
        <div><dt>覆盖站对</dt><dd>{score:,}<span class="denominator"> / {total:,}</span></dd></div>
        <div><dt>遗漏</dt><dd>{len(missing):,}</dd></div>
        <div><dt>较起点</dt><dd>+{gain:,}</dd></div>
      </dl>
      <div class="progress" role="progressbar" aria-label="覆盖比例" aria-valuemin="0"
           aria-valuemax="{total}" aria-valuenow="{score}" style="--coverage: {percentage:.4f}%"><span></span></div>
    </div>
  </section>
  <div class="layout layout--{layout}">
    <section class="panel" aria-labelledby="matrix-title">
      <div class="matrix-heading">
        <h2 id="matrix-title">站对覆盖矩阵</h2>
        <ul class="legend">
          <li><span class="swatch hit" aria-hidden="true"></span>已覆盖</li>
          <li><span class="swatch miss" aria-hidden="true">×</span>遗漏</li>
        </ul>
      </div>
      <div class="matrix-scroll" role="region" aria-label="站对覆盖矩阵，可滚动" tabindex="0">
        {_matrix(pack.n, set(map(tuple, missing)))}
      </div>
      {scroll_hint}
      <div class="missing">
        <h3>遗漏站对（{len(missing):,}）</h3>
        {omissions}
      </div>
    </section>
    <div class="side">
      <section class="panel" aria-labelledby="chain-title">
        <h2 id="chain-title">接力署名</h2>
        <ol class="chain">{chain}</ol>
      </section>
      <section class="panel" aria-labelledby="branches-title">
        <h2 id="branches-title">其他分支</h2>
        {branch_list}
      </section>
    </div>
  </div>
  <footer>本地裁判复算 · 题包 {escape(state["pack_id"])} · 版本 {escape(state["version"])}</footer>
</main>
</body>
</html>"""
