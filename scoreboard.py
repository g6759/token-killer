"""Export and independently replay the research game's public score proofs."""

import argparse
from contextlib import closing
from html import escape
import json
from pathlib import Path
import sqlite3
import sys


ROOT = Path(__file__).resolve().parent
sys.path[:0] = [str(ROOT / folder) for folder in ("方格子容纳", "四境驿站")]

from geometry import SCORE_LIMIT, get_square_pack
from packing_catalog import CASE_COUNTS
from packs import FRONTIER_SOURCE, get_pack
from relay_store import FORMAT as CHECKPOINT_FORMAT, _ancestry, _digest, _fetch, _verify_chain
import research_games


FORMAT = "research-games-leaderboard/v1"
SQUARES = tuple(f"square-{count}" for count in CASE_COUNTS)
RELAY = "relay-49-frontier"
PACKS = (*SQUARES, RELAY, *research_games.PACKS)


def _rules(pack):
    if pack in SQUARES:
        return get_square_pack(pack)
    if pack == RELAY:
        return get_pack(pack)
    if pack in research_games.PACKS:
        return research_games.get_pack(pack)
    raise ValueError("公开榜不支持此题包。")


def _entry(pack, solution):
    rules = _rules(pack)
    result = rules.evaluate(solution)
    seed = rules.evaluate(rules.seed())
    if result["score"] <= seed["score"]:
        raise ValueError(f"{pack} 的候选须严格超过本项目起点。")
    if pack in SQUARES:
        metric = {key: result[key] for key in ("side_ticks", "exact_side")}
        metric["start_side_ticks"] = seed["side_ticks"]
    elif pack == RELAY:
        metric = {"covered": result["score"], "total": result["total"], "start_covered": seed["score"]}
    else:
        if result.get("certified") is False:
            raise ValueError(f"{pack} 候选未通过认证，不能加入公开榜。")
        metric = {"score": result["score"], "total": result["total"], "start_score": seed["score"],
                  "metrics": result.get("metrics", {}), "start_metrics": seed.get("metrics", {})}
        for key in ("status", "certified", "crossing_time", "display_crossing_time"):
            if key in result:
                metric[key] = result[key]
        if "crossing_time" in seed:
            metric["start_metrics"] = {**metric["start_metrics"], "crossing_time": seed["crossing_time"]}
    clean = result["solution"]
    return {"pack_id": pack, "judge_version": rules.version, "solution_sha256": _digest(clean),
            "metric": metric, "solution": clean}


def _best_chain(db, pack, version):
    row = db.execute("""SELECT hash FROM checkpoints WHERE pack_id=? AND version=?
                        ORDER BY score DESC, hash ASC LIMIT 1""", (pack, version)).fetchone()
    return _ancestry(_fetch(db, row[0]), lambda parent: _fetch(db, parent)) if row else []


def packing_case_progress(workspace, packs):
    """Read compact best-score summaries from the already-validated packing store."""
    path = workspace / "方格子容纳" / "packing.sqlite3"
    if not path.is_file():
        return {}
    result = {}
    try:
        with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
            db.execute("BEGIN")
            for pack in packs:
                chain = _best_chain(db, pack, get_square_pack(pack).version)
                if chain:
                    result[pack] = {"score": _side(SCORE_LIMIT - chain[-1]["state"]["score"]),
                                    "records": len(chain) - 1}
    except (sqlite3.Error, ValueError):
        return None
    return result


def _source_entries(path, packs):
    if not path.is_file():
        return []
    result = []
    with closing(sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True)) as db:
        db.execute("BEGIN")
        for pack in packs:
            rules = _rules(pack)
            chain = _best_chain(db, pack, rules.version)
            if len(chain) < 2:
                continue
            verified = _verify_chain({"format": CHECKPOINT_FORMAT, "tip": chain[-1]["hash"], "chain": chain}, _rules)
            result.append(_entry(pack, verified[-1]["solution"]))
    return result


def collect(workspace):
    squares = _source_entries(workspace / "方格子容纳" / "packing.sqlite3", SQUARES)
    relay = _source_entries(workspace / "四境驿站" / "relay.sqlite3", (RELAY,))
    research = _source_entries(workspace / research_games.DATABASE, research_games.PACKS)
    return {"format": FORMAT, "entries": squares + relay + research}


