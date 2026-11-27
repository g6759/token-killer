import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import build_opener, ProxyHandler, Request

import lab


class LabTests(unittest.TestCase):
    def test_target_does_not_extend_window_limit_or_invent_missing_usage(self):
        with TemporaryDirectory() as directory:
            def run(workspace, output, scenarios, *, session_meter, **kwargs):
                session_meter.add({"response_id": "one", "usage": {"input_tokens": 10,
                                  "cached_input_tokens": 0, "output_tokens": 0}})
                return {"scenarios": {"single": {"live": True, "stop": "window", "errors": [],
                    "uncached_input_tokens": 10, "output_tokens": 0, "metered_rounds": 1,
                    "project_records": 0, "tokens_per_minute": 1}}, "incomplete": False, "stopped": False}
            with lab.launch(Path(directory), register=False) as server, \
                    patch.object(lab, "peek_setup_status", return_value={"ready": True}), \
                    patch.object(lab, "live_suite", side_effect=run) as runner:
                server.start("session", scenario="single", windows=1, target_tokens=100)
                server.task.result(timeout=5)
                runner.assert_called_once()
                self.assertEqual(server.continuous["end_reason"], "limit")
                self.assertFalse(server.continuous["target_reached"])
                with patch.object(lab, "live_suite", return_value={"scenarios": {"single": {
                    "live": True, "stop": "window", "errors": [], "metered_rounds": 0}},
                    "incomplete": True, "stopped": False}):
                    server.start("session", scenario="single", windows=3, target_tokens=100)
                    server.task.result(timeout=5)
                self.assertEqual(server.continuous["end_reason"], "interrupted")
                self.assertEqual(server.continuous["effective_tokens"], 0)
                self.assertIsNone(server.continuous["recent_tokens_per_minute"])

    def test_target_accumulates_across_windows_and_stops_without_state_poll(self):
        with TemporaryDirectory() as directory:
            calls = []
            def run(workspace, output, scenarios, *, session_meter, **kwargs):
                calls.append(output)
                session_meter.add({"response_id": "reply-" + str(len(calls)), "usage": {
                    "input_tokens": 80, "cached_input_tokens": 0, "output_tokens": 20}})
                entry = {"live": True, "stop": "target" if session_meter.reached.is_set() else "window",
                         "uncached_input_tokens": 80, "output_tokens": 20, "metered_rounds": 1,
                         "project_records": 0, "tokens_per_minute": 10, "wall_seconds": 600, "errors": []}
                return {"scenarios": {"single": entry}, "incomplete": session_meter.reached.is_set(), "stopped": False}
            with lab.launch(Path(directory), register=False) as server, \
                    patch.object(lab, "peek_setup_status", return_value={"ready": True}), \
                    patch.object(lab, "live_suite", side_effect=run):
                for invalid in (True, 0, -1, 1.5, "10", 2**53):
                    with self.assertRaises(ValueError):
                        server.start("session", scenario="single", windows=3, target_tokens=invalid)
                server.start("session", scenario="single", windows=3, target_tokens=150)
                server.task.result(timeout=5)
                self.assertEqual(len(calls), 2)
                session = server.state()["continuous"]
                self.assertEqual(session["end_reason"], "target")
                self.assertEqual(session["effective_tokens"], 200)
                self.assertTrue(session["target_reached"])

    def setUp(self):
        # All historical HTTP tests use a fixture; no account read or model call.
        from quota import QuotaGate, normalize_allowance
        from time import time
        def reading():
            return normalize_allowance({"ordinaryUsageAllowed": True, "rateLimits": {
                "primary": {"usedPercent": 40, "windowDurationMins": 10080, "resetsAt": time() + 3600}}})
        gate = patch.object(lab, "get_shared_gate", side_effect=lambda: QuotaGate(reader=reading))
        gate.start()
        self.addCleanup(gate.stop)

    def test_lab_state_reads_latest_weekly_meter_without_scanning_receipt_subdirectories(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            archives = data / "验收"
            old = archives / "run-old"
            newest = archives / "run-new"
            old.mkdir(parents=True)
            newest.mkdir(parents=True)
            old_meter = old / "relay-old-周限额-1.json"
            new_meter = newest / "relay-frontier-周限额-2.json"
            old_meter.write_text(json.dumps({"game_window": "older"}), encoding="utf-8")
            meter = {
                "game_window": "relay-frontier-384-probe",
                "before": {
                    "codex_account_shared_weekly": {
                        "used_percent": 37, "window_duration_minutes": 10080,
                        "resets_at": 1795692728, "sampled_at_utc": "2026-11-23T08:10:16Z",
                        "source": "Codex app-server account/rateLimits/read",
                    },
                    "application_local_declaration": {
                        "weekly_limit_percent": 78, "actual_used_percent": None,
                        "source": "local_configuration",
                    },
                },
                "after": {
                    "codex_account_shared_weekly": {
                        "used_percent": 38.25, "window_duration_minutes": 10080,
                        "resets_at": 1795692728, "sampled_at_utc": "2026-11-23T08:11:28Z",
                        "source": "Codex app-server account/rateLimits/read",
                    },
                    "application_local_declaration": {
                        "weekly_limit_percent": 78, "actual_used_percent": None,
                        "source": "local_configuration",
                    },
                },
                "comparable": True, "percentage_point_change": 1.25,
                "effective_game_tokens": 435648,
            }
            new_meter.write_text(json.dumps(meter), encoding="utf-8")
            nested = newest / "lane-1"
            nested.mkdir()
            (nested / "receipt.json").write_text("{}", encoding="utf-8")
            (nested / "nested-周限额-99.json").write_text(
                json.dumps({"game_window": "must-not-be-read"}), encoding="utf-8")
            with patch.object(lab, "peek_setup_status", return_value={"ready": True}):
                with lab.launch(data, register=False) as server:
                    status = server.state()
            self.assertEqual(status["weekly_meter"], meter)

    def test_lab_state_has_clear_empty_weekly_meter_state(self):
        with TemporaryDirectory() as directory:
            with patch.object(lab, "peek_setup_status", return_value={"ready": False}):
                with lab.launch(Path(directory), register=False) as server:
                    self.assertIsNone(server.state()["weekly_meter"])

    def test_continuous_record_run_saves_each_window_and_keeps_the_actual_totals(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            browser = build_opener(ProxyHandler({}))
            outputs = []

            def complete(workspace, output, scenarios, *, abort, before_infer, session_meter):
                before_infer()
                self.assertEqual(scenarios, {"mixed": lab.WINDOWS["mixed"]})
                self.assertFalse(abort.is_set())
                outputs.append(output)
                output.mkdir(parents=True)
                report = {"live": True, "incomplete": False, "stopped": False,
                          "scenarios": {"mixed": {"live": True, "stop": "window", "errors": [],
                                                  "uncached_input_tokens": 35, "output_tokens": 17,
                                                  "metered_rounds": 2, "project_records": 1}}}
                (output / "summary.json").write_text(json.dumps(report), encoding="utf-8")
                return report

            with lab.launch(data, register=False) as server, \
                    patch.object(lab, "peek_setup_status", return_value={"ready": True}), \
                    patch.object(lab, "live_suite", side_effect=complete):
                request = Request(server.origin + "/api/lab/continuous", data=b'{"windows": 2}', headers={
                    "Content-Type": "application/json", "Origin": server.origin,
                })
                with browser.open(request, timeout=5) as response:
                    self.assertEqual(response.status, 202)
                server.task.result(timeout=5)
                status = server.state()
                self.assertEqual(status["run"], "idle")
                session = status["continuous"]
                self.assertEqual((session["completed"], session["effective_tokens"], session["metered_rounds"]),
                                 (2, 104, 4))
                self.assertEqual((session["new_records"], session["end_reason"]), (2, "limit"))
                self.assertEqual(len(set(outputs)), 2)
                self.assertTrue(all((output / "summary.json").is_file() for output in outputs))
            with lab.launch(data, register=False) as restored:
                status = restored.state()
                self.assertEqual(status["run"], "idle")
                for key in ("completed", "effective_tokens", "metered_rounds", "end_reason", "target_tokens"):
                    self.assertEqual(status["continuous"][key], session[key])

    def test_continuous_record_run_keeps_partial_receipts_when_provider_stops(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            stop_report = {"live": True, "incomplete": True, "stopped": True,
                           "scenarios": {"mixed": {"live": True, "stop": "error", "errors": ["订阅用量暂不可用"],
                                                   "uncached_input_tokens": 23, "output_tokens": 4,
                                                   "metered_rounds": 1, "project_records": 0}}}
            with lab.launch(data, register=False) as server, \
                    patch.object(lab, "peek_setup_status", return_value={"ready": True}), \
                    patch.object(lab, "live_suite", return_value=stop_report) as inference:
                server.start("continuous")
                server.task.result(timeout=5)
                session = server.state()["continuous"]
                self.assertEqual((session["completed"], session["effective_tokens"]), (0, 27))
                self.assertEqual((session["end_reason"], session["message"]), ("interrupted", "订阅用量暂不可用"))
                inference.assert_called_once()

    def test_continuous_stop_at_window_handoff_preserves_receipts_without_starting_again(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            written, release = Event(), Event()

            def complete(workspace, output, scenarios, *, abort, before_infer, session_meter):
                before_infer()
                entry = {"stop": "window", "errors": [], "uncached_input_tokens": 28, "output_tokens": 11,
                         "metered_rounds": 3, "project_records": 0}
                report = {"live": True, "incomplete": False, "stopped": False, "scenarios": {"mixed": entry}}
                output.mkdir(parents=True)
                (output / "summary.json").write_text(json.dumps(report), encoding="utf-8")
                written.set()
                if not release.wait(timeout=5):
                    raise RuntimeError("测试超时")
                return report

            with lab.launch(data, register=False) as server, \
                    patch.object(lab, "peek_setup_status", return_value={"ready": True}), \
                    patch.object(lab, "live_suite", side_effect=complete) as inference:
                server.start("continuous")
                try:
                    self.assertTrue(written.wait(timeout=5))
                    self.assertEqual(server.state()["continuous"]["effective_tokens"], 39)
                    self.assertEqual(server.stop()["run"], "stopping")
                finally:
                    release.set()
                server.task.result(timeout=5)
                session = server.state()["continuous"]
                self.assertEqual((session["completed"], session["effective_tokens"]), (1, 39))
                self.assertEqual(session["end_reason"], "stopped")
                inference.assert_called_once()

    def test_relay_concurrency_comparisons_keep_the_same_checkpoint(self):
        cases = (
            ("relay-scale-comparison", "relay_scale_benchmark", "relay-twenty-four", 24, "relay-sixteen", 16),
            ("relay-twenty-lane-comparison", "relay_twenty_lane_benchmark", "relay-twenty", 20, "relay-sixteen", 16),
            ("relay-twenty-two-lane-comparison", "relay_twenty_two_lane_benchmark", "relay-twenty-two", 22, "relay-twenty", 20),
            ("relay-twenty-three-lane-comparison", "relay_twenty_three_lane_benchmark",
             "relay-twenty-three", 23, "relay-twenty-two", 22),
            ("relay-target-comparison", "relay_target_benchmark", "relay-dual-target", 22, "relay-twenty-two", 22),
        )
        for mode, field, high, high_lanes, low, low_lanes in cases:
            with self.subTest(mode=mode), TemporaryDirectory() as directory:
                data = Path(directory)
                browser = build_opener(ProxyHandler({}))
                report = {"live": True, "winner": high, "incomplete": False,
                          "scenarios": {high: {"tokens_per_minute": 240000, "rankable": True},
                                        low: {"tokens_per_minute": 190000, "rankable": True}}}

                def archive(workspace, output, scenarios, **_):
                    self.assertEqual(list(scenarios), [high, low])
                    self.assertEqual(scenarios[high], ("relay-49-frontier",) * high_lanes)
                    self.assertEqual(scenarios[low], ("relay-49-frontier",) * low_lanes)
                    output.mkdir(parents=True)
                    (output / "summary.json").write_text(json.dumps(report), encoding="utf-8")
                    return report

                with lab.launch(data, register=False) as server, \
                        patch.object(lab, "peek_setup_status", return_value={"ready": True}), \
                        patch.object(lab, "live_suite", side_effect=archive):
                    request = Request(server.origin + f"/api/lab/{mode}", data=b"{}", headers={
                        "Content-Type": "application/json", "Origin": server.origin,
                    })
                    with browser.open(request, timeout=5) as response:
                        self.assertEqual(response.status, 202)
                    server.task.result(timeout=5)
                    with browser.open(server.origin + "/api/lab", timeout=5) as response:
                        state = json.load(response)
                    self.assertEqual(state[field], report)
                    self.assertEqual(state["featured_mixed"], high)
                    self.assertEqual(state.get("featured_estimate_tokens"), 2400000)

    def test_relay_mix_changes_only_pack_assignments_and_keeps_the_fastest_verified_default(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            browser = build_opener(ProxyHandler({}))
            prior = {"live": True, "winner": "mixed-portfolio", "incomplete": False,
                     "scenarios": {"mixed-portfolio": {"tokens_per_minute": 210000, "rankable": True}}}
            previous = data / "验收" / "吞吐对照-实跑-多题组合-历史" / "summary.json"
            previous.parent.mkdir(parents=True)
            previous.write_text(json.dumps(prior), encoding="utf-8")
            report = {"live": True, "winner": "relay-sixteen", "incomplete": False,
                      "scenarios": {"mixed-portfolio": {"tokens_per_minute": 177000, "rankable": True},
                                    "relay-sixteen": {"tokens_per_minute": 194000, "rankable": True}}}

            def archive(workspace, output, scenarios, **_):
                self.assertEqual(scenarios["mixed-portfolio"], lab.WINDOWS["mixed-portfolio"])
                self.assertEqual(scenarios["relay-sixteen"], ("relay-49-frontier",) * 16)
                output.mkdir(parents=True)
                (output / "summary.json").write_text(json.dumps(report), encoding="utf-8")
                return report

            with lab.launch(data, register=False) as server, \
                    patch.object(lab, "peek_setup_status", return_value={"ready": True}), \
                    patch.object(lab, "live_suite", side_effect=archive):
                request = Request(server.origin + "/api/lab/relay-mix-comparison", data=b"{}", headers={
                    "Content-Type": "application/json", "Origin": server.origin,
                })
                with browser.open(request, timeout=5) as response:
                    self.assertEqual(response.status, 202)
                server.task.result(timeout=5)
                with browser.open(server.origin + "/api/lab", timeout=5) as response:
                    state = json.load(response)
                self.assertEqual(state["relay_mix_benchmark"], report)
                self.assertEqual(state["featured_mixed"], "mixed-portfolio")

    def test_incomplete_comparison_still_shows_a_previous_attempt_for_the_later_window(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            folder = data / "验收" / "吞吐对照-实跑-多题并发-历史"
            previous = {"live": True, "stop": "error",
                        "packs": list(lab.WINDOWS["mixed-portfolio-twenty-four"]),
                        "tokens_per_minute": 200000, "comparable": False}
            second = folder / "mixed-portfolio-twenty-four" / "summary.json"
            second.parent.mkdir(parents=True)
            second.write_text(json.dumps(previous), encoding="utf-8")
            report = {"live": True, "scenarios": {"mixed-portfolio": {"stop": "error"}},
                      "winner": None, "incomplete": True}
            (folder / "summary.json").write_text(json.dumps(report), encoding="utf-8")

            with lab.launch(data, register=False) as server:
                result = server.state()["portfolio_scale_benchmark"]
                self.assertEqual(result["scenarios"].get("mixed-portfolio-twenty-four"), previous)
                self.assertIsNone(result["winner"])
                self.assertTrue(result["incomplete"])

    def test_scaling_comparison_archives_the_24_lane_result_and_keeps_the_previous_report(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            browser = build_opener(ProxyHandler({}))
            previous = {"live": True, "scenarios": {"mixed-sixteen": {}, "mixed-portfolio": {}},
                        "winner": "mixed-portfolio", "incomplete": False}
            report = {"live": True, "scenarios": {"mixed-portfolio": {}, "mixed-portfolio-twenty-four": {}},
                      "winner": "mixed-portfolio-twenty-four", "incomplete": False}
            old = data / "验收" / "吞吐对照-实跑-多题组合-历史" / "summary.json"
            old.parent.mkdir(parents=True)
            old.write_text(json.dumps(previous), encoding="utf-8")

            def archive(workspace, output, scenarios, **_):
                self.assertEqual({name: len(packs) for name, packs in scenarios.items()},
                                 {"mixed-portfolio": 16, "mixed-portfolio-twenty-four": 24})
                output.mkdir(parents=True)
                (output / "summary.json").write_text(json.dumps(report), encoding="utf-8")
                return report

            with lab.launch(data, register=False) as server, \
                    patch.object(lab, "peek_setup_status", return_value={"ready": True}), \
                    patch.object(lab, "live_suite", side_effect=archive):
                request = Request(server.origin + "/api/lab/portfolio-scale-comparison", data=b"{}", headers={
                    "Content-Type": "application/json", "Origin": server.origin,
                })
                with browser.open(request, timeout=5) as response:
                    self.assertEqual(response.status, 202)
                server.task.result(timeout=5)
                with browser.open(server.origin + "/api/lab", timeout=5) as response:
                    state = json.load(response)
                self.assertEqual(state["portfolio_benchmark"], previous)
                self.assertEqual(state["portfolio_scale_benchmark"], report)
                self.assertEqual(state["featured_mixed"], "mixed-portfolio-twenty-four")

    def test_portfolio_comparison_has_sixteen_lanes_and_preserves_the_previous_report(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            browser = build_opener(ProxyHandler({}))
            previous = {"live": True, "scenarios": {"mixed-eight": {}, "mixed-sixteen": {}},
                        "winner": "mixed-sixteen", "incomplete": False}
            report = {"live": True, "scenarios": {"mixed-sixteen": {}, "mixed-portfolio": {}},
                      "winner": "mixed-portfolio", "incomplete": False}
            old = data / "验收" / "吞吐对照-实跑-八路十六路-历史" / "summary.json"
            old.parent.mkdir(parents=True)
            old.write_text(json.dumps(previous), encoding="utf-8")

            def archive(workspace, output, scenarios, **_):
                relay = "relay-49-frontier"
                self.assertEqual(scenarios["mixed-sixteen"], ("square-26", relay) * 8)
                self.assertEqual(scenarios["mixed-portfolio"], ("square-26", relay) * 2
                                 + ("square-37", relay) * 2 + ("square-41", relay) * 2
                                 + ("square-50", relay) * 2)
                output.mkdir(parents=True)
                (output / "summary.json").write_text(json.dumps(report), encoding="utf-8")
                return report

            with lab.launch(data, register=False) as server, \
                    patch.object(lab, "peek_setup_status", return_value={"ready": True}), \
                    patch.object(lab, "live_suite", side_effect=archive):
                request = Request(server.origin + "/api/lab/portfolio-comparison", data=b"{}", headers={
                    "Content-Type": "application/json", "Origin": server.origin,
                })
                with browser.open(request, timeout=5) as response:
                    self.assertEqual(response.status, 202)
                server.task.result(timeout=5)
                with browser.open(server.origin + "/api/lab", timeout=5) as response:
                    state = json.load(response)
                self.assertEqual(state["sixteen_lane_benchmark"], previous)
                self.assertEqual(state["portfolio_benchmark"], report)
                self.assertEqual(state["featured_mixed"], "mixed-portfolio")

    def test_eight_lane_trial_keeps_its_own_result_and_selects_the_winner(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            browser = build_opener(ProxyHandler({}))
            low = {"live": True, "scenarios": {"mixed": {}, "mixed-four": {}},
                   "winner": "mixed-four", "incomplete": False}
            high = {"live": True, "scenarios": {"mixed-four": {}, "mixed-eight": {}},
                    "winner": "mixed-eight", "incomplete": False}
            base = data / "验收" / "吞吐对照-实跑-并发度-低档" / "summary.json"
            base.parent.mkdir(parents=True)
            base.write_text(json.dumps(low), encoding="utf-8")
            packs = ("square-26", "relay-49-frontier")

            def archive(workspace, output, scenarios, **_):
                self.assertEqual(scenarios, {"mixed-four": packs * 2, "mixed-eight": packs * 4})
                output.mkdir(parents=True)
                (output / "summary.json").write_text(json.dumps(high), encoding="utf-8")
                return high

            with lab.launch(data, register=False) as server, \
                    patch.object(lab, "peek_setup_status", return_value={"ready": True}), \
                    patch.object(lab, "live_suite", side_effect=archive):
                request = Request(server.origin + "/api/lab/high-concurrency", data=b"{}", headers={
                    "Content-Type": "application/json", "Origin": server.origin,
                })
                with browser.open(request, timeout=5) as response:
                    self.assertEqual(response.status, 202)
                server.task.result(timeout=5)
                base.write_text(json.dumps(low), encoding="utf-8")
                with browser.open(server.origin + "/api/lab", timeout=5) as response:
                    state = json.load(response)
                self.assertEqual(state["concurrency_benchmark"], low)
                self.assertEqual(state["high_concurrency_benchmark"], high)
                self.assertEqual(state["featured_mixed"], "mixed-eight")

                highest = {"live": True, "scenarios": {"mixed-eight": {}, "mixed-sixteen": {}},
                           "winner": "mixed-sixteen", "incomplete": False}

                def archive_sixteen(workspace, output, scenarios, **_):
                    self.assertEqual(scenarios, {"mixed-eight": packs * 4, "mixed-sixteen": packs * 8})
                    output.mkdir(parents=True)
                    (output / "summary.json").write_text(json.dumps(highest), encoding="utf-8")
                    return highest

                with patch.object(lab, "live_suite", side_effect=archive_sixteen):
                    request = Request(server.origin + "/api/lab/sixteen-lane-comparison", data=b"{}", headers={
                        "Content-Type": "application/json", "Origin": server.origin,
                    })
                    with browser.open(request, timeout=5) as response:
                        self.assertEqual(response.status, 202)
                    server.task.result(timeout=5)
                with browser.open(server.origin + "/api/lab", timeout=5) as response:
                    state = json.load(response)
                self.assertEqual(state["high_concurrency_benchmark"], high)
                self.assertEqual(state["sixteen_lane_benchmark"], highest)
                self.assertEqual(state["featured_mixed"], "mixed-sixteen")

    def test_concurrency_route_preserves_the_three_mode_report(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            browser = build_opener(ProxyHandler({}))
            previous = {"live": True, "scenarios": {"single": {}, "same-game": {}, "mixed": {}}}
            history = data / "验收" / "吞吐对照-实跑-三组" / "summary.json"
            history.parent.mkdir(parents=True)
            history.write_text(json.dumps(previous), encoding="utf-8")
            data_pair = {"live": True, "scenarios": {"mixed": {}, "mixed-four": {}},
                         "winner": "mixed-four", "incomplete": False}
            packs = ("square-26", "relay-49-frontier")

            def archive(workspace, output, scenarios, **_):
                self.assertEqual(scenarios, {"mixed": packs, "mixed-four": packs * 2})
                output.mkdir(parents=True)
                (output / "summary.json").write_text(json.dumps(data_pair), encoding="utf-8")
                return data_pair

            with lab.launch(data, register=False) as server, \
                    patch.object(lab, "peek_setup_status", return_value={"ready": True}), \
                    patch.object(lab, "live_suite", side_effect=archive):
                request = Request(server.origin + "/api/lab/concurrency", data=b"{}", headers={
                    "Content-Type": "application/json", "Origin": server.origin,
                })
                with browser.open(request, timeout=5) as response:
                    self.assertEqual(response.status, 202)
                server.task.result(timeout=5)
                with browser.open(server.origin + "/api/lab", timeout=5) as response:
                    state = json.load(response)
                self.assertEqual(state["benchmark"], previous)
                self.assertEqual(state["concurrency_benchmark"], data_pair)
                self.assertEqual(state["featured_mixed"], "mixed-four")

                four = {"live": True, "scenarios": {"mixed-four": {"metered_rounds": 4, "packs": list(packs * 2)}}}

                def archive_four(workspace, output, scenarios, **_):
                    self.assertEqual(scenarios, {"mixed-four": packs * 2})
                    output.mkdir(parents=True)
                    (output / "summary.json").write_text(json.dumps(four), encoding="utf-8")
                    return four

                with patch.object(lab, "live_suite", side_effect=archive_four):
                    request = Request(server.origin + "/api/lab/mixed-four", data=b"{}", headers={
                        "Content-Type": "application/json", "Origin": server.origin,
                    })
                    with browser.open(request, timeout=5) as response:
                        self.assertEqual(response.status, 202)
                    server.task.result(timeout=5)
                with browser.open(server.origin + "/api/lab", timeout=5) as response:
                    state = json.load(response)
                self.assertEqual(state["mixed_run"], four["scenarios"]["mixed-four"])
                self.assertEqual(state["concurrency_benchmark"], data_pair)

    def test_mixed_run_uses_two_packs_and_keeps_comparison_history(self):
        with TemporaryDirectory() as directory:
            data = Path(directory)
            browser = build_opener(ProxyHandler({}))
            history = {"live": True, "scenarios": {name: {} for name in ("single", "same-game", "mixed")}}
            comparison = data / "验收" / "吞吐对照-实跑-原始对照" / "summary.json"
            comparison.parent.mkdir(parents=True)
            comparison.write_text(json.dumps(history), encoding="utf-8")
            mixed = {"live": True, "scenarios": {"mixed": {"metered_rounds": 2, "project_records": 1}}}

            def archive_mixed(workspace, output, scenarios, **_):
                self.assertEqual(workspace, data.resolve())
                self.assertEqual(scenarios, {"mixed": ("square-26", "relay-49-frontier")})
                output.mkdir(parents=True)
                (output / "summary.json").write_text(json.dumps(mixed), encoding="utf-8")
                return mixed

            with lab.launch(data, register=False) as server:
                def post():
                    request = Request(server.origin + "/api/lab/mixed", data=b"{}", headers={
                        "Content-Type": "application/json", "Origin": server.origin,
                    })
                    return browser.open(request, timeout=5)

                with patch.object(lab, "peek_setup_status", return_value={"ready": False}):
                    with self.assertRaises(HTTPError) as caught:
                        post()
                    self.assertEqual(caught.exception.code, 400)

                with patch.object(lab, "peek_setup_status", return_value={"ready": True}), \
                        patch.object(lab, "live_suite", side_effect=archive_mixed):
                    with post() as response:
                        self.assertEqual(response.status, 202)
                    server.task.result(timeout=5)
                    with browser.open(server.origin + "/api/lab", timeout=5) as response:
                        state = json.load(response)
                    self.assertEqual(state["benchmark"], history)
                    self.assertEqual(state["mixed_run"], mixed["scenarios"]["mixed"])
                    self.assertEqual(state["last_run_records"], 1)

    def test_launch_shows_both_live_games_with_isolated_databases(self):
        with TemporaryDirectory() as directory:
            browser = build_opener(ProxyHandler({}))
            data = Path(directory)
            with lab.launch(data, port=0, register=False) as server:
                with browser.open(server.origin + "/api/lab", timeout=5) as response:
                    state = json.load(response)
                square = state["games"]["packing"]
                relay = state["games"]["relay"]
                self.assertEqual(square["score"], "5.668")
                self.assertEqual(relay["score"], "1160 / 1176")
                self.assertEqual((square["records"], relay["records"]), (0, 0))
                self.assertNotEqual(square["url"], relay["url"])
                for url in (server.origin, square["url"], relay["url"]):
                    with browser.open(url, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                self.assertIsNone(state["benchmark"])
                self.assertEqual(state["featured_mixed"], "mixed")

                def post(operation):
                    request = Request(server.origin + "/api/lab/" + operation, data=b"{}", headers={
                        "Content-Type": "application/json", "Origin": server.origin,
                    })
                    return browser.open(request, timeout=5)

                with patch.object(lab, "peek_setup_status", return_value={"ready": False}):
                    with self.assertRaises(HTTPError) as caught:
                        post("live")
                    self.assertEqual(caught.exception.code, 400)
                    with post("replay") as response:
                        self.assertEqual(response.status, 202)
                    deadline = monotonic() + 5
                    while monotonic() < deadline:
                        with browser.open(server.origin + "/api/lab", timeout=5) as response:
                            state = json.load(response)
                        if state["benchmark"] and not state["benchmark"]["incomplete"] and state["run"] == "idle":
                            break
                        sleep(0.05)
                    scenarios = state["benchmark"]["scenarios"]
                    self.assertEqual([scenarios[name]["rounds"] for name in ("single", "same-game", "mixed")],
                                     [3, 6, 6])
                    self.assertTrue(all(item["model_rounds"] == 0 for item in scenarios.values()))
                    self.assertIsNone(state["benchmark"]["winner"])

                incomplete = data / "验收" / "吞吐对照-实跑-恢复验收"
                incomplete.mkdir()
                (incomplete / "suite.json").write_text(
                    '{"plan": {"live": true, "scenarios": ["single"]}}', encoding="utf-8")
                with patch.object(lab, "peek_setup_status", return_value={"ready": True}), \
                        patch.object(lab, "live_suite", return_value={"scenarios": {}}) as resume:
                    with browser.open(server.origin + "/api/lab", timeout=5) as response:
                        self.assertTrue(json.load(response)["resumable"])
                    with post("resume") as response:
                        self.assertEqual(response.status, 202)
                    server.task.result(timeout=5)
                    self.assertEqual(resume.call_args.args[1].resolve(), incomplete.resolve())
                    self.assertTrue(resume.call_args.kwargs["resume"])
            self.assertTrue((data / "方格子容纳" / "packing.sqlite3").is_file())
            self.assertTrue((data / "四境驿站" / "relay.sqlite3").is_file())


if __name__ == "__main__":
    unittest.main()
