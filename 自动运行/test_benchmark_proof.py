from copy import deepcopy
from hashlib import sha256
import json
from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory
import unittest
from zipfile import ZipFile

from 本地裁判 import LocalJudge, get_pack
import benchmark
import benchmark_proof
from evidence import _station_candidate
import release
import scoreboard
from workspace_io import save


class BenchmarkProofTests(unittest.TestCase):
    def _write_series_window(self, folder, judge, baseline, *, started_at, response_id, scenario="sample"):
        pack = judge.pack
        profile = {"model": "test-model", "effort": "high", "account": "private-binding"}
        folder.mkdir(parents=True)
        save(folder / "window.json", {"started_at": started_at, "seconds": 600,
                                       "packs": [pack], "live": True})
        save(folder.parent / "suite.json", {
            "plan": {"live": True, "seconds": 600, "scenarios": [scenario],
                     "packs": {scenario: [pack]}, "profile": profile},
            "baselines": {pack: baseline},
        })
        lane = folder / "lane-1"
        lane.mkdir()
        row = {
            "pack": pack, "initial_hash": baseline["tip"], "in_window": True, "turn": 1,
            "request": {"lane": "lane-1", "candidate": {"solution": baseline["chain"][-1]["solution"]}},
            "thread_id": "private-thread", "response_id": response_id,
            "provider_started": 1.0, "received": 2.0, "model": profile["model"],
            "reasoning_effort": profile["effort"],
            "usage": {"input_tokens": 20, "cached_input_tokens": 4, "output_tokens": 4},
            "key": None, "candidate": None, "verification": {"valid": False},
        }
        save(lane / "0001.json", row)
        summary = {**benchmark.metrics([row], 600, live=True), "packs": [pack], "live": True,
                   "comparable": True, "stop": "window", "errors": [], "window_seconds": 600,
                   "wall_seconds": 601.0, "models": [profile["model"]],
                   "reasoning_efforts": [profile["effort"]], "initial_hashes": {pack: baseline["tip"]},
                   "model_overlap_seconds": 0.0}
        save(folder / "summary.json", summary)

    def test_series_recounts_two_windows_and_includes_gap(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            judge = LocalJudge(root / "seed", "cap-set")
            baseline = judge.store.export(judge.metric()["hash"])
            first = root / "first" / "sample"
            second = root / "second" / "sample"
            self._write_series_window(first, judge, baseline, started_at=1000.0,
                                      response_id="first-response")
            self._write_series_window(second, judge, baseline, started_at=1602.0,
                                      response_id="second-response")

            result = benchmark_proof.audit_series([second, first])

            self.assertEqual(result["window_count"], 2)
            self.assertEqual(result["effective_tokens"], 40)
            self.assertEqual(result["metered_rounds"], 2)
            self.assertEqual([row["started_at"] for row in result["windows"]], [1000.0, 1602.0])
            self.assertEqual(result["elapsed_seconds"], 1203.0)
            self.assertEqual(result["peak_tokens_per_minute"], 2.0)

    def test_series_rejects_duplicate_response_ids_across_windows(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            judge = LocalJudge(root / "seed", "relay-49-frontier")
            baseline = judge.store.export(judge.metric()["hash"])
            first = root / "first" / "sample"
            second = root / "second" / "sample"
            self._write_series_window(first, judge, baseline, started_at=1000.0,
                                      response_id="same-response")
            self._write_series_window(second, judge, baseline, started_at=1602.0,
                                      response_id="same-response")

            with self.assertRaisesRegex(ValueError, "回包指纹缺失或重复"):
                benchmark_proof.audit_series([first, second])

    def test_series_rejects_duplicate_response_ids_within_one_window(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            judge = LocalJudge(root / "seed", "cap-set")
            baseline = judge.store.export(judge.metric()["hash"])
            first = root / "first" / "sample"
            second = root / "second" / "sample"
            self._write_series_window(first, judge, baseline, started_at=1000.0,
                                      response_id="same-response")
            self._write_series_window(second, judge, baseline, started_at=1602.0,
                                      response_id="later-response")
            first_lane = first / "lane-1"
            first_row = json.loads((first_lane / "0001.json").read_text(encoding="utf-8"))
            second_row = {**first_row, "turn": 2, "provider_started": 3.0, "received": 4.0}
            save(first_lane / "0002.json", second_row)
            first_summary = {**benchmark.metrics([first_row, second_row], 600, live=True),
                             **{key: first_row_value for key, first_row_value in
                                json.loads((first / "summary.json").read_text(encoding="utf-8")).items()
                                if key not in benchmark_proof.MEASUREMENTS},
                             "model_overlap_seconds": 0.0}
            save(first / "summary.json", first_summary)

            with self.assertRaisesRegex(ValueError, "回包指纹缺失或重复"):
                benchmark_proof.audit_series([first, second])

    def test_series_accepts_windows_from_two_game_families(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            cap_judge = LocalJudge(root / "cap-seed", "cap-set")
            lorenz_judge = LocalJudge(root / "lorenz-seed", "lorenz-divergence")
            first = root / "first" / "sample"
            second = root / "second" / "sample"
            self._write_series_window(first, cap_judge,
                                      cap_judge.store.export(cap_judge.metric()["hash"]),
                                      started_at=1000.0, response_id="cap-response")
            self._write_series_window(second, lorenz_judge,
                                      lorenz_judge.store.export(lorenz_judge.metric()["hash"]),
                                      started_at=1602.0, response_id="lorenz-response")

            result = benchmark_proof.audit_series([first, second])

            self.assertEqual(result["window_count"], 2)
            self.assertEqual([row["packs"][0] for row in result["windows"]],
                             ["cap-set", "lorenz-divergence"])
            self.assertEqual(result["effective_tokens"], 40)

    def test_series_rejects_overlapping_windows(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            judge = LocalJudge(root / "seed", "relay-49-frontier")
            baseline = judge.store.export(judge.metric()["hash"])
            first = root / "first" / "sample"
            second = root / "second" / "sample"
            self._write_series_window(first, judge, baseline, started_at=1000.0,
                                      response_id="first-response")
            self._write_series_window(second, judge, baseline, started_at=1599.0,
                                      response_id="second-response")

            with self.assertRaisesRegex(ValueError, "窗口之间存在时间重叠"):
                benchmark_proof.audit_series([first, second])

    def test_single_research_window_exports_a_redacted_native_proof(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            judge = LocalJudge(root / "seed", "cap-set")
            baseline = judge.store.export(judge.metric()["hash"])
            run = root / "run"
            window = run / "cap-set"
            self._write_series_window(window, judge, baseline, started_at=1000.0,
                                      response_id="private-response-id", scenario="cap-set")
            save(run / "summary.json", {"live": True, "incomplete": False, "scenarios": {
                "cap-set": json.loads((window / "summary.json").read_text(encoding="utf-8"))}})

            proof = benchmark_proof.collect_run(run)
            audited = benchmark_proof.verify(proof)

            self.assertEqual(audited["scenario"], "cap-set")
            self.assertEqual(audited["window"]["metered_rounds"], 1)
            self.assertNotIn("private-response-id", json.dumps(proof))
            self.assertNotIn("private-binding", json.dumps(proof))

    def test_portable_comparison_recounts_receipts_and_replays_the_candidates(self):
        with TemporaryDirectory() as scratch:
            root = Path(scratch)
            judge = LocalJudge(root / "seed", "relay-49-frontier")
            baseline = judge.store.export(judge.metric()["hash"])
            initial = baseline["chain"][-1]["solution"]
            solution, _ = _station_candidate(judge.pack, 1, initial)
            key = sha256(json.dumps(solution, separators=(",", ":")).encode()).hexdigest()
            plan = benchmark.PLANS["relay-twenty-lane-comparison"]
            run = root / "comparison"
            run.mkdir()
            save(run / "suite.json", {
                "plan": {"live": True, "seconds": 600, "scenarios": list(plan),
                         "packs": {name: list(packs) for name, packs in plan.items()},
                         "profile": {"model": "test-model", "effort": "high", "account": "private-binding"}},
                "baselines": {judge.pack: baseline},
            })
            summaries = {}
            for name, packs in plan.items():
                group = run / name
                group.mkdir()
                save(group / "window.json", {"live": True, "seconds": 600, "packs": list(packs)})
                rows = []
                for lane_number, pack in enumerate(packs, start=1):
                    lane = group / f"lane-{lane_number}"
                    lane.mkdir()
                    row = {
                        "pack": pack, "initial_hash": baseline["tip"], "in_window": True, "turn": 1,
                        "request": {"lane": lane.name, "candidate": {"solution": initial}, "prompt": "private prompt"},
                        "thread_id": "private-thread", "response_id": f"private-response-{name}-{lane_number}",
                        "provider_started": lane_number / 100, "received": 2.0,
                        "model": "test-model", "reasoning_effort": "high",
                        "usage": {"input_tokens": 20, "cached_input_tokens": 4, "output_tokens": 4},
                        "key": key, "candidate": {"solution": solution, "handoff": "private note"},
                        "verification": {"valid": True, "score": get_pack(pack).evaluate(solution)["score"]},
                    }
                    save(lane / "0001.json", row)
                    rows.append(row)
                summary = {**benchmark.metrics(rows, 600, live=True), "packs": list(packs),
                           "live": True, "comparable": True, "stop": "window", "errors": [],
                           "window_seconds": 600, "wall_seconds": 601,
                           "models": ["test-model"], "reasoning_efforts": ["high"],
                           "initial_hashes": {judge.pack: baseline["tip"]},
                           "model_overlap_seconds": benchmark._overlap_seconds(rows, minimum=len(packs))}
                save(group / "summary.json", summary)
                summaries[name] = summary
            save(run / "summary.json", {"live": True, "incomplete": False, "winner": "relay-twenty",
                                       "scenarios": summaries})

            proof = benchmark_proof.collect_run(run)
            goal = benchmark_proof.audit_goal_window(run / "relay-twenty")
            self.assertEqual(goal["effective_tokens_per_minute_bucket"], [400] + [0] * 9)
            self.assertEqual(goal["average_tokens_per_minute"], 40)
            self.assertFalse(goal["target_met"])
            billion = benchmark_proof.audit_goal_window(run / "relay-twenty", 100_000_000)
            self.assertEqual(billion["target_tokens"], 1_000_000_000)
            self.assertEqual(billion["effective_tokens"], goal["effective_tokens"])
            self.assertFalse(billion["target_met"])
            encoded = json.dumps(proof)
            for private in ("private prompt", "private-thread", "private note", "private-binding", "private-response"):
                self.assertNotIn(private, encoded)
            audited = benchmark_proof.verify(proof)
            self.assertEqual(audited["winner"], "relay-twenty")
            results = audited["scenarios"]
            self.assertEqual(results["relay-twenty"]["tokens_per_minute"], 40.0)
            self.assertEqual(results["relay-sixteen"]["tokens_per_minute"], 32.0)
            self.assertEqual(results["relay-twenty"]["unique_candidates"], 1)

            changed = deepcopy(proof)
            changed["scenarios"]["relay-twenty"]["receipts"][0]["usage"]["cached_input_tokens"] = 21
            with self.assertRaises(ValueError):
                benchmark_proof.verify(changed)
            changed = deepcopy(proof)
            changed["scenarios"]["relay-twenty"]["candidates"][judge.pack][key]["solution"]["blocks"].pop()
            with self.assertRaises(ValueError):
                benchmark_proof.verify(changed)

            # Build the legacy evidence mode from a minimal, clean fixture. A
            # source checkout intentionally has no public leaderboard files.
            checkout = root / "checkout"
            for directory, filenames in release.SOURCE.items():
                if directory == "public":
                    continue
                for filename in filenames:
                    source = release.ROOT / directory / filename
                    target = checkout / directory / filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    copy2(source, target)
            public = checkout / "public"
            public.mkdir()
            (public / "verifier.zip").write_bytes(b"fixture verifier")
            scoreboard.export(checkout, public)
            archive = root / "audit.zip"
            release.build_archive(checkout, archive, evidence_run=run)
            with ZipFile(archive) as package:
                audited = benchmark_proof.verify(json.loads(package.read("复验/吞吐回执.json")))
                scores = scoreboard.verify(json.loads(package.read("复验/leaderboard.json")))
                self.assertEqual(audited["winner"], "relay-twenty")
                self.assertEqual(scores, scoreboard.collect(checkout))
                self.assertTrue({"benchmark_proof.py", "复验/index.html"} <= set(package.namelist()))


if __name__ == "__main__":
    unittest.main()
