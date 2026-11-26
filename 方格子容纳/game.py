"""Play, verify and preserve unit-square packing constructions locally."""
import argparse
from fractions import Fraction
from functools import lru_cache
from html import escape
import json
from pathlib import Path
import sys
from threading import Lock

from geometry import SCALE, SCORE_LIMIT, get_square_pack, symmetry_key
from packing_catalog import PACKS, PACK_COUNTS
from reference_sync import CASE_URL, case_is_open, load_reference, refresh_reference

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent / "四境驿站"))
from relay import read_json
from relay_store import Store, _check_author, _check_handoff, _verify_chain
from solo_server import SoloServer
from packing_batches import PackingBatches
from lite_game import LITE_PACK, PROFILE, ROUNDS, PackingLiteHandler, fast_prompt


TITLE_LEVELS = (
    {"name": "方格学徒", "reason": "从本关公开布局的粗精度复现起步。"},
    {"name": "紧排能手", "reason": "通过精确几何验收，包围面积比本关起始布局更小。"},
    {"name": "微尺制图师", "reason": "认证边长达到公开构造按百万分之一单位向上取整后的数值。"},
    {"name": "边界探路者", "reason": "认证边长严格小于所列公开构造，候选有待独立复核。"},
)


def decimal_units(integer, places):
    whole, part = divmod(integer, 10 ** places)
    return f"{whole}.{part:0{places}d}".rstrip("0").rstrip(".")


def measures(ticks):
    return {"side": decimal_units(ticks, 6), "area": decimal_units(ticks * ticks, 12)}


def below_public_upper(ticks, n):
    if n not in (18, 19):
        threshold = load_reference(n, HERE)["upper_bound"]["comparison_ticks"]
        return threshold is not None and ticks < threshold
    side = Fraction(ticks, SCALE)
    term = 2 * side - 7 if n == 18 else 3 * (side - 3) / 4
    return term < 0 or term * term < (7 if n == 18 else 2)


@lru_cache(maxsize=len(PACKS))
def published_reproduction(pack_id):
    references = json.loads((HERE / "reference_layouts.json").read_text(encoding="utf-8"))
    return symmetry_key(references[pack_id]["finer"])


def prompt_for(record, pack, result, upper):
    state = record["state"]
    sample = {"pack_id": state["pack_id"], "parent_hash": record["hash"],
              "solution": record["solution"], "handoff": "写下实际改动和验证依据"}
    return "\n".join([
        f"方格子容纳：把 {pack.n} 个边长为 1 的正方形装入尽可能小的正方形。允许各块独立旋转、边角接触。",
        f"当前已认证的包围边长为 {measures(result['side_ticks'])['side']}，面积为 {measures(result['side_ticks'])['area']}。",
        f"所列公开构造边长 {upper}。复现公开构造请在手记中注明来源。",
        "每个姿态 [cx,cy,p,q] 有四个整数，中心坐标以百万分之一边长为单位，范围 0..100000000。",
        "p/q 为 tan(θ/2)，0≤p<q≤10000，须约分（轴对齐为 0/1）。"
        "cosθ=(q²−p²)/(q²+p²)，sinθ=2pq/(q²+p²)。",
        "裁判精确验证每一对方块内部不相交，根据所有顶点的横纵跨度自动包框；"
        "边长向上取整到百万分之一再算面积。只有严格缩小包框才保存。",
        "可尝试调整倾角、重排边界附近的方块、联动移动多个中心，用本地程序筛掉碰撞。",
        "上一份交接手记：" + json.dumps(record["handoff"], ensure_ascii=False),
        "保留父档与题号，交回全部姿态和手记组成的一个 JSON 对象。",
        json.dumps(sample, ensure_ascii=False, separators=(",", ":")),
    ])


