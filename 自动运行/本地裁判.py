"""Run the original game judges against separate local progress databases."""

import hashlib
import json
from pathlib import Path
import sys


ROOT = Path(__file__).resolve().parent.parent

# The original standalone games import their sibling modules by name.
_original_path = sys.path[:]
try:
    sys.path[:0] = [str(ROOT), str(ROOT / "四境驿站"), str(ROOT / "方格子容纳")]
    from 方格子容纳 import game as packing
    from auto_search import SearchStopped, search_packing_round, search_relay_pair
    from geometry import SCORE_LIMIT, get_square_pack, symmetry_key
    from packs import get_pack
    from relay import prompt_for as relay_prompt
    from relay_store import Store
    from solo_server import SoloServer
    import research_games
finally:
    sys.path[:] = _original_path
    del _original_path


_PACK_ALIASES = {"18": "square-18", "19": "square-19",
                 "x=18": "square-18", "x=19": "square-19"}
_CANDIDATE_FIELDS = {"pack_id", "parent_hash", "solution", "handoff"}


class LocalJudge:
    def __init__(self, directory: Path, pack: str):
        if not isinstance(pack, str):
            raise ValueError("题包编号必须是文本。")
        self.pack = _PACK_ALIASES.get(pack, pack)
        self._packing = self.pack in packing.PACKS
        self._research = self.pack in research_games.PACKS
        self._rules = (get_square_pack(self.pack) if self._packing else
                       research_games.get_pack(self.pack) if self._research else get_pack(self.pack))
        self._upper = (packing.load_reference(self._rules.n, packing.HERE)["upper_bound"]["value_text"]
                       if self._packing else None)
        directory = Path(directory).resolve()
        directory.mkdir(parents=True, exist_ok=True)
        self.store = (packing.PackingServer.build_store(directory / "packing.sqlite3") if self._packing else
                      research_games.build_store(directory / research_games.DATABASE) if self._research else
                      Store(directory / "relay.sqlite3"))

    def task(self) -> str:
        record = self.store.best(self.pack)
        if self._packing:
            return packing.prompt_for(record, self._rules, self._rules.evaluate(record["solution"]), self._upper)
        if self._research:
            state = record["state"]
            description = (getattr(self._rules, "instructions", None)
                           or getattr(self._rules, "prompt", None)
                           or getattr(self._rules, "title", self.pack))
            if getattr(self._rules, "kind", None) == "cap-set":
                description += (f"。在 F3^{self._rules.dimension} 中选点，任意三点不得共线；"
                                "对每一对不同点 x、y，逐坐标 -(x+y) mod 3 得到的第三点不能入集；目标是增加合法点数。")
            elif getattr(self._rules, "kind", None) == "golomb-ruler":
                description += (f"。提供 {self._rules.mark_count} 个严格递增整数刻度，从 0 开始；"
                                "所有两两正差必须互不相同；目标是缩短最后刻度。")
            elif self.pack == "lorenz-divergence":
                description += ("。从 x=initial_x_tick/100 的 (x,1,1) 和 (x+0.00001,1,1) 开始，"
                                "使用 sigma=10、rho=28、beta=8/3 的 Lorenz 方程；寻找两轨迹距离首次达到 1 的更早初值。"
                                "只计双积分器收敛且百分之一模拟时间计分一致的已认证结果。")
            candidate = {"pack_id": self.pack, "parent_hash": record["hash"],
                         "solution": record["solution"], "handoff": ""}
            return (f"研究题：{description}。当前项目成绩 {state['score']} / {state['total']}。"
                    "请提出一个不同且可由本机裁判复算的方案；手记写一句可检验的理由。\n"
                    "只返回完整 JSON 候选：\n" + json.dumps(candidate, ensure_ascii=False, separators=(",", ":")))
        state = record["state"]
        return "" if state["score"] == state["total"] else relay_prompt(record)

    def metric(self) -> dict:
        return self._metric(self.store.best(self.pack))

    def inspect_candidate(self, candidate: dict) -> dict:
        """Verify a layout independently of whether it beats the current record."""
        if not isinstance(candidate, dict) or candidate.keys() != _CANDIDATE_FIELDS:
            raise ValueError("候选须包含 pack_id、parent_hash、solution、handoff 四个字段。")
        current = self.store.best(self.pack)
        if candidate["pack_id"] != self.pack or candidate["parent_hash"] != current["hash"]:
            raise ValueError("候选的题包或父档与本轮不匹配。")
        result = self._rules.evaluate(candidate["solution"])
        if self._research and result.get("certified") is False:
            raise ValueError(result.get("public_status") or "候选尚未通过认证复核，不能计为有效候选。")
        signature = self._signature(result["solution"])
        changed = signature != self._signature(current["solution"])
        fingerprint = hashlib.sha256(json.dumps(signature, separators=(",", ":")).encode()).hexdigest()
        return {"key": fingerprint if changed else None, "score": result["score"],
                **({key: result[key] for key in ("status", "certified", "crossing_time", "display_crossing_time")
                   if key in result} if self._research else {}),
                **({"metrics": result["metrics"]} if self._research and "metrics" in result else {})}

    def grade(self, candidate: dict, author: str) -> dict:
        """Grade against the named parent, then report the best and export the saved chain."""
        before = self.metric()
        try:
            if not isinstance(candidate, dict) or candidate.keys() != _CANDIDATE_FIELDS:
                raise ValueError("候选须包含 pack_id、parent_hash、solution、handoff 四个字段。")
            if candidate["pack_id"] != self.pack:
                raise ValueError(f"候选的题包须为 {self.pack}。")
            if self._research:
                checkpoint = self.store.submit(self.pack, candidate["parent_hash"], author,
                                               candidate["solution"], candidate["handoff"])
                after = self.metric()
                return {"accepted": True,
                        "message": "研究候选已通过原生裁判并加入本地存档。",
                        "new_result": after["hash"] != before["hash"], "before": before,
                        "after": after, "checkpoint": self.store.export(checkpoint["hash"])}
            payload = {"author": author, "candidate": candidate}
            judge = packing.PackingServer.judge if self._packing else SoloServer.judge
            view = judge(self.store, payload)
        except ValueError as error:
            return {"accepted": False, "message": str(error), "new_result": False,
                    "before": before, "after": before, "checkpoint": None}

        # The game returns its best view even when the candidate was saved on a branch.
        # Match the submitted solution to export the actual checkpoint, including repeats.
        result = self._rules.evaluate(candidate["solution"])
        signature = self._signature(result["solution"])
        saved = next(
            (entry for entry in self.store.history(self.pack)
             if entry["state"]["score"] == result["score"]
             and self._signature(entry["solution"]) == signature),
            None,
        )
        if saved is None:
            raise RuntimeError("原生裁判已接受候选，但未能在本地读回候选存档。")
        return {"accepted": True, "message": view["message"], "new_result": view["new_result"],
                "before": before, "after": self.metric(), "checkpoint": self.store.export(saved["hash"])}

    def _signature(self, solution: dict):
        return symmetry_key(solution) if self._packing else solution

    def _metric(self, record: dict) -> dict:
        state = record["state"]
        metric = {"pack_id": self.pack, "hash": record["hash"]}
        if self._packing:
            ticks = SCORE_LIMIT - state["score"]
            metric.update(n=self._rules.n, side_ticks=ticks, **packing.measures(ticks))
        elif self._research:
            result = self._rules.evaluate(record["solution"])
            metric.update(score=state["score"], total=state["total"], complete=state["score"] == state["total"])
            for key in ("status", "certified", "crossing_time", "display_crossing_time"):
                if key in result:
                    metric[key] = result[key]
            if "metrics" in result:
                metric["metrics"] = result["metrics"]
        else:
            score, total = state["score"], state["total"]
            metric.update(score=score, total=total, complete=score == total)
        return metric
