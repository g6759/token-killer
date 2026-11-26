import json
from pathlib import Path
import sys
import time
import unittest
from unittest.mock import patch


def serve(case):
    def send(message):
        print(json.dumps(message), flush=True)

    for line in sys.stdin:
        request = json.loads(line)
        method = request["method"]
        params = request.get("params", {})
        if "id" not in request:
            continue
        result = {}
        if method == "account/read":
            assert params == {"refreshToken": False}
            result = {"account": {"type": "apiKey" if case == "api" else "chatgpt",
                                  "email": "fixture@example.test", "planType": "plus"},
                      "requiresOpenaiAuth": True, "userId": "fixture-user"}
        elif method == "account/rateLimits/read":
            assert case != "api"
            credits = {"hasCredits": case == "paid", "unlimited": False,
                       "balance": "0.12345678901234567890" if case == "paid" else "0.000"}
            limit = {"limitId": "codex",
                     "primary": {"usedPercent": 23, "windowDurationMins": 300, "resetsAt": 12345},
                     "secondary": {"usedPercent": 51.5, "windowDurationMins": 10080, "resetsAt": 12999},
                     "credits": None if case == "unknown" else credits}
            result = {"rateLimits": limit, "rateLimitsByLimitId": {"codex": limit},
                      "ordinaryUsageAllowed": False if case == "exhausted" else True,
                      "accountId": "fixture-account"}
        elif method == "thread/start":
            assert params["modelProvider"] == "openai"
            assert params["environments"] == []
            assert params["dynamicTools"] == []
            assert params["ephemeral"] is True
            assert params["approvalPolicy"] == "never"
            assert params["allowProviderModelFallback"] is False
            model = "gpt-fiction" if case == "mismatch" else params.get("model", "gpt-default")
            result = {"thread": {"id": "fixture-thread", "ephemeral": True,
                                 "environments": None if case == "environment" else []},
                      "model": model, "modelProvider": "openai", "approvalPolicy": "never",
                      "sandbox": {"type": "readOnly", "networkAccess": False},
                      "serviceTier": None}
        elif method == "turn/start":
            assert case not in {"api", "exhausted", "mismatch", "unknown", "environment"}
            assert params["input"][0]["text"] == "请给出候选"
            assert params["environments"] == []
            assert params["outputSchema"]["required"] == ["pack_id", "parent_hash", "solution", "handoff"]
            assert params["serviceTierForTurn"] == "default"
            if case == "timeout":
                time.sleep(30)
            event = {"threadId": "fixture-thread", "turnId": "fixture-turn"}
            if case == "approval":
                send({"id": 99, "method": "item/commandExecution/requestApproval", "params": event})
                continue
            if case == "tool":
                send({"method": "item/started", "params": {
                    **event, "item": {"type": "commandExecution", "id": "fixture-cmd"}}})
                continue
            candidate = {"pack_id": "p1", "solution": {"blocks": [[1, 2]]},
                         "parent_hash": "abc", "handoff": ""}
            msg = {"type": "agentMessage", "id": "fixture-msg", "phase": "final_answer",
                   "text": json.dumps(candidate) if case != "invalid" else "just text"}
            send({"method": "item/completed", "params": {**event, "item": msg}})
            send({"method": "thread/tokenUsage/updated", "params": {
                **event, "tokenUsage": {"last": {"inputTokens": 12, "outputTokens": 6,
                                                "totalTokens": 18, "cachedInputTokens": 3}}}})
            send({"method": "turn/completed", "params": {
                **event, "turn": {"id": "fixture-turn", "status": "completed", "items": []}}})
            # The client may receive turn events before turn/start's response.
            result = {"turn": {"id": "fixture-turn"}}
        elif method == "turn/interrupt":
            pass
        elif method != "initialize":
            raise AssertionError(method)
        send({"id": request["id"], "result": result})


