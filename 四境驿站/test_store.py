import copy
import hashlib
import json
import sqlite3
import unittest
from concurrent.futures import ThreadPoolExecutor
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory

from relay_store import Store, credited_hashes


PACK = "relay-7"


def changed(solution, old, new):
    result = copy.deepcopy(solution)
    result["blocks"][result["blocks"].index(old)] = new
    return result


def sha256(value):
    encoded = json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


class StoreTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name)
        self.origin = Store(self.path / "origin.sqlite")
        self.peer = Store(self.path / "peer.sqlite")
        self.seed = self.origin.seed(PACK)

    def candidates(self):
        starting = self.seed["solution"]
        east = changed(starting, [1, 2, 4], [1, 2, 3])
        west = changed(starting, [1, 5, 6], [1, 6, 7])
        both = changed(east, [1, 5, 6], [1, 6, 7])
        return east, west, both

    def winning_chain(self):
        east, _, both = self.candidates()
        first = self.origin.submit(PACK, self.seed["hash"], "甲", east, "缺两对")
        final = self.origin.submit(PACK, first["hash"], "乙", both, "覆盖全数")
        return first, final

    def test_seed_and_canonical_retry(self):
        self.assertEqual(self.seed, self.peer.best(PACK))
        self.assertEqual(self.seed["state"]["score"], 17)
        east, _, _ = self.candidates()
        first = self.origin.submit(PACK, self.seed["hash"], " 甲 ", east, "x")
        reordered = {"blocks": [row[::-1] for row in east["blocks"][::-1]]}
        retry = self.origin.submit(PACK, self.seed["hash"], "甲", reordered, "x")
        self.assertEqual(first, retry)
        self.assertEqual(len(self.origin.history(PACK)), 2)
        self.assertEqual(first["hash"], sha256(first["state"]))
        self.assertEqual(first["state"]["solution_hash"], sha256(first["solution"]))
        self.assertEqual(first["state"]["handoff_hash"], sha256(first["handoff"]))

    def test_concurrent_tied_branches_are_saved_and_ranked_by_hash(self):
        east, west, _ = self.candidates()
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [
                pool.submit(self.origin.submit, PACK, self.seed["hash"], author, solution, "")
                for author, solution in (("甲", east), ("乙", west))
            ]
            branches = [future.result() for future in futures]
        hashes = sorted(record["hash"] for record in branches)
        self.assertEqual([record["state"]["score"] for record in branches], [19, 19])
        self.assertEqual(self.origin.best(PACK)["hash"], hashes[0])
        self.assertEqual([record["hash"] for record in self.origin.history(PACK)], hashes + [self.seed["hash"]])

    def test_submit_reuses_canonical_solution_with_another_author_or_note(self):
        east, _, _ = self.candidates()
        first = self.origin.submit(PACK, self.seed["hash"], "甲", east, "第一份说明")
        for author, handoff in (("甲", "第二份说明"), ("乙", "第一份说明")):
            reused = self.origin.submit(PACK, self.seed["hash"], author, east, handoff)
            self.assertEqual(reused, first)
        self.assertEqual(len(self.origin.history(PACK)), 2)

    def test_cross_store_duplicate_solutions_preserve_branches_but_count_once(self):
        east, _, both = self.candidates()
        first = self.origin.submit(PACK, self.seed["hash"], "甲", east, "")
        self.peer.seed(PACK)
        second = self.peer.submit(PACK, self.seed["hash"], "乙", east, "")
        first_tip = self.origin.submit(PACK, first["hash"], "甲", both, "甲的续档")
        second_tip = self.peer.submit(PACK, second["hash"], "乙", both, "乙的续档")
        self.assertEqual(self.origin.ingest(self.peer.export(second_tip["hash"])), second_tip)
        self.assertEqual(self.peer.ingest(self.origin.export(first_tip["hash"])), first_tip)
        expected_credit = {
            self.seed["hash"],
            min(first["hash"], second["hash"]),
            min(first_tip["hash"], second_tip["hash"]),
        }
        for store in (self.origin, self.peer):
            history = store.history(PACK)
            self.assertEqual(len(history), 5)
            self.assertEqual(credited_hashes(history), expected_credit)
            self.assertEqual(credited_hashes(reversed(history)), expected_credit)
            self.assertEqual(store.get(first_tip["hash"])["state"]["parent_hash"], first["hash"])
            self.assertEqual(store.get(second_tip["hash"])["state"]["parent_hash"], second["hash"])
            self.assertEqual(store.submit(PACK, self.seed["hash"], "甲", east, ""), first)
            representative = store.submit(PACK, self.seed["hash"], "丙", east, "")
            self.assertEqual(representative["hash"], min(first["hash"], second["hash"]))

    def test_submit_requires_strict_parent_improvement_and_same_pack(self):
        east, west, _ = self.candidates()
        first = self.origin.submit(PACK, self.seed["hash"], "甲", east, "")
        for solution in (west, self.seed["solution"]):
            with self.assertRaisesRegex(ValueError, "提高"):
                self.origin.submit(PACK, first["hash"], "乙", solution, "")
        other = self.origin.seed("relay-49")
        with self.assertRaisesRegex(ValueError, "题包"):
            self.origin.submit(PACK, other["hash"], "乙", west, "")
        self.assertEqual(len(self.origin.history(PACK)), 2)

    def test_peers_can_replay_and_continue_independently(self):
        east, _, both = self.candidates()
        first = self.origin.submit(PACK, self.seed["hash"], "甲", east, "下一步")
        bundle = self.origin.export(first["hash"])
        self.assertEqual(self.peer.history(PACK), [])
        self.assertEqual(self.peer.ingest(json.loads(json.dumps(bundle))), first)
        self.assertEqual(self.peer.ingest(bundle), first)
        self.assertEqual(len(self.peer.history(PACK)), 2)
        self.assertEqual(self.peer.get(first["hash"]), first)
        final = self.peer.submit(PACK, first["hash"], "乙", both, "完成")
        replayed = self.origin.ingest(self.peer.export(final["hash"]))
        self.assertEqual(replayed, final)
        self.assertEqual(self.origin.best(PACK), final)
        self.assertEqual([step["score"] for step in final["lineage"]], [17, 19, 21])
        self.assertEqual([step["author"] for step in final["lineage"]], ["seed", "甲", "乙"])

    def test_ingest_rescores_forged_tip_and_rolls_back_ancestors(self):
        _, final = self.winning_chain()
        bundle = self.origin.export(final["hash"])
        forged = bundle["chain"][-1]
        forged["state"]["score"] = 20
        forged["hash"] = sha256(forged["state"])
        bundle["tip"] = forged["hash"]
        with self.assertRaises(ValueError):
            self.peer.ingest(bundle)
        self.assertEqual(self.peer.history(PACK), [])

    def test_ingest_rolls_back_valid_ancestors_when_database_write_fails(self):
        _, final = self.winning_chain()
        with closing(sqlite3.connect(self.path / "peer.sqlite")) as db, db:
            db.execute("""
                CREATE TRIGGER reject_tip BEFORE INSERT ON checkpoints
                WHEN NEW.score = 21
                BEGIN
                    SELECT RAISE(ABORT, 'test storage failure');
                END
                """)
        with self.assertRaisesRegex(ValueError, "test storage failure"):
            self.peer.ingest(self.origin.export(final["hash"]))
        self.assertEqual(self.peer.history(PACK), [])

    def test_ingest_rejects_missing_ancestor_and_unbound_handoff(self):
        first, final = self.winning_chain()
        self.peer.ingest(self.origin.export(first["hash"]))
        original = self.origin.export(final["hash"])
        truncated = copy.deepcopy(original)
        truncated["chain"].pop(0)
        tampered = copy.deepcopy(original)
        tampered["chain"][-1]["handoff"] = "different"
        for bundle in (truncated, tampered):
            with self.assertRaises(ValueError):
                self.peer.ingest(bundle)
        self.assertEqual(self.peer.best(PACK), first)

    def test_ingest_rejects_alternate_genesis_and_unsupported_versions(self):
        original = self.origin.export(self.seed["hash"])
        alternate = copy.deepcopy(original)
        alternate["chain"][0]["state"]["author"] = "甲"
        alternate["chain"][0]["hash"] = sha256(alternate["chain"][0]["state"])
        alternate["tip"] = alternate["chain"][0]["hash"]
        wrong_version = copy.deepcopy(original)
        wrong_version["chain"][0]["state"]["version"] = "future"
        wrong_version["chain"][0]["hash"] = sha256(wrong_version["chain"][0]["state"])
        wrong_version["tip"] = wrong_version["chain"][0]["hash"]
        for bundle in (alternate, wrong_version):
            with self.assertRaises(ValueError):
                self.peer.ingest(bundle)
        self.assertEqual(self.peer.history(PACK), [])

    def test_input_validation_does_not_create_checkpoints(self):
        east, _, _ = self.candidates()
        for author, handoff in (("  ", ""), ("a\nb", ""), ("a", "x" * 4097), ("a", "\ud800")):
            with self.assertRaises(ValueError):
                self.origin.submit(PACK, self.seed["hash"], author, east, handoff)
        with self.assertRaises(ValueError):
            self.origin.submit(PACK, self.seed["hash"], "甲", {"blocks": []}, "")
        with self.assertRaises(ValueError):
            self.origin.get("not a hash")
        self.assertEqual(self.origin.history(PACK), [self.seed])


if __name__ == "__main__":
    unittest.main()
