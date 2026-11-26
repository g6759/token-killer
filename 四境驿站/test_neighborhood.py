from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from neighborhood import audit, verify
from packs import FRONTIER_PACK_ID
from relay_store import Store


class NeighborhoodTests(unittest.TestCase):
    def setUp(self):
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        self.store = Store(Path(folder.name) / "game.sqlite3")

    def test_frontier_audit_exhausts_the_legal_single_station_moves(self):
        record = self.store.best(FRONTIER_PACK_ID)
        result = audit(record)
        self.assertEqual(result["parent_hash"], record["hash"])
        self.assertEqual(result["title"], "单站替换已排尽")
        self.assertEqual(
            [result[key] for key in ("score", "total_neighbors", "better", "equal", "worse", "best_delta")],
            [1160, 10868, 0, 23, 10845, 0],
        )

    def test_audit_reports_improving_moves_without_a_negative_achievement(self):
        result = audit(self.store.best("relay-49"))
        self.assertEqual(result["title"], "已复算单换邻域")
        self.assertEqual((result["better"], result["best_delta"]), (3888, 9))

    def test_verifier_replays_the_checkpoint_and_rejects_false_summaries(self):
        record = self.store.best(FRONTIER_PACK_ID)
        summary = audit(record)
        certificate = {**summary, "checkpoint": self.store.export(record["hash"])}
        self.assertEqual(verify(certificate), summary)
        for key, value in (("better", False), ("parent_hash", "a" * 64), ("equal", 22)):
            with self.subTest(key=key), self.assertRaises(ValueError):
                verify({**certificate, key: value})

        invalid = deepcopy(certificate)
        invalid["checkpoint"]["chain"][0]["state"]["score"] = 1176
        with self.assertRaises(ValueError):
            verify(invalid)


if __name__ == "__main__":
    unittest.main()
