import gzip
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from unittest.mock import patch

import prepared_frontiers as prepared
import relay_frontier
from 本地裁判 import get_pack


class PreparedFrontiersTests(unittest.TestCase):
    def setUp(self):
        self.parent = {"hash": "native-parent", "solution": {"blocks": get_pack(prepared.PACK).seed()["blocks"]}}
        self.suite = "future-suite-A"
        self.scenario = "relay-frontier-1152-probe"

    def test_matching_checks_every_slot_parent_and_prompt_hash(self):
        with TemporaryDirectory() as temporary:
            deck = Path(temporary)
            identity = prepared._identity(self.suite, self.scenario, 1, 2, self.parent, 1152, prepared.PACK)
            prompt = '{"real":"local branch"}'
            context = {"seed": relay_frontier._seed(prepared.PACK, self.parent["hash"],
                                                    identity["builder_lane"], 2, 1152),
                       "count": 1152, "parent_hash": self.parent["hash"]}
            prepared._write_entry(deck, identity, prompt, context)
            lookup = lambda **extra: prepared.load_if_matching(
                deck, extra.get("suite", self.suite), extra.get("scenario", self.scenario),
                extra.get("lane", 1), extra.get("turn", 2), extra.get("parent", self.parent), count=1152)
            self.assertEqual(lookup(), (prompt, context))
            for change in ({"suite": "future-suite-B"}, {"scenario": "another-scenario"},
                           {"lane": 2}, {"turn": 1},
                           {"parent": {**self.parent, "hash": "changed"}},
                           {"parent": {**self.parent, "solution": {"blocks": []}}}):
                self.assertIsNone(lookup(**change))
            path = prepared._entry_path(deck, identity)
            with gzip.open(path, "rt", encoding="utf-8") as stream:
                data = json.load(stream)
            data["prompt"] = "altered"
            path.write_bytes(gzip.compress(json.dumps(data).encode()))
            self.assertIsNone(lookup())

    def test_prepare_one_real_branch_and_resume_without_rebuilding(self):
        with TemporaryDirectory() as temporary:
            deck = Path(temporary) / "deck"
            seen = []
            first = prepared.prepare(deck, self.suite, self.scenario, self.parent, 1, 1,
                                     count=1, workers=1, progress=seen.append)
            self.assertEqual((first["generated"], first["skipped"]), (1, 0))
            prompt, context = prepared.load_if_matching(deck, self.suite, self.scenario, 1, 1,
                                                        self.parent, count=1)
            expected = relay_frontier.build(prepared.PACK, self.parent, f"{self.suite}/lane-1", 1, 1)
            self.assertEqual((prompt, context), expected)
            with patch.object(prepared, "ProcessPoolExecutor", side_effect=AssertionError("rebuilt")):
                second = prepared.prepare(deck, self.suite, self.scenario, self.parent, 1, 1,
                                          count=1, workers=1, progress=seen.append)
            self.assertEqual((second["generated"], second["skipped"]), (0, 1))

    def test_inputs_chain_uses_native_last_parent(self):
        with TemporaryDirectory() as temporary:
            source = Path(temporary) / "inputs.json"
            previous = {**self.parent, "hash": "older"}
            source.write_text(json.dumps({prepared.PACK: {"chain": [previous, self.parent]}}),
                              encoding="utf-8")
            self.assertEqual(prepared._read_parent(source, prepared.PACK), self.parent)


if __name__ == "__main__":
    unittest.main()