def game_view(record, sources=None, *, data_dir=HERE):
    stored = record["state"]
    pack_id = stored["pack_id"]
    pack = get_square_pack(pack_id)
    result = pack.evaluate(record["solution"])
    ticks = result["side_ticks"]
    seed_ticks = SCORE_LIMIT - record["lineage"][0]["score"]
    if sources is None:
        sources = load_reference(pack.n, data_dir)
    lower_bound = sources["lower_bound"]
    upper_bound = sources["upper_bound"]
    witness = sources.get("witness") or {}
    lower = lower_bound["value_text"]
    upper = upper_bound["value_text"]
    upper_verified = upper_bound.get("proven") is True
    upper_ticks = upper_bound.get("comparison_ticks") if upper_verified else None
    gap = ticks - upper_ticks if type(upper_ticks) is int else None
    open_case = sources.get("open") is not False
    lineage = [{"author": x["author"] if i else "公开构造 · 粗精度起点", "hash": x["hash"],
                **measures(SCORE_LIMIT - x["score"])} for i, x in enumerate(record["lineage"])]
    achievement = "公开构造起点" if stored["parent_hash"] is None else "本地面积纪录"
    if stored["parent_hash"]:
        arrangement = symmetry_key(result["solution"])
        if (arrangement == published_reproduction(pack_id)
                or witness.get("solution") and arrangement == symmetry_key(witness["solution"])):
            achievement = "公开构造 · 精细复现"
    if gap is not None and gap < 0:
        achievement = "优于所列公开构造的候选 · 待独立复核"
    level = int(ticks < seed_ticks)
    if gap is not None and gap <= 0:
        level = 2 if gap == 0 else 3
    seed = measures(seed_ticks)
    math_url = CASE_URL.format(case=pack.n)
    github_url = f"https://github.com/jlevy/squares/blob/main/packing/witnesses/known-best/n-{pack.n:03d}.yaml"
    verified_ticks = witness.get("side_ticks")
    reported = sources.get("reported_upper_bound") or {}
    reference = {"lower": lower, "upper": upper, "gap_ticks": gap,
                 "upper_verified": upper_verified, "comparison_ticks": upper_ticks, "open": open_case,
                 "url": "https://kingbird.myphotos.cc/packing/squares_in_squares.html",
                 "lower_url": math_url, "math_url": math_url, "github_url": witness.get("source_url") or github_url,
                 "source_status": sources["status"], "checked_at": sources.get("checked_at"),
                 "math_checked_at": upper_bound.get("checked_at"), "github_checked_at": witness.get("checked_at"),
                 "lower_evidence_url": lower_bound.get("evidence_url"),
                 "upper_evidence_url": upper_bound.get("evidence_url"), "github_blob_sha": witness.get("blob_sha"),
                 "reported_upper": reported.get("value_text"),
                 "verified_side": measures(verified_ticks)["side"] if type(verified_ticks) is int else None,
                 "note": "数学库给出边长上下界。GitHub 收录构造坐标，游戏将它转换为整数格布局，再逐对检查方块。"}
    if reference["reported_upper"]:
        reference["note"] += f" 数学库另列更紧的报告值 {reference['reported_upper']}，有待验证。"
    return {"state": {"pack_id": pack_id, "title": f"{pack.n} 块方格", "n": pack.n, "hash": record["hash"],
                      "side_ticks": ticks, **measures(ticks), "seed_area": seed["area"],
                      "seed_side": seed["side"], "exact_side": result["exact_side"]},
            "packs": [{"id": key, "n": count,
                       "open": open_case if key == pack_id else case_is_open(count, data_dir)}
                      for key, count in PACK_COUNTS.items()],
            "solution": result["solution"], "polygons": pack.drawing(result["solution"]),
            "reference": reference, "achievement": achievement, "lineage": lineage,
            "title_levels": TITLE_LEVELS,
            "title_award": {**TITLE_LEVELS[level], "holder": stored["author"] if stored["parent_hash"] else "起始布局"},
            "handoff": record["handoff"], "prompt": prompt_for(record, pack, result, upper)}


