import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from 本地裁判 import LocalJudge


ROOT = Path(__file__).resolve().parent.parent


class LocalJudgeTests(unittest.TestCase):
    def setUp(self):
        temporary = TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name)

    def candidate(self, judge):
        task = judge.task()
        start = task.index('{"pack_id"')
        return json.loads(task[start:])

    def test_square_cut_in_both_packs_has_independently_replayable_checkpoint(self):
        layouts = json.loads((ROOT / "方格子容纳" / "reference_layouts.json").read_text(encoding="utf-8"))
        for pack, count in (("x=18", 18), ("x=19", 19)):
            with self.subTest(pack=pack):
                judge = LocalJudge(self.root, pack)
                candidate = self.candidate(judge)
                self.assertEqual(candidate["pack_id"], f"square-{count}")
                candidate["solution"] = layouts[candidate["pack_id"]]["finer"]
                candidate["handoff"] = "复核公开构造。"
                graded = json.loads(json.dumps(judge.grade(candidate, "本地测试")))
                self.assertTrue(graded["accepted"], graded["message"])
                self.assertTrue(graded["new_result"])
                self.assertLess(graded["after"]["side_ticks"], graded["before"]["side_ticks"])
                self.assertEqual(graded["after"], LocalJudge(self.root, pack).metric())
                witness = LocalJudge(self.root / "verify", pack)
                self.assertEqual(witness.store.ingest(graded["checkpoint"])["hash"], graded["after"]["hash"])

                repeat = judge.grade(candidate, "本地测试")
                self.assertTrue(repeat["accepted"], repeat["message"])
                self.assertFalse(repeat["new_result"])
                self.assertEqual(repeat["checkpoint"], graded["checkpoint"])
                self.assertEqual(repeat["before"], repeat["after"])
                self.assertEqual(len(judge.store.history(candidate["pack_id"])), 2)

    def test_frontier_uses_its_own_store_and_rejects_non_improvement(self):
        square = LocalJudge(self.root, "square-19")
        relay = LocalJudge(self.root, "relay-49-frontier")
        score = relay.metric()
        self.assertEqual((score["score"], score["total"]), (1160, 1176))
        self.assertEqual(set(path.name for path in self.root.iterdir()), {"packing.sqlite3", "relay.sqlite3"})

        same = relay.grade(self.candidate(relay), "本地测试")
        self.assertFalse(same["accepted"])
        self.assertIsNone(same["checkpoint"])
        self.assertIn("未高于父档", same["message"])
        self.assertEqual((same["before"], same["after"]), (score, score))

        wrong_pack = relay.grade(self.candidate(square), "本地测试")
        self.assertFalse(wrong_pack["accepted"])
        self.assertIn("题包", wrong_pack["message"])
        self.assertEqual(relay.metric(), score)

    def test_research_pack_uses_shared_research_store_and_reports_native_metric(self):
        judge = LocalJudge(self.root, "lorenz-divergence")

        metric = judge.metric()
        task = judge.task()

        self.assertEqual(judge.store.db_path, str((self.root / "研究题" / "records.sqlite3").resolve()))
        self.assertTrue(metric["certified"])
        self.assertAlmostEqual(metric["crossing_time"], 23.6955, places=3)
        self.assertIn('"pack_id":"lorenz-divergence"', task)
        self.assertIn(metric["hash"], task)

    def test_offline_reference_candidates_pass_all_research_pack_judges(self):
        from evidence import rehearsal_candidate
        from research_games import PACKS

        for pack in PACKS:
            with self.subTest(pack=pack):
                judge = LocalJudge(self.root, pack)
                candidate = rehearsal_candidate(pack, 1, judge.task())
                checked = judge.inspect_candidate(candidate)
                receipt = judge.grade(candidate, "离线验证")

                self.assertTrue(receipt["accepted"], receipt["message"])
                self.assertTrue(receipt["new_result"])
                self.assertTrue(checked["key"])
                if pack == "lorenz-divergence":
                    self.assertTrue(checked["certified"])
                else:
                    self.assertGreater(checked["score"], receipt["before"]["score"])

    def test_square_reports_overlap_without_saving(self):
        judge = LocalJudge(self.root, "square-18")
        candidate = self.candidate(judge)
        poses = candidate["solution"]["poses"]
        poses[1] = poses[0][:]
        graded = judge.grade(candidate, "本地测试")
        self.assertFalse(graded["accepted"])
        self.assertIn("重叠", graded["message"])
        self.assertEqual(graded["before"], graded["after"])
        self.assertIsNone(graded["checkpoint"])

    def test_relay_branch_checkpoint_matches_candidate_even_when_it_is_behind_best(self):
        judge = LocalJudge(self.root, "relay-7")
        partial = self.candidate(judge)
        partial["solution"]["blocks"][partial["solution"]["blocks"].index([1, 2, 4])] = [1, 2, 3]
        complete = copy.deepcopy(partial)
        complete["solution"]["blocks"][complete["solution"]["blocks"].index([1, 5, 6])] = [1, 6, 7]
        self.assertTrue(judge.grade(complete, "全覆盖测试")["accepted"])
        graded = judge.grade(partial, "分支测试")
        self.assertTrue(graded["accepted"], graded["message"])
        self.assertTrue(graded["new_result"])
        self.assertEqual((graded["before"]["score"], graded["after"]["score"]), (21, 21))
        self.assertEqual(graded["checkpoint"]["chain"][-1]["state"]["score"], 19)
        self.assertEqual(judge.task(), "")


if __name__ == "__main__":
    unittest.main()
