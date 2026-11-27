import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest
from types import SimpleNamespace
from threading import Lock

from auto_jobs import GameJobs
from relay_store import Store
from solo_server import SoloServer


class AutoJobsTest(unittest.TestCase):
    def test_client_target_counts_each_receipt_once_and_stops_at_target(self):
        self.jobs.stop(self.pack, self.key)
        job = self.jobs.start(self.pack, 10, "mcp", "client-start", client="claude-code", target_tokens=10)
        first = self.jobs.claim(self.pack, job["id"])
        candidate = json.loads(first["prompt"].splitlines()[-1])
        receipt = {"response_id": "native-response", "uncached_input_tokens": 5, "output_tokens": 2}
        result = self.jobs.submit(self.pack, job["id"], 1, candidate, usage_receipt=receipt)
        self.assertEqual(result["job"]["effective_tokens"], 7)
        self.assertEqual(self.jobs.submit(self.pack, job["id"], 1, candidate, usage_receipt=receipt)["turn"], 2)
        next_candidate = json.loads(result["prompt"].splitlines()[-1])
        with self.assertRaisesRegex(ValueError, "Duplicate"):
            self.jobs.submit(self.pack, job["id"], 2, next_candidate, usage_receipt=receipt)
        final = self.jobs.submit(self.pack, job["id"], 2, next_candidate,
                                 usage_receipt={**receipt, "response_id": "another-response"})
        self.assertIsNone(final["prompt"])
        self.assertEqual((final["job"]["effective_tokens"], final["job"]["end_reason"]), (14, "target"))
        self.assertEqual(GameJobs(self.jobs.server).status(self.pack)["effective_tokens"], 14)

    def test_missing_client_usage_saves_candidate_and_obeys_selected_limit(self):
        self.jobs.stop(self.pack, self.key)
        for target, expected in ((12, "stopped"), (None, "running")):
            job = self.jobs.start(self.pack, 3, "mcp", client="antigravity", target_tokens=target)
            task = self.jobs.claim(self.pack, job["id"])
            result = self.jobs.submit(self.pack, job["id"], 1, json.loads(task["prompt"].splitlines()[-1]))
            self.assertEqual((result["job"]["status"], result["job"]["completed"]), (expected, 1))
            self.assertIsNone(result["job"]["effective_tokens"])
            self.assertIsNone(result["job"]["recent_tokens_per_minute"])

    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        server = SimpleNamespace(store=Store(Path(temp.name) / "relay.sqlite3"),
                                 submit_lock=Lock(), packs=SoloServer.packs,
                                 view=SoloServer.view, judge=SoloServer.judge)
        self.jobs = GameJobs(server)
        self.pack = "relay-7"
        self.key = self.jobs.start(self.pack, 3, "mcp", "first")["id"]

    def test_mcp_turn_survives_restart_and_retries(self):
        jobs = self.jobs
        turn = jobs.turn(self.pack, self.key)
        self.assertIn("等待 ChatGPT 领取", turn["job"]["stage"])
        self.assertIn("已接手", jobs.claim(self.pack, self.key)["job"]["stage"])
        candidate = json.loads(turn["prompt"].splitlines()[-1])
        received = jobs.submit(self.pack, self.key, 1, candidate)
        self.assertEqual(received["turn"], 2)
        self.assertFalse(received["job"]["receipts"][0]["accepted"])
        restarted = GameJobs(jobs.server)
        self.assertEqual(restarted.start(self.pack, 3, "mcp", "first")["id"], self.key)
        self.assertEqual(restarted.submit(self.pack, self.key, 1, candidate)["turn"], 2)
        with self.assertRaises(ValueError):
            restarted.submit(self.pack, self.key, 1, {**candidate, "handoff": "另一个候选"})
        self.assertEqual(restarted.status(self.pack, ledger=True)["records"][0]["candidate"], candidate)

    def test_read_only_evaluation_and_stop(self):
        task = self.jobs.turn(self.pack, self.key)
        candidate = json.loads(task["prompt"].splitlines()[-1])
        first = self.jobs.status(self.pack, ledger=True)
        scored = self.jobs.evaluate(self.pack, candidate["solution"])
        self.assertEqual((scored["score"], scored["total"]), (17, 21))
        self.assertEqual(self.jobs.status(self.pack, ledger=True)["records"], first["records"])
        self.jobs.stop(self.pack, self.key)
        with self.assertRaises(ValueError):
            self.jobs.submit(self.pack, self.key, 1, candidate)
        self.assertIsNone(self.jobs.turn(self.pack, self.key)["prompt"])

    def test_progress_belongs_to_claimed_turn_and_survives_polling(self):
        args = self.pack, self.key, 1
        with self.assertRaises(ValueError):
            self.jobs.report_progress(*args, 3, "检查覆盖")
        claim = self.jobs.claim(self.pack, self.key)
        before = self.jobs.status(self.pack)["best_hash"]
        self.jobs.report_progress(*args, 12, "复核候选")
        self.jobs.claim(self.pack, self.key)
        status = self.jobs.status(self.pack)
        self.assertEqual(status["work"], {"checked": 12, "stage": "复核候选"})
        self.assertEqual(status["best_hash"], before)
        with self.assertRaises(ValueError):
            self.jobs.report_progress(*args, 11, "过期回报")
        candidate = json.loads(claim["prompt"].splitlines()[-1])
        candidate["solution"] = {"blocks": [[(i+d)%7+1 for d in (0,1,3)] for i in range(7)]}
        self.jobs.submit(*args, candidate)
        after = self.jobs.status(self.pack)
        self.assertNotIn("work", after)
        self.assertNotEqual(after["best_hash"], before)
        self.assertEqual(after["receipts"][0]["work"]["checked"], 12)
        with self.assertRaises(ValueError):
            self.jobs.report_progress(*args, 13, "迟到回报")

    def test_two_start_ids_for_one_active_job_never_create_another_on_retry(self):
        second = self.jobs.start(self.pack, 3, "mcp", "other-start")
        self.assertEqual(second["id"], self.key)
        self.jobs.stop(self.pack, self.key)
        self.assertEqual(self.jobs.start(self.pack, 3, "mcp", "other-start")["status"], "stopped")