class DriverTest(unittest.TestCase):
    def driver(self, case, **kwargs):
        command = [sys.executable, "-u", str(Path(__file__).resolve()), "--fixture", case]
        patcher = patch("codex_driver._command", return_value=command)
        patcher.start()
        self.addCleanup(patcher.stop)
        return CodexDriver(**kwargs)

    def test_snapshot_has_metered_week_and_redacts_identifiers(self):
        result = self.driver("paid").snapshot()
        self.assertEqual(result["auth"], "chatgpt")
        self.assertEqual(result["weekly_used"], 51.5)
        self.assertEqual(result["credits"], "0.12345678901234567890")
        self.assertTrue(result["ordinary_usage_allowed"])
        self.assertEqual(result["windows"], [
            {"used": 23, "minutes": 300, "reset": 12345},
            {"used": 51.5, "minutes": 10080, "reset": 12999},
        ])
        self.assertNotIn("fixture", json.dumps(result))

    def test_snapshot_refuses_api_key_login(self):
        with self.assertRaisesRegex(DriverError, "ChatGPT"):
            self.driver("api").snapshot()

    def test_infer_accepts_one_structured_candidate_and_usage(self):
        result = self.driver("ok", model="gpt-test").infer("请给出候选")
        self.assertEqual(result["model"], "gpt-test")
        self.assertEqual(json.loads(result["text"])["solution"], {"blocks": [[1, 2]]})
        self.assertEqual(result["usage"]["total_tokens"], 18)

    def test_infer_does_not_submit_after_exhaustion(self):
        with self.assertRaisesRegex(PreflightError, "套餐"):
            self.driver("exhausted").infer("请给出候选")

    def test_default_refuses_positive_or_unconfirmed_balance(self):
        for case in ("paid", "unknown"):
            with self.subTest(case=case), self.assertRaisesRegex(DriverError, "余额"):
                self.driver(case).infer("请给出候选")

    def test_monitored_accepts_existing_balance_when_included_usage_is_allowed(self):
        result = self.driver("paid", monitored=True).infer("请给出候选")
        self.assertIn('"pack_id"', result["text"])

    def test_infer_refuses_model_fallback(self):
        with self.assertRaisesRegex(DriverError, "模型"):
            self.driver("mismatch", model="gpt-test").infer("请给出候选")

    def test_infer_interrupts_tool_requests_and_tool_started_events(self):
        for case in ("approval", "tool"):
            with self.subTest(case=case), self.assertRaisesRegex(DriverError, "工具"):
                self.driver(case).infer("请给出候选")

    def test_infer_retains_non_json_candidate_and_usage_for_the_judge(self):
        result = self.driver("invalid").infer("请给出候选")
        self.assertEqual(result["text"], "just text")
        self.assertEqual(result["usage"]["total_tokens"], 18)

    def test_infer_requires_disabled_runtime_environment_on_readback(self):
        with self.assertRaisesRegex(PreflightError, "空执行环境"):
            self.driver("environment").infer("请给出候选")

    def test_infer_honors_weekly_threshold_at_its_own_preflight(self):
        with self.assertRaisesRegex(PreflightError, "停止线"):
            self.driver("ok", stop_at=50).infer("请给出候选")

    def test_infer_rejects_changed_window_after_initial_read(self):
        driver = self.driver("ok")
        first = driver.snapshot()
        first["windows"][0]["reset"] += 1
        with self.assertRaisesRegex(PreflightError, "窗口"):
            driver.infer("请给出候选", expected_before=first)

    def test_infer_rejects_changed_credit_after_initial_read(self):
        driver = self.driver("ok")
        first = driver.snapshot()
        first["credits"] = "0.001"
        with self.assertRaisesRegex(PreflightError, "余额"):
            driver.infer("请给出候选", expected_before=first)

    def test_deadline_stops_unresponsive_subprocess(self):
        started = time.monotonic()
        with self.assertRaisesRegex(DriverError, "超时"):
            self.driver("timeout", timeout=0.8).infer("请给出候选")
        self.assertLess(time.monotonic() - started, 6)


if __name__ == "__main__" and sys.argv[1:2] == ["--fixture"]:
    serve(sys.argv[2])
else:
    from codex_driver import CodexDriver, DriverError, PreflightError

    if __name__ == "__main__":
        unittest.main()
