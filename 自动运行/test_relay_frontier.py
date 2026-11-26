from itertools import combinations
import json
import unittest

import relay_frontier
from 本地裁判 import get_pack


class RelayFrontierTests(unittest.TestCase):
    def setUp(self):
        self.pack_id = "relay-49-frontier"
        self.rules = get_pack(self.pack_id)
        blocks = self.rules.seed()["blocks"]
        self.parent = {"hash": "parent-49", "solution": {"blocks": blocks}}

    def test_build_is_replayable_and_reports_distinct_real_branches(self):
        prompt, context = relay_frontier.build(self.pack_id, self.parent, "lane-2", 7, 3)
        repeated, repeated_context = relay_frontier.build(self.pack_id, self.parent, "lane-2", 7, 3)

        self.assertEqual((prompt, context), (repeated, repeated_context))
        self.assertEqual(set(context), {"seed", "count", "parent_hash"})
        payload = json.loads(prompt)
        self.assertEqual(len(payload["branches"]), 3)
        self.assertEqual(len({branch["branch_id"] for branch in payload["branches"]}), 3)
        baseline = self.rules.evaluate(self.parent["solution"])
        baseline_missing = {tuple(pair) for pair in baseline["missing"]}
        baseline_counts = {}
        for row in baseline["solution"]["blocks"]:
            for pair in combinations(row, 2):
                baseline_counts[pair] = baseline_counts.get(pair, 0) + 1
        baseline_fragile = {pair for pair, amount in baseline_counts.items() if amount == 1}
        for branch in payload["branches"]:
            branch_rows = [row[:] for row in payload["baseline"]["blocks"]]
            for action in branch["edits"]:
                row = branch_rows[action["block"] - 1]
                row.remove(action["remove"])
                row.append(action["add"])
                row.sort()
            measured = self.rules.evaluate({"blocks": branch_rows})
            self.assertEqual(branch["score"], measured["score"])
            branch_missing = baseline_missing | {tuple(pair) for pair in branch["gap_add"]}
            branch_missing -= {tuple(pair) for pair in branch["gap_filled"]}
            self.assertEqual(sorted(branch_missing), [tuple(pair) for pair in measured["missing"]])
            branch_fragile = baseline_fragile | {tuple(pair) for pair in branch["fragile_add"]}
            branch_fragile -= {tuple(pair) for pair in branch["fragile_drop"]}
            branch_counts = {}
            for row in branch_rows:
                for pair in combinations(row, 2):
                    branch_counts[pair] = branch_counts.get(pair, 0) + 1
            self.assertEqual(branch_fragile, {pair for pair, amount in branch_counts.items() if amount == 1})
            self.assertNotEqual(measured["solution"], baseline["solution"])
            self.assertTrue(branch["edits"])
            self.assertIn("fragile_add", branch)
            self.assertIn("fragile_drop", branch)
            self.assertIn("repairs", branch)
        signatures = []
        for branch in payload["branches"]:
            rows = [row[:] for row in payload["baseline"]["blocks"]]
            for action in branch["edits"]:
                row = rows[action["block"] - 1]
                row.remove(action["remove"])
                row.append(action["add"])
                row.sort()
            signatures.append(json.dumps(rows))
        self.assertGreater(len(set(signatures)), 1)

    def test_resolve_applies_one_legal_edit_and_returns_guided_search_shape(self):
        prompt, context = relay_frontier.build(self.pack_id, self.parent, "lane-2", 7, 2)
        branch = json.loads(prompt)["branches"][0]
        edit = None
        blocks = self.rules.evaluate(self.parent["solution"])["solution"]["blocks"]
        # Branch operations preserve parent row positions; replay their deltas to find a legal extra swap.
        for action in branch["edits"]:
            row = blocks[action["block"] - 1]
            row.remove(action["remove"])
            row.append(action["add"])
            row.sort()
        for block_index, old_row in enumerate(blocks, start=1):
            for incoming in range(1, 50):
                if incoming in old_row:
                    continue
                for outgoing in old_row:
                    candidate_row = tuple(sorted((set(old_row) - {outgoing}) | {incoming}))
                    others = {tuple(row) for i, row in enumerate(blocks, start=1) if i != block_index}
                    if candidate_row not in others:
                        edit = (block_index, outgoing, incoming)
                        break
                if edit:
                    break
            if edit:
                break
        self.assertIsNotNone(edit)

        response = json.dumps({
            "pack_id": self.pack_id,
            "parent_hash": self.parent["hash"],
            "branch_id": branch["branch_id"],
            "block": edit[0],
            "remove": edit[1],
            "add": edit[2],
            "handoff": "查看替换后新增覆盖与损失的唯一覆盖。",
        })
        result = relay_frontier.resolve(self.pack_id, self.parent, context, response)
        fast_result = relay_frontier.resolve(self.pack_id, self.parent, context, response,
                                             presented_prompt=prompt)

        self.assertEqual(result, fast_result)
        self.assertEqual(set(result), {"strategy", "checked", "candidate", "hypothesis", "outcome"})
        self.assertEqual(result["strategy"], "branch_repair")
        self.assertGreater(result["checked"], 0)
        self.assertEqual(result["candidate"]["pack_id"], self.pack_id)
        self.assertEqual(result["candidate"]["parent_hash"], self.parent["hash"])
        self.assertEqual(self.rules.evaluate(result["candidate"]["solution"])["score"],
                         int(result["outcome"].split("score=")[1].split(";")[0]))

    def test_schema_is_strict_and_resolve_rejects_stale_parent_or_invalid_edit(self):
        schema = relay_frontier.schema()
        self.assertEqual(schema["type"], "object")
        self.assertFalse(schema["additionalProperties"])
        self.assertEqual(set(schema["properties"]), set(schema["required"]))

        prompt, context = relay_frontier.build(self.pack_id, self.parent, "lane-2", 7, 1)
        branch = json.loads(prompt)["branches"][0]
        reply = {"pack_id": self.pack_id, "parent_hash": self.parent["hash"],
                 "branch_id": branch["branch_id"], "block": 1,
                 "remove": 1, "add": 1, "handoff": "检查边界。"}
        with self.assertRaisesRegex(ValueError, "编辑"):
            relay_frontier.resolve(self.pack_id, self.parent, context, json.dumps(reply))
        reply["parent_hash"] = "stale"
        with self.assertRaisesRegex(ValueError, "父档"):
            relay_frontier.resolve(self.pack_id, self.parent, context, json.dumps(reply))

    def test_large_tiers_replay_distinct_dense_parent_and_respect_byte_caps(self):
        # A 1170-point parent from a real relay handoff: only six pairs are missing.
        rows_text = (
            "1 2 3 4 5 6 7 8 9 12 31;1 2 3 9 10 11 22 33 34 35 48;"
            "1 2 3 13 17 18 19 32 45 47 49;1 2 3 14 15 16 26 27 39 40 41;"
            "1 2 3 20 21 23 24 25 42 44 46;1 2 3 28 29 30 36 37 38 43 44;"
            "4 5 6 10 11 32 39 40 41 42 44;4 5 6 13 14 15 16 23 24 25 43;"
            "4 5 6 13 20 21 22 36 37 38 49;4 5 6 17 18 19 28 29 30 42 48;"
            "4 5 6 26 27 33 34 35 45 46 47;7 8 10 11 12 13 28 29 30 46 49;"
            "7 8 12 14 15 16 22 42 44 45 47;7 8 12 17 18 19 26 27 36 37 38;"
            "7 8 12 20 21 31 32 33 34 35 43;7 8 12 23 24 25 39 40 41 48 49;"
            "9 13 21 26 27 31 42 43 44 48 49;9 14 15 16 32 36 37 38 46 47 48;"
            "9 17 18 19 23 24 25 33 34 35 44;9 20 21 28 29 30 39 40 41 45 48;"
            "10 11 14 15 16 17 18 19 20 21 47;10 11 23 24 25 31 36 37 38 43 45;"
            "13 33 34 35 36 37 38 39 40 41 42;14 15 16 28 29 30 31 33 34 35 49;"
            "17 18 19 22 31 39 40 41 43 46 47;22 23 24 25 26 27 28 29 30 32 47"
        )
        parent = {"hash": "dense-1170", "solution": {"blocks": [
            [int(value) for value in row.split()] for row in rows_text.split(";")]}}
        baseline = self.rules.evaluate(parent["solution"])
        self.assertEqual(baseline["score"], 1170)
        self.assertEqual(relay_frontier.MAX_PROMPT_BYTES, 256 * 1024)
        with self.assertRaises(ValueError):
            relay_frontier.build(self.pack_id, parent, "lane", 1, 193)

        for count in relay_frontier.LARGE_BRANCH_COUNTS:
            prompt, context = relay_frontier.build(self.pack_id, parent, "lane", 1, count)
            self.assertLessEqual(len(prompt.encode("utf-8")),
                                 relay_frontier.LARGE_PROMPT_BYTES[count])
            branches = json.loads(prompt)["branches"]
            if count == 384:
                first_384_edits = branches[0]["edits"]
                context_384 = context
            self.assertEqual(len(branches), count)
            signatures = set()
            for branch in branches:
                rows = [row[:] for row in baseline["solution"]["blocks"]]
                for action in branch["edits"]:
                    row = rows[action["block"] - 1]
                    row.remove(action["remove"])
                    row.append(action["add"])
                    row.sort()
                signatures.add(tuple(tuple(row) for row in rows))
            self.assertEqual(len(signatures), count)
            for branch in (branches[0], branches[count // 2], branches[-1]):
                rows = [row[:] for row in baseline["solution"]["blocks"]]
                for action in branch["edits"]:
                    row = rows[action["block"] - 1]
                    row.remove(action["remove"])
                    row.append(action["add"])
                    row.sort()
                measured = self.rules.evaluate({"blocks": rows})
                self.assertEqual(branch["score"], measured["score"])
                missing = {tuple(pair) for pair in baseline["missing"]}
                missing.update(tuple(pair) for pair in branch["gap_add"])
                missing.difference_update(tuple(pair) for pair in branch["gap_filled"])
                self.assertEqual(missing, {tuple(pair) for pair in measured["missing"]})
                self.assertTrue(branch["repairs"])
                for repair in branch["repairs"]:
                    repaired_rows = [row[:] for row in rows]
                    target = repaired_rows[repair["block"] - 1]
                    target.remove(repair["remove"])
                    target.append(repair["add"])
                    repaired = self.rules.evaluate({"blocks": repaired_rows})
                    self.assertEqual(repaired["score"] - measured["score"], repair["net"])
                    repaired_missing = {tuple(pair) for pair in repaired["missing"]}
                    self.assertEqual({tuple(pair) for pair in repair["fills"]},
                                     missing - repaired_missing)
                    self.assertEqual({tuple(pair) for pair in repair["exposes"]},
                                     repaired_missing - missing)
                hint = branch["repairs"][0]
                response = json.dumps({"pack_id": self.pack_id, "parent_hash": parent["hash"],
                                       "branch_id": branch["branch_id"],
                                       "block": hint["block"], "remove": hint["remove"],
                                       "add": hint["add"], "handoff": "核验增量修复。"})
                if count == 1152 and branch is branches[0]:
                    result = relay_frontier.resolve(self.pack_id, parent, context, response)
                    fast_result = relay_frontier.resolve(self.pack_id, parent, context, response,
                                                         presented_prompt=prompt)
                    self.assertEqual(result, fast_result)
                    self.assertEqual(result["checked"], count + 1)
                    self.assertEqual(self.rules.evaluate(result["candidate"]["solution"])["score"],
                                     measured["score"] + hint["net"])
                    tampered = json.loads(prompt)
                    tampered["reply_shape"]["parent_hash"] = "other-parent"
                    with self.assertRaisesRegex(ValueError, "父档"):
                        relay_frontier.resolve(self.pack_id, parent, context, response,
                                               presented_prompt=json.dumps(tampered))
                    tampered = json.loads(prompt)
                    tampered["branches"][0]["edits"][0]["add"] = 99
                    with self.assertRaisesRegex(ValueError, "编辑"):
                        relay_frontier.resolve(self.pack_id, parent, context, response,
                                               presented_prompt=json.dumps(tampered))
        alternate, alternate_context = relay_frontier.build(self.pack_id, parent,
                                                              "another-lane", 2, 384)
        self.assertNotEqual(context_384["seed"], alternate_context["seed"])
        self.assertNotEqual(json.loads(alternate)["branches"][0]["edits"],
                            first_384_edits)


if __name__ == "__main__":
    unittest.main()
