from copy import deepcopy
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from auto_search import SearchStopped, geometry, next_candidate
from packs import get_pack
from relay_store import Store


get_square_pack = geometry.get_square_pack


class AutoSearchTest(unittest.TestCase):
    def test_compacts_a_saved_nonseed_square_layout(self):
        pack_id = "square-18"
        pack = get_square_pack(pack_id)
        with TemporaryDirectory() as directory:
            store = Store(Path(directory) / "packing.sqlite3", resolve_pack=get_square_pack)
            parent = store.best(pack_id)
            first = next_candidate(pack_id, parent, 1)
            self.assertEqual(set(first), {"pack_id", "parent_hash", "solution", "handoff"})
            self.assertGreater(pack.evaluate(first["solution"])["score"], parent["state"]["score"])
            saved = store.submit(pack_id, first["parent_hash"], "自动搜索测试", first["solution"], first["handoff"])
            snapshot = deepcopy(saved)
            second = next_candidate(pack_id, saved, 2)
            self.assertEqual(saved, snapshot)
            self.assertEqual(second["parent_hash"], saved["hash"])
            self.assertNotEqual(second["solution"], saved["solution"])
            pack.evaluate(second["solution"])

    def test_square19_seed_has_real_improvement(self):
        pack_id = "square-19"
        pack = get_square_pack(pack_id)
        with TemporaryDirectory() as directory:
            store = Store(Path(directory) / "packing.sqlite3", resolve_pack=get_square_pack)
            parent = store.best(pack_id)
            candidate = next_candidate(pack_id, parent, 1)
            self.assertGreater(pack.evaluate(candidate["solution"])["score"], parent["state"]["score"])

    def test_neighborhood_uses_real_relay_parent_and_is_replayable(self):
        pack_id = "relay-49"
        pack = get_pack(pack_id)
        with TemporaryDirectory() as directory:
            store = Store(Path(directory) / "relay.sqlite3")
            seed = store.best(pack_id)
            first = next_candidate(pack_id, seed, 1)
            saved = store.submit(pack_id, seed["hash"], "自动搜索测试", first["solution"], first["handoff"])
            self.assertGreater(saved["state"]["score"], seed["state"]["score"])
            child = next_candidate(pack_id, saved, 2)
            self.assertEqual(child, next_candidate(pack_id, saved, 2))
            self.assertEqual(child["parent_hash"], saved["hash"])
            self.assertGreater(pack.evaluate(child["solution"])["score"], saved["state"]["score"])

    def test_frontier_rounds_produce_distinct_valid_candidates(self):
        pack_id = "relay-49-frontier"
        pack = get_pack(pack_id)
        with TemporaryDirectory() as directory:
            parent = Store(Path(directory) / "relay.sqlite3").best(pack_id)
            first = next_candidate(pack_id, parent, 1)
            second = next_candidate(pack_id, parent, 2)
        self.assertNotEqual(first["solution"], second["solution"])
        for candidate in (first, second):
            self.assertEqual(candidate["parent_hash"], parent["hash"])
            pack.evaluate(candidate["solution"])

    def test_stop_interrupts_work_in_both_packs(self):
        with TemporaryDirectory() as directory:
            for pack_id, resolver in (("square-18", get_square_pack), ("relay-49-frontier", get_pack)):
                with self.subTest(pack=pack_id):
                    parent = Store(Path(directory) / f"{pack_id}.sqlite3", resolve_pack=resolver).best(pack_id)
                    calls = 0

                    def stopped():
                        nonlocal calls
                        calls += 1
                        return calls >= 4

                    with self.assertRaises(SearchStopped):
                        next_candidate(pack_id, parent, 1, stopped)


if __name__ == "__main__":
    unittest.main()