def verify(proof):
    if (not isinstance(proof, dict) or set(proof) != {"format", "entries"}
            or proof["format"] != FORMAT or not isinstance(proof["entries"], list)
            or len(proof["entries"]) > len(PACKS)):
        raise ValueError("公开成绩文件格式无效。")
    seen = set()
    for row in proof["entries"]:
        if not isinstance(row, dict) or not isinstance(row.get("pack_id"), str) or "solution" not in row:
            raise ValueError("成绩条目缺少有效题包或解文件。")
        pack = row["pack_id"]
        if pack in seen:
            raise ValueError(f"重复的题包 {pack}。")
        seen.add(pack)
        expected = _entry(pack, row["solution"])
        if json.dumps(row, sort_keys=True) != json.dumps(expected, sort_keys=True):
            raise ValueError(f"{pack} 的得分、坐标摘要或裁判版本未通过复算。")
    return proof


def _side(ticks):
    integral, fractional = divmod(ticks, 1_000_000)
    return f"{integral}.{fractional:06d}"


def display(entry):
    metric = entry["metric"]
    pack = entry["pack_id"]
    if pack in SQUARES:
        count = pack.removeprefix("square-")
        return (f"{count} 块方格", _side(metric["start_side_ticks"]), _side(metric["side_ticks"]),
                f"https://squaremath.org/cases/{count}.html")
    if pack == RELAY:
        return ("四十九站覆盖", f"{metric['start_covered']} / {metric['total']}",
                f"{metric['covered']} / {metric['total']}", FRONTIER_SOURCE)
    details = metric.get("metrics", {})
    if pack.startswith("cap-set"):
        dimension = details.get("dimension", pack.removeprefix("cap-set-").removesuffix("d") if pack != "cap-set" else 4)
        return (f"Cap Set · {dimension} 维", f"{metric['start_score']} 点",
                f"{metric['score']} 点", "")
    if pack.startswith("golomb-ruler"):
        marks = details.get("mark_count", pack.removeprefix("golomb-ruler").removeprefix("-") or 8)
        length = details.get("length", metric["score"])
        start_length = metric.get("start_metrics", {}).get("length", metric["start_score"])
        return (f"Golomb 尺 · {marks} 刻度", str(start_length), str(length), "")
    crossing = metric.get("display_crossing_time", metric.get("crossing_time"))
    seed_time = metric.get("start_metrics", {}).get("crossing_time", "—")
    seed_time = f"{seed_time:.2f}" if isinstance(seed_time, (int, float)) else seed_time
    crossing = f"{crossing:.2f}" if isinstance(crossing, (int, float)) else crossing
    return ("Lorenz 双轨分离", f"模拟时间 {seed_time}",
            f"模拟时间 {crossing} · 已认证", "")


def _english_values(entry):
    metric = entry["metric"]
    pack = entry["pack_id"]
    if pack in SQUARES:
        name = f"{pack.removeprefix('square-')} squares"
        return name, display(entry)[1], display(entry)[2], "Public reference ↗"
    if pack == RELAY:
        return "49-station coverage", display(entry)[1], display(entry)[2], "Public reference ↗"
    details = metric.get("metrics", {})
    if pack.startswith("cap-set"):
        dimension = details.get("dimension", pack.removeprefix("cap-set-").removesuffix("d") if pack != "cap-set" else 4)
        return f"Cap Set · {dimension}D", f"{metric['start_score']} points", f"{metric['score']} points", "Local judge"
    if pack.startswith("golomb-ruler"):
        marks = details.get("mark_count", pack.removeprefix("golomb-ruler-") if pack != "golomb-ruler" else 8)
        length = details.get("length", metric["score"])
        start_length = metric.get("start_metrics", {}).get("length", metric["start_score"])
        return f"Golomb ruler · {marks} marks", str(start_length), str(length), "Local judge"
    crossing = metric.get("display_crossing_time", metric.get("crossing_time"))
    seed_time = metric.get("start_metrics", {}).get("crossing_time", "—")
    seed_time = f"{seed_time:.2f}" if isinstance(seed_time, (int, float)) else seed_time
    crossing = f"{crossing:.2f}" if isinstance(crossing, (int, float)) else crossing
    return ("Lorenz trajectory divergence", f"simulation time {seed_time}",
            f"simulation time {crossing} · certified", "Local judge")


