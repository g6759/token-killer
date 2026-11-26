from concurrent.futures import ThreadPoolExecutor
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic
import unittest

import benchmark
import square_frontier as frontier
from 本地裁判 import LocalJudge, get_square_pack


class SquareFrontierTests(unittest.TestCase):
    def test_model_can_only_select_a_presented_branch(self):
        for count in (1, 1152, 1728):
            with self.subTest(count=count):
                branch = frontier.schema(count)["properties"]["branch"]
                self.assertEqual((branch["minimum"], branch["maximum"]), (0, count - 1))

    def test_wider_frontier_is_within_request_budget_and_selectable(self):
        with TemporaryDirectory() as scratch:
            parent = LocalJudge(Path(scratch), frontier.PACK).store.best(frontier.PACK)
            prompt, context = frontier.build(frontier.PACK, parent, "test/lane-2", 1, 1728)
            branches = json.loads(prompt)["branches"]
            self.assertEqual(len(branches), 1728)
            self.assertLessEqual(len(prompt.encode()), 1600*1024)
            raw = json.dumps({"pack_id":frontier.PACK, "parent_hash":parent["hash"], "branch":1727,
                              "square":1, "dx":0, "dy":0, "handoff":"Verify the final branch."})
            result = frontier.resolve(frontier.PACK, parent, context, raw)
            get_square_pack(frontier.PACK).evaluate(result["candidate"]["solution"])

    def test_large_prompt_and_model_decision_match_native_judge(self):
        with TemporaryDirectory() as scratch, ThreadPoolExecutor(max_workers=1) as workers:
            lane = Path(scratch) / "square-frontier-1152-40" / "lane-1"
            judge = LocalJudge(lane, frontier.PACK)
            parent = judge.store.best(frontier.PACK)

            def infer(request, seconds, abort, *, on_sent):
                payload = json.loads(request["prompt"])
                self.assertEqual(len(payload["branches"]), 1152)
                self.assertLessEqual(len(request["prompt"].encode()), 1600*1024)
                index, poses, span, contacts = payload["branches"][9]
                self.assertEqual(len(poses), 26)
                self.assertEqual(len(contacts), 5)
                self.assertGreater(span, 1000000)
                on_sent()
                move = {"pack_id":frontier.PACK, "parent_hash":parent["hash"], "branch":index,
                        "square":0, "dx":0, "dy":0, "handoff":"Test the selected boundary shift."}
                return {"text":json.dumps(move), "guided":True, "model":"fixture", "response_id":"fixture-one",
                        "usage":{"input_tokens":5,"cached_input_tokens":0,"output_tokens":2}}

            rows, error = benchmark._run_lane(lane, judge, parent["hash"], "", infer, monotonic(), 60,
                                              True, 1, Event(), None, 1, 1152, workers)
            self.assertIsNone(error)
            self.assertEqual(len(rows), 1)
            self.assertTrue(rows[0]["verification"]["valid"])
            result = get_square_pack(frontier.PACK).evaluate(rows[0]["candidate"]["solution"])
            self.assertEqual(rows[0]["verification"]["score"], result["score"])


if __name__ == "__main__":
    unittest.main()
