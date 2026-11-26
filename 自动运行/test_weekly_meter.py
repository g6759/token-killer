import json
from pathlib import Path
from tempfile import TemporaryDirectory
import threading
import time
import unittest
from concurrent.futures import ThreadPoolExecutor

from weekly_meter import WindowCounter, build_snapshot, compare_snapshots, observe_window, write_snapshot


class WeeklyMeterTests(unittest.TestCase):
    def test_window_counter_accepts_only_measured_unique_responses(self):
        counter = WindowCounter()
        reply = {"response_id": "response-1", "usage": {
            "input_tokens": 100, "cached_input_tokens": 40, "output_tokens": 20}}
        with ThreadPoolExecutor(max_workers=8) as workers:
            accepted = list(workers.map(counter.add, [reply] * 40))
        self.assertEqual(sum(accepted), 1)
        self.assertFalse(counter.add({"response_id": "", "usage": reply["usage"]}))
        self.assertFalse(counter.add({"response_id": "response-2", "usage": {
            **reply["usage"], "cached_input_tokens": 101}}))
        self.assertFalse(counter.add({"response_id": "response-3", "usage": {
            **reply["usage"], "output_tokens": True}}))
        self.assertEqual(counter.snapshot(), {
            "response_count": 1, "uncached_input_tokens": 60,
            "output_tokens": 20, "effective_game_tokens": 80})

    def test_full_window_samples_in_background_and_stops_on_exit(self):
        counter = WindowCounter()
        first = threading.Event()
        records = []
        threads = []
        main_thread = threading.get_ident()

        def read_only_snapshot():
            threads.append(threading.get_ident())
            return build_snapshot(None, error="weekly_window_unavailable")

        def emit(record):
            threads.append(threading.get_ident())
            records.append(record)
            first.set()

        with observe_window(time.monotonic(), 600, read_only_snapshot, emit, counter, interval=0.03):
            self.assertTrue(counter.add({"response_id": "r1", "usage": {
                "input_tokens": 7, "cached_input_tokens": 2, "output_tokens": 3}}))
            self.assertTrue(first.wait(1))
        observed_count = len(records)
        time.sleep(0.1)
        self.assertEqual(len(records), observed_count)
        self.assertTrue(threads)
        self.assertTrue(all(ident != main_thread for ident in threads))
        self.assertEqual(records[0]["receipt_counter"]["effective_game_tokens"], 8)
        self.assertIsNone(records[0]["account_snapshot"]["codex_account_shared_weekly"])
        self.assertNotIn("percentage_point_change", records[0])

    def test_short_window_does_not_read_account(self):
        counter = WindowCounter()
        with observe_window(time.monotonic(), 599, lambda: self.fail("snapshot called"),
                            lambda _: self.fail("emit called"), counter, interval=0.01):
            time.sleep(0.03)

    def test_snapshot_and_emit_errors_do_not_affect_game(self):
        counter = WindowCounter()
        called = threading.Event()

        def fails():
            called.set()
            raise OSError("read failed")

        def also_fails(_):
            raise OSError("write failed")

        with observe_window(time.monotonic(), 600, fails, also_fails, counter, interval=0.02):
            self.assertTrue(called.wait(1))

    def test_snapshot_keeps_account_week_and_local_cap_separate(self):
        result = build_snapshot(
            {"rateLimitsByLimitId": {"codex": {
                "primary": {"usedPercent": 17, "windowDurationMins": 300, "resetsAt": 10},
                "secondary": {"usedPercent": 41.5, "windowDurationMins": 10080, "resetsAt": 20},
            }}},
            app_weekly_limit_percent=65,
            sampled_at_utc="2026-11-23T00:00:00Z",
            source="fixture",
        )
        self.assertEqual(result["codex_account_shared_weekly"], {
            "used_percent": 41.5, "window_duration_minutes": 10080,
            "resets_at": 20, "sampled_at_utc": "2026-11-23T00:00:00Z", "source": "fixture",
        })
        self.assertEqual(result["application_local_declaration"], {
            "weekly_limit_percent": 65, "actual_used_percent": None,
            "source": "local_configuration",
        })

    def test_compare_calculates_percentage_points_only_for_same_window(self):
        before = {"codex_account_shared_weekly": {
            "used_percent": 40, "window_duration_minutes": 10080, "resets_at": 123,
        }}
        after = {"codex_account_shared_weekly": {
            "used_percent": 43.5, "window_duration_minutes": 10080, "resets_at": 123,
        }}
        self.assertEqual(compare_snapshots(before, after), {
            "comparable": True, "percentage_point_change": 3.5,
        })
        after["codex_account_shared_weekly"]["resets_at"] = 456
        self.assertEqual(compare_snapshots(before, after), {
            "comparable": False, "percentage_point_change": None,
        })

    def test_write_snapshot_persists_only_declared_fields(self):
        snapshot = build_snapshot(None, app_weekly_limit_percent=50, error="unavailable")
        with TemporaryDirectory() as scratch:
            target = Path(scratch) / "meter.json"
            write_snapshot(target, snapshot)
            saved = json.loads(target.read_text(encoding="utf-8"))
        self.assertEqual(saved["codex_account_shared_weekly"], None)
        self.assertIsNone(saved["application_local_declaration"]["actual_used_percent"])
        self.assertNotIn("token", json.dumps(saved).lower())
        self.assertNotIn("collection_error", saved)


if __name__ == "__main__":
    unittest.main()
