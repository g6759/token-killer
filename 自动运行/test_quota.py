"""Quota safety tests: fixture-only reads, never a live model request."""
import importlib.util
import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event, Thread
from time import sleep, time
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener

SPEC = importlib.util.find_spec("quota")
if SPEC is not None:
    import quota
    import lab


def payload(ordinary=True, used=40):
    return {"ordinaryUsageAllowed": ordinary, "rateLimitsByLimitId": {"codex": {
        "primary": {"usedPercent": used, "windowDurationMins": 10080, "resetsAt": time() + 3600},
        "secondary": None, "credits": {"balance": "secret-credits"}}}}


class AvailabilityTest(unittest.TestCase):
    def test_quota_module_exists(self):
        self.assertIsNotNone(SPEC, "Quota guard is not implemented")


@unittest.skipIf(SPEC is None, "Quota module not implemented yet")
class QuotaTests(unittest.TestCase):
    def test_standalone_driver_cannot_bypass_unknown_shared_gate(self):
        import subscription_worker as worker
        gate = quota.QuotaGate(reader=lambda: quota.normalize_allowance({}))
        with patch.object(quota, "get_shared_gate", return_value=gate), \
                patch.object(worker, "get_subscription") as auth, \
                patch.object(worker, "_open_response") as send:
            with self.assertRaises(worker.PreflightError):
                worker.SubscriptionDriver().infer("local test puzzle")
            auth.assert_not_called()
            send.assert_not_called()

    def test_final_dispatch_hook_blocks_after_initial_permission(self):
        import benchmark
        import subscription_worker as worker
        calls, sent = [], []
        def guard():
            calls.append(1)
            if len(calls) > 1:
                raise quota.QuotaError("fixture exhausted")
        class Driver:
            def __init__(self, **kwargs):
                pass
            def prepare_model(self):
                return "fixture-model", "fixture-account"
            def infer(self, prompt, *, on_ready, **kwargs):
                on_ready()
                sent.append(True)
        def suite(workspace, directory, scenarios, infer, **kwargs):
            return infer({"prompt": "fixture", "pack": "relay-49-frontier"},
                         10, Event(), on_sent=lambda: sent.append("journal"))
        with patch.object(worker, "peek_setup_status", return_value={"ready": True}), \
                patch.object(worker, "SubscriptionDriver", Driver), \
                patch.object(benchmark, "_run_suite", side_effect=suite):
            with self.assertRaises(quota.QuotaError):
                benchmark.live_suite(Path("unused"), Path("unused"), before_infer=guard)
        self.assertEqual(calls, [1, 1])
        self.assertEqual(sent, [])

    def test_requires_explicit_ordinary_permission(self):
        raw = payload()
        result = quota.normalize_allowance(raw)
        self.assertEqual(result["status"], "available")
        self.assertEqual(result["windows"][0]["remaining_percent"], 60)
        self.assertNotIn("secret-credits", json.dumps(result))
        del raw["ordinaryUsageAllowed"]
        self.assertEqual(quota.normalize_allowance(raw)["status"], "unknown")
        self.assertEqual(quota.normalize_allowance(payload(False))["status"], "blocked")

    def test_all_applicable_windows_must_be_valid_and_remaining(self):
        raw = payload()
        raw["rateLimitsByLimitId"]["codex"]["secondary"] = {
            "usedPercent": 100, "windowDurationMins": 300, "resetsAt": time() + 600}
        self.assertEqual(quota.normalize_allowance(raw)["status"], "blocked")
        for invalid in ({}, {"usedPercent": float("nan")}, {"usedPercent": True}):
            raw["rateLimitsByLimitId"]["codex"]["secondary"] = invalid
            self.assertEqual(quota.normalize_allowance(raw)["status"], "unknown")
        raw["rateLimitsByLimitId"]["codex"]["primary"] = None
        raw["rateLimitsByLimitId"]["codex"]["secondary"] = None
        self.assertEqual(quota.normalize_allowance(raw)["status"], "unknown")

    def test_primary_bucket_preferred_over_legacy_and_expired_window_unknown(self):
        raw = payload()
        raw["rateLimits"] = {"primary": {"usedPercent": 100, "windowDurationMins": 300,
                                          "resetsAt": time() + 600}}
        self.assertEqual(quota.normalize_allowance(raw)["status"], "available")
        del raw["rateLimitsByLimitId"]
        self.assertEqual(quota.normalize_allowance(raw)["status"], "blocked")
        raw["rateLimits"]["primary"]["resetsAt"] = time() - 1
        self.assertEqual(quota.normalize_allowance(raw)["status"], "unknown")

    def test_offline_failure_is_redacted_unknown(self):
        with patch("weekly_meter._read_app_server", side_effect=OSError("private account details")):
            result = quota.read_allowance()
        self.assertEqual(result["status"], "unknown")
        self.assertNotIn("private account", json.dumps(result))

    def test_shared_freshness_and_failed_refresh_invalidate_previous_success(self):
        clock = [0.0]
        responses = [quota.normalize_allowance(payload()), quota.normalize_allowance({})]
        calls = []
        def reader():
            calls.append(1)
            sleep(0.01)
            return responses.pop(0)
        gate = quota.QuotaGate(reader=reader, clock=lambda: clock[0], max_age=30)
        with ThreadPoolExecutor(max_workers=8) as pool:
            self.assertEqual(list(pool.map(lambda _: gate.require()["status"], range(8))), ["available"] * 8)
        self.assertEqual(len(calls), 1)
        clock[0] = 31
        self.assertEqual(gate.snapshot()["status"], "unknown")
        with self.assertRaises(quota.QuotaError):
            gate.require()
        with self.assertRaises(quota.QuotaError):
            gate.require()
        self.assertEqual(len(calls), 2)


