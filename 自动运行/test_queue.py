"""Exercise charge-sensitive queue boundaries without making a provider call."""
import json
from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

import run


class FixtureDriver:
    def __init__(self):
        self.calls = 0
        self.reads = 0
        self.break_inference = False
        self.meter_failure_at = 0
        self.credit_change_at = 0

    def snapshot(self):
        self.reads += 1
        if self.reads == self.meter_failure_at:
            raise OSError("test meter unavailable")
        changed = self.credit_change_at and self.reads >= self.credit_change_at
        return {"auth": "chatgpt", "ordinary_usage_allowed": True, "at": run.now(),
                "windows": [{"minutes": 10080, "reset": 2000000000, "used": 22}],
                "weekly_used": 22, "credits": "4.123" if changed else "5.123"}

    def infer(self, prompt, *, expected_before=None):
        self.calls += 1
        if self.break_inference:
            raise TimeoutError("test uncertain request")
        return {"text": 'this fixture is an invalid candidate', "usage": {
            "input_tokens": 20, "cached_input_tokens": 0, "output_tokens": 8}}


class QueueTest(unittest.TestCase):
    def setUp(self):
        temp = TemporaryDirectory()
        self.addCleanup(temp.cleanup)
        self.directory = Path(temp.name)
        self.plan = {"version": 1, "mode": "run", "packs": ["square-18"], "rounds": 2,
                     "model": None, "stop_at": 90, "monitored": True}
        self.driver = FixtureDriver()

    def execute(self, **options):
        run.execute(self.directory, self.plan, self.driver, **options)

    def record(self, turn):
        return json.loads((self.directory / f"square-18-{turn:02d}.json").read_text(encoding="utf-8"))

    def test_strict_mode_records_only_preflight(self):
        self.plan["monitored"] = False
        with self.assertRaisesRegex(ValueError, "严格模式"):
            self.execute()
        self.assertEqual(self.driver.calls, 0)
        self.assertEqual(self.record(1)["status"], "ready")

    def test_does_not_retry_an_uncertain_model_request(self):
        self.driver.break_inference = True
        with self.assertRaises(TimeoutError):
            self.execute()
        self.driver.break_inference = False
        with self.assertRaisesRegex(ValueError, "未重发"):
            self.execute()
        self.assertEqual(self.driver.calls, 1)
        self.execute(skip_uncertain=True)
        self.assertEqual((self.record(1)["status"], self.record(2)["status"]), ("skipped", "done"))
        self.assertEqual(self.driver.calls, 2)

    def test_recovers_missing_post_read_without_new_inference(self):
        self.driver.meter_failure_at = 2
        with self.assertRaisesRegex(RuntimeError, "后读数失败"):
            self.execute()
        self.assertFalse(self.record(1)["grade"]["accepted"])
        self.execute()
        self.assertEqual(self.driver.calls, 2)
        self.assertTrue(self.record(1)["delayed_after"])
        self.execute()
        self.assertEqual(self.driver.calls, 2)

    def test_credit_change_stops_queue_across_restarts(self):
        self.driver.credit_change_at = 2
        for _ in range(2):
            with self.assertRaisesRegex(ValueError, "余额有变动"):
                self.execute()
        self.assertEqual(self.driver.calls, 1)

    def test_second_process_cannot_enter_same_batch(self):
        with run.exclusive(self.directory):
            with self.assertRaisesRegex(ValueError, "另一个进程"):
                with run.exclusive(self.directory):
                    self.fail("unexpected second lock")


if __name__ == "__main__":
    unittest.main()