def submit(store, payload):
    if not isinstance(payload, dict) or payload.keys() != {"author", "candidate"}:
        raise ValueError("请提供署名和候选")
    candidate = payload["candidate"]
    if not isinstance(candidate, dict) or candidate.keys() != {"pack_id", "parent_hash", "solution", "handoff"}:
        raise ValueError("候选须包含 pack_id、parent_hash、solution、handoff")
    _check_author(payload["author"])
    _check_handoff(candidate["handoff"])
    pack_id = candidate["pack_id"]
    pack = get_square_pack(pack_id)
    parent = store.get(candidate["parent_hash"])
    if parent["state"]["pack_id"] != pack_id:
        raise ValueError("题目与父档不符")
    result = pack.evaluate(candidate["solution"])
    before = SCORE_LIMIT - parent["state"]["score"]
    after = result["side_ticks"]
    if after >= before:
        raise ValueError(f"几何验证通过，边长 {measures(after)['side']}。须小于父档 {measures(before)['side']} 才能存档。")
    history = store.history(pack_id)
    signature = symmetry_key(result["solution"])
    known = any(x["state"]["score"] == result["score"] and symmetry_key(x["solution"]) == signature
                for x in history)
    if not known:
        store.submit(pack_id, parent["hash"], payload["author"], result["solution"], candidate["handoff"])
    best = store.best(pack_id)
    view = game_view(best, data_dir=Path(store.db_path).resolve().parent)
    if known:
        message = "同一布局或其旋转、镜像已有存档，本次复核通过。"
    else:
        kind = "本地纪录" if result["score"] > history[0]["state"]["score"] else "分支纪录"
        message = f"{kind}已保存。相对父档节省面积 {decimal_units(before*before - after*after, 12)}。"
    view.update(message=message, new_result=not known)
    return view


def report(record, history, sources=None, *, data_dir=HERE):
    view = game_view(record, sources, data_dir=data_dir)
    state = view["state"]
    rows = "".join(f"<tr><td>{escape(x['author'])}</td><td>{x['side']}</td><td>{x['area']}</td></tr>"
                   for x in view["lineage"])
    paths = "".join('<polygon points="' + " ".join(f"{x:.8f},{y:.8f}" for x,y in p) + '"/>'
                    for p in view["polygons"])
    side = state["side"]
    return f"""<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>方格子容纳 · 构造证书</title><style>body{{max-width:750px;margin:3em auto;padding:0 1em;font:16px/1.7 system-ui;color:#1d3340;background:#f9f7f0}}
svg{{max-width:28rem;width:100%;border:1px solid #455}}polygon{{fill:#d8e6e4;stroke:#376568;stroke-width:.009}}
td,th{{text-align:left;padding:.3em 1em .3em 0}}code{{overflow-wrap:anywhere}}</style>
<h1>{state['n']} 块单位方格的构造证书</h1><p>{escape(view['achievement'])} · 边长 {side} · 面积 {state['area']}</p>
<p>{escape(view['title_award']['holder'])} · {escape(view['title_award']['name'])}。{escape(view['title_award']['reason'])}</p>
<svg viewBox="0 0 {side} {side}" role="img" aria-label="已复核布局"><g transform="translate(0 {side}) scale(1 -1)">{paths}</g></svg>
<p>{escape(view['handoff'])}</p><table><tr><th>署名</th><th>边长</th><th>面积</th></tr>{rows}</table>
<p>父链上的每一次改善均可从 JSON 存档重验。全部本地分支合计 {len(history)} 个存档。</p><code>{record['hash']}</code></html>"""