@unittest.skipIf(SPEC is None, "Quota module not implemented yet")
class LabQuotaTests(unittest.TestCase):
    def setUp(self):
        self.temp = TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        shared = patch.object(lab, "get_shared_gate", return_value=quota.QuotaGate(reader=lambda: lab.read_allowance()))
        shared.start()
        self.addCleanup(shared.stop)
        self.server = lab.LabServer(Path(self.temp.name), 0, {})
        self.addCleanup(self.server.server_close)
        ready = patch.object(lab, "peek_setup_status", return_value={"ready": True})
        ready.start()
        self.addCleanup(ready.stop)

    def test_http_refresh_contract_and_unknown_session_rejection(self):
        thread = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        thread.start()
        browser = build_opener(ProxyHandler({}))
        def post(path, data):
            return browser.open(Request(self.server.origin + path, data=json.dumps(data).encode(),
                headers={"Origin": self.server.origin, "Content-Type": "application/json"}), timeout=3)
        try:
            with patch.object(lab, "read_allowance", return_value=quota.normalize_allowance({})):
                with post("/api/lab/quota", {}) as response:
                    value = json.load(response)["allowance"]
                    self.assertEqual(value["status"], "unknown")
                    self.assertIn("sampled_at", value)
                    self.assertIn("message_en", value)
                with browser.open(self.server.origin + "/api/lab", timeout=3) as response:
                    self.assertEqual(json.load(response)["allowance"]["status"], "unknown")
                with self.assertRaises(HTTPError) as caught:
                    post("/api/lab/session", {"scenario": "cap-set", "windows": 1})
                self.assertEqual(caught.exception.code, 400)
        finally:
            self.server.shutdown()
            thread.join(timeout=3)

    def test_unknown_blocks_every_model_entry_but_replay_is_offline(self):
        with patch.object(lab, "read_allowance", return_value=quota.normalize_allowance({})), \
                patch.object(lab, "live_suite") as infer:
            for mode in ("live", "mixed", "resume", "continuous", "lorenz-divergence", "cap-set"):
                with self.subTest(mode=mode), self.assertRaises(quota.QuotaError):
                    self.server.start(mode)
            infer.assert_not_called()
        with patch.object(lab, "read_allowance", side_effect=AssertionError("offline quota read")), \
                patch.object(lab, "replay_suite", return_value={}) as replay:
            self.server.start("replay")
            self.server.task.result(timeout=2)
            replay.assert_called_once()

    def test_session_explicit_scenario_and_finite_windows(self):
        seen = []
        def complete(workspace, directory, scenarios, *, abort, before_infer, session_meter):
            before_infer()
            seen.append(list(scenarios))
            entry = {"stop": "window", "errors": [], "uncached_input_tokens": 20,
                     "output_tokens": 3, "metered_rounds": 1, "project_records": 1}
            report = {"live": True, "incomplete": False, "stopped": False,
                      "scenarios": {"cap-set": entry}}
            directory.mkdir(parents=True)
            (directory / "summary.json").write_text(json.dumps(report), encoding="utf-8")
            return report
        with patch.object(lab, "read_allowance", side_effect=lambda: quota.normalize_allowance(payload())), \
                patch.object(lab, "live_suite", side_effect=complete):
            self.server.start("session", scenario="cap-set", windows=2)
            self.server.task.result(timeout=3)
            self.assertEqual(seen, [["cap-set"], ["cap-set"]])
            self.assertEqual(self.server.continuous["effective_tokens"], 46)
            self.assertEqual(self.server.continuous["completed"], 2)
            self.assertEqual(self.server.continuous["end_reason"], "limit")
            for invalid in (0, -1, True, 1.5, 13):
                with self.subTest(windows=invalid), self.assertRaises(ValueError):
                    self.server.start("session", scenario="cap-set", windows=invalid)
            with self.assertRaises(ValueError):
                self.server.start("session", scenario="not-a-scenario", windows=1)

    def test_between_window_unknown_keeps_first_receipts_and_stops(self):
        reads = [quota.normalize_allowance(payload()), quota.normalize_allowance(payload()),
                 quota.normalize_allowance({})]
        def complete(workspace, directory, scenarios, *, abort, before_infer, session_meter):
            before_infer()
            return {"live": True, "incomplete": False, "stopped": False, "scenarios": {
                "mixed": {"stop": "window", "errors": [], "uncached_input_tokens": 7,
                          "output_tokens": 2, "metered_rounds": 1, "project_records": 1}}}
        with patch.object(lab, "read_allowance", side_effect=lambda: reads.pop(0)), \
                patch.object(lab, "live_suite", side_effect=complete) as infer:
            self.server.start("session", scenario="mixed", windows=2)
            self.server.task.result(timeout=3)
        self.assertEqual(infer.call_count, 1)
        self.assertEqual(self.server.continuous["effective_tokens"], 9)
        self.assertEqual(self.server.continuous["end_reason"], "quota")
        self.assertEqual(self.server.state()["allowance"]["status"], "unknown")

    def test_in_window_refresh_stops_and_keeps_confirmed_partial_totals(self):
        self.server.quota.max_age = 0.01
        reads = [quota.normalize_allowance(payload()), quota.normalize_allowance(payload()),
                 quota.normalize_allowance({})]
        def complete(workspace, directory, scenarios, *, abort, before_infer, session_meter):
            self.assertTrue(abort.wait(2), "quota watcher did not stop an active window")
            return {"live": True, "incomplete": True, "stopped": True, "scenarios": {
                "mixed": {"stop": "stopped", "errors": [], "uncached_input_tokens": 11,
                          "output_tokens": 3, "metered_rounds": 1, "project_records": 1}}}
        with patch.object(lab, "MAX_AGE_SECONDS", 0.02), \
                patch.object(lab, "read_allowance", side_effect=lambda: reads.pop(0)), \
                patch.object(lab, "live_suite", side_effect=complete) as infer:
            self.server.start("session", scenario="mixed", windows=2)
            self.server.task.result(timeout=3)
        self.assertEqual(infer.call_count, 1)
        self.assertEqual(self.server.continuous["effective_tokens"], 14)
        self.assertEqual(self.server.continuous["new_records"], 1)
        self.assertEqual(self.server.continuous["end_reason"], "quota")


if __name__ == "__main__":
    unittest.main()
