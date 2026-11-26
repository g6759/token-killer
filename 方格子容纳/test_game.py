import copy
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

from game import HERE, PackingServer, below_public_upper, game_view, report, submit


class PackingGameTests(unittest.TestCase):
    def setUp(self):
        tmp = TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)
        self.store = PackingServer.build_store(self.root / "game.db")
        self.examples = json.loads((HERE / "reference_layouts.json").read_text(encoding="utf-8"))

    def test_both_open_instances_save_replay_and_reject_false_scores(self):
        for key in PackingServer.packs:
            seed = self.store.best(key)
            initial = game_view(seed)
            self.assertEqual(len(initial["polygons"]), initial["state"]["n"])
            item = {"pack_id": key, "parent_hash": seed["hash"], "solution": self.examples[key]["finer"],
                    "handoff": "公开构造复现，校对面积。"}
            improved = submit(self.store, {"author": "试局", "candidate": item})
            self.assertLess(improved["state"]["side_ticks"], initial["state"]["side_ticks"])
            self.assertFalse(submit(self.store, {"author": "另一位", "candidate": item})["new_result"])
            rec = self.store.best(key)
            self.assertIn("试局", report(rec, self.store.history(key)))
            bundle = self.store.export(rec["hash"])
            target = PackingServer.build_store(self.root / "peer.db")
            self.assertEqual(target.ingest(bundle)["hash"], rec["hash"])
            forged = copy.deepcopy(bundle)
            forged["chain"][-1]["state"]["score"] += 1
            with self.assertRaises(ValueError):
                target.ingest(forged)

    def test_irrational_reference_compared_without_decimal_rounding(self):
        for n, floor in [(18, 4822875), (19, 4885618)]:
            self.assertTrue(below_public_upper(floor, n))
            self.assertFalse(below_public_upper(floor + 1, n))

    def test_sync_awards_only_an_improving_fresh_witness(self):
        from game import load_reference
        with PackingServer(self.root / "sync.sqlite3", 0) as server:
            key = "square-18"
            baseline = server.store.best(key)
            source = {**load_reference(18, self.root), "status": "online", "witness_refreshed": True}
            with patch("game.refresh_reference", return_value=source):
                first = server.sync_reference(key)
                again = server.sync_reference(key)
            self.assertNotEqual(first["state"]["hash"], baseline["hash"])
            self.assertEqual(first["state"]["hash"], again["state"]["hash"])
            self.assertEqual(len(server.store.history(key)), 2)
            self.assertIn("公开构造", first["title_award"]["holder"])
            self.assertEqual(first["title_award"]["name"], "紧排能手")

            other = "square-19"
            cached = {**load_reference(19, self.root), "status": "partial", "witness_refreshed": False}
            with patch("game.refresh_reference", return_value=cached):
                result = server.sync_reference(other)
            self.assertEqual(result["title_award"]["name"], "方格学徒")
            self.assertEqual(len(server.store.history(other)), 1)

    def test_rotating_an_existing_record_does_not_award_another_submission(self):
        key = "square-18"
        seed = self.store.best(key)
        finer = self.examples[key]["finer"]
        item = {"pack_id": key, "parent_hash": seed["hash"], "solution": finer, "handoff": "公开见证精细复现"}
        first = submit(self.store, {"author": "甲", "candidate": item})
        limit = max(x for x, *_ in finer["poses"])
        rotated = {"poses": [[y, limit-x, p, q] for x, y, p, q in finer["poses"]]}
        second = submit(self.store, {"author": "乙", "candidate": {**item, "solution": rotated}})
        self.assertFalse(second["new_result"])
        self.assertEqual(first["state"]["hash"], second["state"]["hash"])
        self.assertEqual(len(self.store.history(key)), 2)
        self.assertIn("复现", first["achievement"])
