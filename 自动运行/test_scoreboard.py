from copy import deepcopy
from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from 本地裁判 import LocalJudge
import scoreboard


class ScoreboardTests(unittest.TestCase):
    def test_lorenz_local_judge_record_exports_recomputable_certification_metrics(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            judge = LocalJudge(root, "lorenz-divergence")
            candidate = {"pack_id": judge.pack, "parent_hash": judge.metric()["hash"],
                         "solution": {"initial_x_tick": 100}, "handoff": "较早越过分离阈值。"}

            checked = judge.inspect_candidate(candidate)
            receipt = judge.grade(candidate, "本地测试")
            proof = scoreboard.collect(root)

            self.assertTrue(checked["certified"])
            self.assertTrue(receipt["accepted"])
            self.assertTrue(receipt["new_result"])
            self.assertEqual(proof["entries"][0]["pack_id"], "lorenz-divergence")
            self.assertTrue(proof["entries"][0]["metric"]["certified"])
            self.assertAlmostEqual(proof["entries"][0]["metric"]["crossing_time"], 21.5564, places=3)
            self.assertEqual(scoreboard.verify(proof), proof)

    def test_exports_verified_improvement_without_local_notes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            judge = LocalJudge(root / "方格子容纳", "square-26")
            self.assertEqual(scoreboard.collect(root)["entries"], [])
            designs = scoreboard.ROOT / "方格子容纳" / "reference_layouts.json"
            solution = json.loads(designs.read_text(encoding="utf-8"))["square-26"]["finer"]
            receipt = judge.grade({
                "pack_id": "square-26", "parent_hash": judge.metric()["hash"],
                "solution": solution, "handoff": "PRIVATE-HANDOFF",
            }, "PRIVATE-AUTHOR")
            self.assertTrue(receipt["new_result"])
            output = root / "public"
            summary = scoreboard.export(root, output)
            self.assertEqual(summary["entries"][0]["pack_id"], "square-26")
            upload_text = "\n".join(file.read_text(encoding="utf-8") for file in output.iterdir())
            self.assertNotIn("PRIVATE-", upload_text)
            self.assertNotIn(receipt["after"]["hash"], upload_text)
            self.assertIn("公开基准", upload_text)
            self.assertEqual(scoreboard.verify(summary), summary)
            shared = LocalJudge(root / "shared", "square-26")
            with ThreadPoolExecutor(max_workers=2) as pool:
                results = list(pool.map(lambda _: shared.store.ingest(receipt["checkpoint"], improvement_only=True),
                                        range(2)))
            self.assertEqual([r["hash"] for r in results if r is not None], [receipt["after"]["hash"]])
            self.assertIsNone(shared.store.ingest(receipt["checkpoint"], improvement_only=True))

            bad_claim = deepcopy(summary)
            bad_claim["entries"][0]["metric"]["side_ticks"] -= 1
            with self.assertRaises(ValueError):
                scoreboard.verify(bad_claim)
            collision = deepcopy(summary)
            poses = collision["entries"][0]["solution"]["poses"]
            poses[1] = poses[0]
            with self.assertRaises(ValueError):
                scoreboard.verify(collision)


if __name__ == "__main__":
    unittest.main()