def page(proof, *, verifier=False):
    rows = []
    for entry in proof["entries"]:
        name, initial, best, reference = display(entry)
        english_name, initial_en, best_en, reference_en = _english_values(entry)
        reference_html = (f'<a href="{escape(reference, quote=True)}" data-en="{escape(reference_en, quote=True)}">公开基准 ↗</a>'
                          if reference else f'<span data-en="{escape(reference_en, quote=True)}">本机裁判</span>')
        rows.append(f'<tr><th scope="row" data-en="{escape(english_name, quote=True)}">{escape(name)}</th>'
                    f'<td data-en="{escape(initial_en, quote=True)}">{escape(initial)}</td>'
                    f'<td><strong data-en="{escape(best_en, quote=True)}">{escape(best)}</strong></td>'
                    f'<td>{reference_html}</td></tr>')
    empty = '<tr><td colspan="4" data-en="No verified project records yet.">暂无经裁判验收的项目纪录</td></tr>'
    tools = ' · <a href="verifier.zip" download data-en="Download verifier">下载复算程序</a>' if verifier else ""
    return """<!doctype html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>解题实验室 · 项目成绩</title>
<style>
:root{color-scheme:light;font-family:Inter,"Microsoft YaHei UI",sans-serif;color:#23312b;background:#f4f3ec}
body{margin:0}main{width:min(820px,calc(100% - 32px));margin:clamp(28px,7vw,70px) auto}
h1{font-size:clamp(2rem,5vw,3.5rem);margin:0;letter-spacing:-.04em}
p{line-height:1.6;color:#4a5c52}a{color:#25563c;text-underline-offset:3px}
table{width:100%;border-collapse:collapse;background:#fefdf8;font-variant-numeric:tabular-nums}
th,td{text-align:left;padding:.75rem .6rem;border-bottom:1px solid #d9dcd0}
thead{background:#e5ecdf}code{font-size:.9rem;overflow-wrap:anywhere}
nav{display:flex;justify-content:flex-end;gap:.3rem;margin-bottom:.75rem}
nav button{cursor:pointer;border:1px solid #ccd5c9;background:transparent;padding:.4rem .6rem;color:#23312b}
nav button[aria-pressed="true"]{background:#e5ecdf;border-color:#577662;font-weight:700}
@media(max-width:480px){th,td{font-size:.8rem;padding:.65rem .35rem}}
</style></head><body><main>
<nav aria-label="Language / 语言"><button type="button" lang="zh-CN" aria-pressed="true">中文</button><button type="button" lang="en" aria-pressed="false">English</button></nav>
<h1 data-en="Project scores">项目成绩</h1>
<p data-en="Each row shows the best result verified by the exact judge. For square packing, a smaller enclosing square is better.">每行都是经精确裁判复算的项目最好成绩。方格栏为包围正方形边长，数值越小越好。</p>
<table><thead><tr><th scope="col" data-en="Puzzle">关卡</th><th scope="col" data-en="Starting score">项目起点</th><th scope="col" data-en="Best score">项目最好</th><th scope="col" data-en="Reference">数学资料</th></tr></thead>
<tbody>""" + ("".join(rows) or empty) + """</tbody></table>
<p><a href="leaderboard.json" download data-en="Download layouts and coverage proofs">下载布局和覆盖证明</a>""" + tools + """</p>
<p><span data-en="Verify from the project directory">在项目根目录复算</span> <code>python -B scoreboard.py verify public/leaderboard.json</code></p>
<script>
const labels = Array.from(document.querySelectorAll("[data-en]"), el => [el, el.textContent]);
const buttons = document.querySelectorAll("nav button");
function useLanguage(lang) {
  const english = lang === "en";
  document.documentElement.lang = english ? "en" : "zh-CN";
  document.title = english ? "token-killer · Project scores" : "解题实验室 · 项目成绩";
  for (const [element, chinese] of labels) element.textContent = english ? element.dataset.en : chinese;
  for (const button of buttons) button.setAttribute("aria-pressed", String(button.lang === lang));
}
for (const button of buttons) button.addEventListener("click", () => {
  try { localStorage.setItem("research-games-language", button.lang); } catch {}
  useLanguage(button.lang);
});
let preferred;
try { preferred = localStorage.getItem("research-games-language"); } catch {}
useLanguage(preferred || (navigator.language.toLowerCase().startsWith("en") ? "en" : "zh-CN"));
</script>
</main></body></html>
"""


def export(workspace, directory):
    proof = verify(collect(workspace))
    directory.mkdir(parents=True, exist_ok=True)
    text = json.dumps(proof, ensure_ascii=False, indent=2) + "\n"
    (directory / "leaderboard.json").write_text(text, encoding="utf-8")
    (directory / "index.html").write_text(page(proof, verifier=(directory / "verifier.zip").is_file()), encoding="utf-8")
    return proof


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    build = commands.add_parser("export")
    build.add_argument("--workspace", type=Path, default=ROOT)
    build.add_argument("--out", type=Path, default=ROOT / "public")
    check = commands.add_parser("verify")
    check.add_argument("proof", type=Path)
    args = parser.parse_args()
    try:
        if args.command == "export":
            result = export(args.workspace, args.out)
        else:
            result = verify(json.loads(args.proof.read_text(encoding="utf-8")))
    except (OSError, ValueError, sqlite3.Error) as error:
        parser.exit(1, f"成绩复核失败。{error}\n")
    for entry in result["entries"]:
        name, _, best, _ = display(entry)
        print(f"{name} · {best} · 复算通过")


if __name__ == "__main__":
    main()