class PackingServer(SoloServer):
    game_family = "packing"
    handler = PackingLiteHandler
    supports_neighborhood = False
    page = HERE / "board.html"
    default_pack = "square-18"
    packs = PACKS
    build_store = staticmethod(lambda path: Store(path, resolve_pack=get_square_pack))
    judge = staticmethod(submit)
    job_profiles = {PROFILE: (LITE_PACK, ROUNDS)}

    def __init__(self, db_path, port=8766):
        self.data_dir = Path(db_path).resolve().parent
        self.source_locks = {key: Lock() for key in PACKS}
        super().__init__(db_path, port)
        try:
            self.batches = PackingBatches(self)
            self.lite_usage = self.batches.usage
        except Exception:
            self.server_close()
            raise

    def server_close(self):
        if hasattr(self, "batches"):
            self.batches.close()
        super().server_close()

    def view(self, record):
        return game_view(record, data_dir=self.data_dir)

    def profile_prompt(self, record, job):
        return fast_prompt(self.view(record)["prompt"], job)

    def render_report(self, record, history):
        return report(record, history, data_dir=self.data_dir)

    def sync_reference(self, pack_id):
        pack = get_square_pack(pack_id)
        lock = self.source_locks[pack_id]
        if not lock.acquire(blocking=False):
            raise ValueError("这一关正在核对来源，请稍后查看结果。")
        try:
            sources = refresh_reference(pack.n, self.data_dir)
            witness = sources.get("witness") or {}
            solution = witness.get("solution")
            with self.submit_lock:
                best = self.store.best(pack_id)
                rated = pack.evaluate(best["solution"])
                note = "已重新验算本地成绩。"
                if solution and sources.get("witness_refreshed"):
                    candidate = pack.evaluate(solution)
                    if candidate["side_ticks"] < rated["side_ticks"]:
                        self.judge(self.store, {"author": "Squares Project · 公开构造复现", "candidate": {
                            "pack_id": pack_id, "parent_hash": best["hash"], "solution": candidate["solution"],
                            "handoff": f"复现公开坐标，经本地精确裁判验证。来源 {witness['source_url']}，"
                                       f"Git blob {witness.get('blob_sha', '未提供')}。"}})
                        best = self.store.best(pack_id)
                        note = "上游坐标通过精确几何验收，新的本地成绩已存档。"
                    else:
                        note = "已复算上游布局，本地纪录仍是最佳。"
            result = game_view(best, sources, data_dir=self.data_dir)
            result.update(sync_status=sources["status"], message=" ".join([note, *sources.get("warnings", [])]).strip())
            return result
        finally:
            lock.release()


def main():
    parser = argparse.ArgumentParser(description="方格子容纳 · 本机游戏与独立复核")
    parser.add_argument("--port", type=int, default=8766)
    parser.add_argument("--open", action="store_true", help="游戏就绪后打开浏览器")
    parser.add_argument("--lite", action="store_true", help="打开 26 块方格冲榜页")
    parser.add_argument("--db", default=str(HERE / "packing.sqlite3"))
    sub = parser.add_subparsers(dest="command")
    verify = sub.add_parser("verify", help="从种子独立复核整个 JSON 存档")
    verify.add_argument("file")
    args = parser.parse_args()
    if args.command == "verify":
        document = read_json(args.file, 16 * 1024 * 1024)
        proof = isinstance(document, dict) and set(document) == {"batch_id", "result", "checkpoint"}
        chain = _verify_chain(document["checkpoint"] if proof else document, get_square_pack)
        entry = chain[-1]
        if proof:
            try:
                evidence = document["result"]["evidence"]
                side_ticks = SCORE_LIMIT - entry["state"]["score"]
                if (evidence["hash"] != entry["hash"] or evidence["side_ticks"] != side_ticks
                        or document["result"]["pack_id"] != entry["state"]["pack_id"]):
                    raise ValueError("公开纪录候选的成绩与存档不符。")
            except (KeyError, TypeError):
                raise ValueError("公开纪录候选复核材料格式不符。") from None
        result = measures(SCORE_LIMIT - entry["state"]["score"])
        print(f"{entry['state']['pack_id']} · {len(chain)} 个存档复核通过 · 边长 {result['side']} · 面积 {result['area']}")
        return
    if not 0 <= args.port <= 65535:
        parser.error("端口须为 0 至 65535")
    with PackingServer(args.db, args.port) as server:
        from auto_jobs import publish_origin
        publish_origin(server, "packing")
        print(server.origin, flush=True)
        if args.open:
            import webbrowser
            webbrowser.open(server.origin + ("/lite" if args.lite else ""))
        server.serve_forever()


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
    except (ValueError, OSError) as exc:
        sys.exit(f"操作未完成：{exc}")
