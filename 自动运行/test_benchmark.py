from base64 import b64decode
from concurrent.futures import ThreadPoolExecutor
import gzip
from hashlib import sha256
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Barrier, Event
from time import monotonic, monotonic_ns, sleep
import unittest
from unittest.mock import patch

from 本地裁判 import LocalJudge, get_pack
import benchmark
import guided_search


class BenchmarkTests(unittest.TestCase):
    def test_research_improvement_is_shared_by_web_scoreboard_and_next_window(self):
        import research_games
        import scoreboard
        from research_games.discrete import golomb_ruler_improvement_example

        with TemporaryDirectory() as directory:
            root = Path(directory)
            pack = "golomb-ruler"
            runner = LocalJudge(root / "lane", pack)
            winner = runner.store.submit(pack, runner.metric()["hash"], "test", golomb_ruler_improvement_example(), "")
            proof = runner.store.export(winner["hash"])
            self.assertTrue(benchmark.publish_record(root, pack, proof))
            self.assertFalse(benchmark.publish_record(root, pack, proof))
            web_store = research_games.build_store(root / research_games.DATABASE)
            self.assertEqual(web_store.best(pack)["hash"], winner["hash"])
            self.assertEqual(benchmark.snapshot_baselines(root, [pack])[pack]["tip"], winner["hash"])
            entries = scoreboard.verify(scoreboard.collect(root))["entries"]
            self.assertEqual([entry["pack_id"] for entry in entries], [pack])

    def test_research_packs_are_registered_as_independent_single_lane_scenarios(self):
        import research_games

        self.assertEqual({pack: benchmark.WINDOWS.get(pack) for pack in research_games.PACKS},
                         {pack: (pack,) for pack in research_games.PACKS})

    def test_offline_research_window_runs_candidate_through_native_judge(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            pack = "cap-set"
            baselines = benchmark.snapshot_baselines(root, (pack,))

            def infer(request, seconds, abort):
                candidate = __import__("evidence").rehearsal_candidate(pack, 1, request["prompt"])
                return {"text": json.dumps(candidate), "model": None, "usage": None}

            result = benchmark.run_window(root / "offline" / pack, (pack,), baselines, infer,
                                          seconds=10, live=False, max_turns=1)
            record = benchmark.read(root / "offline" / pack / "lane-1" / "0001.json")

            self.assertEqual(result["unique_candidates"], 1)
            self.assertTrue(record["verification"]["valid"])
            self.assertEqual(record["verification"]["score"], 18)

    def test_research_guided_schema_and_resolver_use_direct_candidate_contract(self):
        with TemporaryDirectory() as directory:
            judge = LocalJudge(Path(directory), "golomb-ruler")
            candidate = __import__("evidence").rehearsal_candidate("golomb-ruler", 1, judge.task())

            schema = guided_search.schema(judge.pack)
            result = guided_search.resolve(judge, {"pack": judge.pack}, json.dumps(candidate), lambda: False)

            self.assertEqual(schema["required"], ["pack_id", "parent_hash", "solution", "handoff"])
            self.assertEqual(result["candidate"], candidate)

    def test_frontier_receipt_preserves_the_dispatched_prompt_and_grades_the_move(self):
        with TemporaryDirectory() as directory, ThreadPoolExecutor(max_workers=1) as workers:
            lane = Path(directory) / "lane-1"
            judge = LocalJudge(lane, "relay-49-frontier")
            seed = judge.metric()["hash"]
            sent = []

            def infer(request, seconds, stopping, *, on_sent):
                on_sent()
                sent.append(request["prompt"])
                payload = json.loads(sent[-1])
                branch = payload["branches"][0]
                hint = branch["repairs"][0]
                move = {"pack_id": judge.pack, "parent_hash": seed, "branch_id": branch["branch_id"],
                        **{key: hint[key] for key in ("block", "remove", "add")}, "handoff": "检验这次替换。"}
                return {"guided": True, "model": "fixture", "text": json.dumps(move), "response_id": "test-1",
                        "usage": {"input_tokens": 16, "cached_input_tokens": 4, "output_tokens": 2}}

            records, error = benchmark._run_lane(lane, judge, seed, "", infer, monotonic(), 30, True, 1,
                                                Event(), None, 1, 1, workers)
            self.assertIsNone(error)
            self.assertTrue(records[0]["verification"]["valid"])
            request = benchmark.read(lane / "0001.json")["request"]
            archived = gzip.decompress(b64decode(request["prompt_gzip_base64"]))
            self.assertEqual(archived.decode(), sent[0])
            self.assertEqual(sha256(archived).hexdigest(), request["prompt_sha256"])

    def test_progress_refresh_reads_new_receipts_once_and_keeps_previous_counts(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            group = root / "single"
            lane = group / "lane-1"
            lane.mkdir(parents=True)
            benchmark.save(group / "window.json", {"seconds": 600, "started_at": benchmark.time(), "live": True})
            first, second = lane / "0001.json", lane / "0002.json"
            benchmark.save(first, {"in_window": True, "pack": "square-26",
                                   "usage": {"input_tokens": 7, "cached_input_tokens": 2, "output_tokens": 5}})
            cache = {}
            with patch.object(benchmark, "read", wraps=benchmark.read) as read:
                initial = benchmark.current_progress(root, receipts_cache=cache)
                benchmark.save(second, {"in_window": True, "pack": "square-26",
                                        "usage": {"input_tokens": 8, "cached_input_tokens": 0, "output_tokens": 3}})
                updated = benchmark.current_progress(root, receipts_cache=cache)
            self.assertEqual((initial["metered_rounds"], updated["metered_rounds"]), (1, 2))
            self.assertEqual((updated["uncached_input_tokens"], updated["output_tokens"]), (13, 8))
            self.assertEqual([call.args[0] for call in read.call_args_list if call.args[0].parent == lane],
                             [first, second])

    def test_dual_relay_decision_checks_two_missing_pairs_and_returns_the_better_native_score(self):
        with TemporaryDirectory() as directory:
            judge = LocalJudge(Path(directory), "relay-49-frontier")
            parent = judge.store.best(judge.pack)
            rules = get_pack(judge.pack)
            missing = rules.evaluate(parent["solution"])["missing"]
            request = {"pack": judge.pack, "turn": 1, "lane": "lane-1", "relay_targets": 2}
            reply = {"pack_id": judge.pack, "parent_hash": parent["hash"], "strategy": "cover_two_pairs",
                     "solution": None, "pair": missing[0] + missing[1], "handoff": "比较两条不同的缺口。"}
            self.assertIn("cover_two_pairs",
                          guided_search.schema(judge.pack, relay_targets=2)["properties"]["strategy"]["enum"])
            result = guided_search.resolve(judge, request, json.dumps(reply), lambda: False)
            self.assertEqual([item["pair"] for item in result["trials"]], missing[:2])
            self.assertEqual(result["checked"], sum(item["checked"] for item in result["trials"]))
            verified = judge.inspect_candidate(result["candidate"])
            self.assertEqual(verified["score"], max(item["score"] for item in result["trials"]))
            reply["pair"] = missing[0] + list(reversed(missing[0]))
            with self.assertRaisesRegex(ValueError, "两个不同"):
                guided_search.resolve(judge, request, json.dumps(reply), lambda: False)

    def test_connection_failure_after_cutoff_preserves_the_completed_window(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            packs = ("square-26",)
            baselines = benchmark.snapshot_baselines(root, packs)
            abort = Event()

            def infer(request, seconds, stopping, *, on_sent):
                on_sent()
                if request["turn"] == 1:
                    return {"text": json.dumps(request["candidate"]), "model": "test",
                            "reasoning_effort": "high", "response_id": "finished-before-cutoff",
                            "usage": {"input_tokens": 11, "cached_input_tokens": 0, "output_tokens": 6}}
                sleep(seconds + 0.02)
                raise benchmark.LaneConnectionError("连接关闭")

            output = root / "run"
            result = benchmark.run_window(output, packs, baselines, infer, seconds=1, live=True, abort=abort)
            self.assertEqual(result["stop"], "window")
            self.assertFalse(abort.is_set())
            self.assertEqual(result["metered_rounds"], 1)
            self.assertTrue(result["comparable"])
            self.assertEqual(result["errors"], [])
            deadline = json.loads((output / "lane-1" / "deadline.json").read_text(encoding="utf-8"))
            self.assertGreaterEqual(deadline["elapsed"], 1)
            self.assertTrue((output / "lane-1" / "pending-0002.json").is_file())

    def test_dropped_lane_preserves_other_receipts_and_excludes_the_window_from_ranking(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            packs = ("square-26", "square-26")
            baselines = benchmark.snapshot_baselines(root, packs)
            abort = Event()

            def infer(request, seconds, stopping, *, on_sent):
                on_sent()
                if request["lane"] == "lane-1":
                    raise benchmark.LaneInterrupted("响应流中断")
                self.assertFalse(stopping.is_set())
                if request["turn"] == 1:
                    return {"text": json.dumps(request["candidate"]), "model": "test",
                            "reasoning_effort": "high", "response_id": "completed-metered-round",
                            "usage": {"input_tokens": 11, "cached_input_tokens": 0, "output_tokens": 6}}
                sleep(seconds + 0.01)
                raise TimeoutError("本组计时结束")

            output = root / "run"
            result = benchmark.run_window(output, packs, baselines, infer, seconds=1, live=True, abort=abort)
            self.assertFalse(abort.is_set())
            self.assertEqual(result["stop"], "window")
            self.assertEqual(result["metered_rounds"], 1)
            self.assertEqual(result["errors"], ["响应流中断"])
            self.assertFalse(result["comparable"])
            self.assertFalse(result["rankable"])
            pending = json.loads((output / "lane-1" / "pending-0001.json").read_text(encoding="utf-8"))
            self.assertEqual(pending["state"], "possibly-sent")

    def test_live_submission_journals_uncertain_request(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seed = LocalJudge(root / "seed", "square-26")
            baseline = seed.store.export(seed.metric()["hash"])

            def failed(request, seconds, abort, *, on_sent):
                on_sent()
                raise RuntimeError("连接中断")

            result = benchmark.run_window(root / "run", ["square-26"], {"square-26": baseline}, failed,
                                          seconds=4, live=True)
            self.assertEqual(result["stop"], "error")
            self.assertFalse(result["rankable"])
            path = root / "run" / "lane-1" / "pending-0001.json"
            self.assertEqual(json.loads(path.read_text(encoding="utf-8"))["state"], "possibly-sent")

    def test_resume_keeps_completed_window_and_preserves_first_attempt(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            work = root / "experiment"
            scenarios = {"single": ("square-26",), "same-game": ("square-26", "square-26")}
            stop = Event()

            def interrupted(request, seconds, abort):
                if request["lane"] == "lane-2":
                    stop.set()
                return {"text": json.dumps(request["candidate"]), "usage": None}

            result = benchmark._run_suite(root, work, scenarios, interrupted, seconds=4,
                                          live=False, max_turns=1, abort=stop)
            self.assertEqual(result["scenarios"]["single"]["stop"], "round-limit")
            self.assertEqual(result["scenarios"]["same-game"]["stop"], "stopped")
            completed = (work / "single" / "summary.json").read_bytes()
            baseline = json.loads((work / "suite.json").read_text(encoding="utf-8"))["baselines"]
            old_attempt = (work / "same-game" / "window.json").read_bytes()
            (work / "same-game" / "summary.json").unlink()
            (work / "summary.json").unlink()

            def continue_run(request, seconds, abort):
                self.assertEqual(request["candidate"]["parent_hash"], baseline["square-26"]["tip"])
                return {"text": json.dumps(request["candidate"]), "usage": None}

            resumed = benchmark._run_suite(root, work, scenarios, continue_run, seconds=4,
                                           live=False, max_turns=1, abort=Event(), resume=True)
            self.assertEqual((work / "single" / "summary.json").read_bytes(), completed)
            self.assertEqual((work / "attempts" / "same-game-001" / "window.json").read_bytes(), old_attempt)
            self.assertEqual(resumed["scenarios"]["same-game"]["rounds"], 2)
            self.assertFalse(resumed["incomplete"])
            with self.assertRaises(ValueError):
                benchmark._run_suite(root, work, scenarios, continue_run, seconds=5,
                                     live=False, max_turns=1, resume=True)

    def test_resume_replaces_a_timed_window_with_a_dropped_lane_and_reuses_the_comparable_window(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            results = root / "验收"
            work = results / "吞吐对照-实跑-并发度-测试"
            profile = {"model": "test", "effort": "high", "account": "local"}
            dropped = Event()
            control_lanes = Barrier(4)
            resumed_lanes = Barrier(2)
            resuming = False

            def infer(request, seconds, abort, *, on_sent):
                on_sent()
                if request["lane"] == "lane-2" and not dropped.is_set():
                    dropped.set()
                    raise benchmark.LaneInterrupted("模拟缺少完成回执")
                if request["turn"] > 1:
                    sleep(seconds + 0.01)
                    raise TimeoutError("本组计时结束")
                started = monotonic()
                # Establish actual overlapping requests even on a slow hosted runner.
                if resuming:
                    resumed_lanes.wait(timeout=seconds)
                elif (work / "mixed-four" / "window.json").is_file():
                    control_lanes.wait(timeout=seconds)
                sleep(0.02)
                return {"text": json.dumps(request["candidate"]), "model": "test",
                        "reasoning_effort": "high", "response_id": str(monotonic_ns()),
                        "provider_started": started,
                        "usage": {"input_tokens": 11, "cached_input_tokens": 0, "output_tokens": 6}}

            scenarios = benchmark.PLANS["concurrency"]
            first = benchmark._run_suite(root, work, scenarios, infer, seconds=3, live=True,
                                         abort=Event(), profile=profile)
            self.assertEqual(first["scenarios"]["mixed"]["stop"], "window")
            self.assertFalse(first["scenarios"]["mixed"]["comparable"])
            self.assertTrue(first["scenarios"]["mixed-four"]["comparable"])
            valid_control = (work / "mixed-four" / "summary.json").read_bytes()
            interrupted = (work / "mixed" / "summary.json").read_bytes()
            self.assertTrue(first["incomplete"])
            self.assertEqual(benchmark.resumable_run(results), work)

            resuming = True
            resumed = benchmark._run_suite(root, work, scenarios, infer, seconds=3, live=True,
                                           abort=Event(), resume=True, profile=profile)
            self.assertTrue(resumed["scenarios"]["mixed"]["comparable"])
            self.assertFalse(resumed["incomplete"])
            self.assertEqual((work / "mixed-four" / "summary.json").read_bytes(), valid_control)
            self.assertEqual((work / "attempts" / "mixed-001" / "summary.json").read_bytes(), interrupted)
            self.assertIsNone(benchmark.resumable_run(results))

    def test_overlap_only_counts_intersecting_provider_request_intervals(self):
        intervals = [{"provider_started": 1.0, "received": 4.0},
                     {"provider_started": 2.5, "received": 6.0},
                     {"provider_started": None, "received": 3.0},
                     {"provider_started": 6.0, "received": 7.0}]
        self.assertEqual(benchmark._overlap_seconds(intervals), 1.5)

    def test_four_lane_overlap_requires_all_four_requests(self):
        intervals = [{"provider_started": start, "received": end} for start, end in
                     ((0, 10), (1, 6), (3, 9), (5, 8))]
        self.assertEqual(benchmark._overlap_seconds(intervals, minimum=4), 1)
        self.assertEqual(benchmark._overlap_seconds(intervals[:3], minimum=4), 0)

    def test_four_mixed_lanes_share_the_verified_start_of_each_game(self):
        packs = ("square-26", "relay-49-frontier") * 2
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baselines = benchmark.snapshot_baselines(root, packs)
            barrier = Barrier(4, timeout=4)

            def infer(request, seconds, abort):
                self.assertEqual(request["candidate"]["parent_hash"], baselines[request["pack"]]["tip"])
                barrier.wait()
                return {"text": json.dumps(request["candidate"]), "model": "test", "usage": None}

            result = benchmark.run_window(root / "run", packs, baselines, infer, seconds=5,
                                          live=False, max_turns=1)
            self.assertEqual(result["rounds"], 4)
            self.assertEqual(result["stop"], "round-limit")
            self.assertEqual(result["packs"], list(packs))

    def test_eight_and_sixteen_lanes_use_the_same_two_verified_checkpoints(self):
        for width in (8, 16):
            with self.subTest(width=width), TemporaryDirectory() as directory:
                packs = ("square-26", "relay-49-frontier") * (width // 2)
                root = Path(directory)
                baselines = benchmark.snapshot_baselines(root, packs)
                barrier = Barrier(width, timeout=4)

                def infer(request, seconds, abort):
                    self.assertEqual(request["candidate"]["parent_hash"], baselines[request["pack"]]["tip"])
                    barrier.wait()
                    return {"text": json.dumps(request["candidate"]), "model": "test", "usage": None}

                report = benchmark.run_window(root / "run", packs, baselines, infer, seconds=5,
                                              live=False, max_turns=1)
                self.assertEqual(report["rounds"], width)
                self.assertEqual(report["packs"], list(packs))
                self.assertEqual(report["stop"], "round-limit")

    def test_ranks_the_concurrency_trial_only_after_both_comparable_windows(self):
        shared = {"window_seconds": 600, "models": ["model"], "reasoning_efforts": ["high"],
                  "stop": "window", "live": True, "metered_rounds": 4, "rounds": 4,
                  "initial_hashes": {"square-26": "a", "relay-49-frontier": "b"},
                  "comparable": True, "rankable": True}
        results = {"mixed": {**shared, "tokens_per_minute": 16000},
                   "mixed-four": {**shared, "tokens_per_minute": 24000}}
        self.assertEqual(benchmark.select_winner(results), "mixed-four")
        self.assertIsNone(benchmark.select_winner({"mixed": results["mixed"]}))
        results["mixed-four"]["comparable"] = False
        self.assertIsNone(benchmark.select_winner(results))

        high = {"mixed-four": {**shared, "tokens_per_minute": 24000},
                "mixed-eight": {**shared, "tokens_per_minute": 36000}}
        self.assertEqual(benchmark.select_winner(high), "mixed-eight")

        sixteen = {"mixed-eight": {**shared, "tokens_per_minute": 80000},
                   "mixed-sixteen": {**shared, "tokens_per_minute": 110000}}
        self.assertEqual(benchmark.select_winner(sixteen), "mixed-sixteen")

        portfolio = {"mixed-sixteen": sixteen["mixed-sixteen"],
                     "mixed-portfolio": {**shared, "tokens_per_minute": 115000,
                                         "initial_hashes": {**shared["initial_hashes"], "square-41": "c"}}}
        self.assertEqual(benchmark.select_winner(portfolio), "mixed-portfolio")
        portfolio["mixed-portfolio"]["initial_hashes"]["square-26"] = "other"
        self.assertIsNone(benchmark.select_winner(portfolio))

    def test_twenty_four_portfolio_lanes_share_the_control_checkpoints_and_all_finish(self):
        relay = "relay-49-frontier"
        expected_packs = (("square-26", relay) * 3 + ("square-37", relay) * 3
                          + ("square-41", relay) * 3 + ("square-50", relay) * 3)
        comparison = benchmark.PLANS.get("portfolio-scale-comparison", {})
        self.assertEqual(comparison.get("mixed-portfolio"), benchmark.WINDOWS["mixed-portfolio"])
        self.assertEqual(comparison.get("mixed-portfolio-twenty-four"), expected_packs)
        with TemporaryDirectory() as directory:
            root = Path(directory)
            baselines = benchmark.snapshot_baselines(root, expected_packs)
            barrier = Barrier(24, timeout=12)

            def infer(request, seconds, abort):
                self.assertEqual(request["candidate"]["parent_hash"], baselines[request["pack"]]["tip"])
                direction = request["prompt"].split("\n本轮方向：", 1)[1].split("。", 1)[0]
                self.assertIn("方格" if request["pack"].startswith("square-") else "站", direction)
                barrier.wait()
                return {"text": json.dumps(request["candidate"]), "usage": None}

            report = benchmark.run_window(root / "run", expected_packs, baselines, infer, seconds=15,
                                          live=False, max_turns=1)
            self.assertEqual(report["rounds"], 24)
            self.assertEqual(report["packs"], list(expected_packs))
            self.assertEqual(report["stop"], "round-limit")

    def test_guided_search_needs_a_valid_current_parent_and_runs_native_judge(self):
        with TemporaryDirectory() as directory:
            judge = LocalJudge(Path(directory), "square-26")
            parent = judge.store.best("square-26")
            request = {"pack": "square-26", "turn": 1, "lane": "lane-1"}
            reply = {"pack_id": "square-26", "parent_hash": parent["hash"], "handoff": "检验整体间距",
                     "strategy": "compact", "pair": None, "solution": None}
            attempt = guided_search.resolve(judge, request, json.dumps(reply), lambda: False)
            self.assertGreater(attempt["checked"], 0)
            self.assertEqual(attempt["strategy"], "compact")
            self.assertEqual(attempt["candidate"]["parent_hash"], parent["hash"])
            self.assertIsNotNone(judge.inspect_candidate(attempt["candidate"])["key"])
            reply["parent_hash"] = "stale"
            with self.assertRaisesRegex(ValueError, "父档"):
                guided_search.resolve(judge, request, json.dumps(reply), lambda: False)

    def test_selects_the_highest_measured_rate_from_comparable_results(self):
        common = {"window_seconds": 600, "models": ["model"], "reasoning_efforts": ["high"],
                  "stop": "window", "live": True, "metered_rounds": 4, "rounds": 4,
                  "initial_hashes": {"square-26": "seed"}, "comparable": True}
        results = {
            "single": {**common, "rankable": True, "tokens_per_minute": 600},
            "same-game": {**common, "rankable": True, "tokens_per_minute": 800},
            "mixed": {**common, "rankable": False, "tokens_per_minute": 1000},
        }
        self.assertEqual(benchmark.select_winner(results), "same-game")
        results["mixed"]["models"] = ["different-model"]
        self.assertIsNone(benchmark.select_winner(results))

    def test_freezes_live_checkpoint_and_leaves_source_database_intact(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            original = LocalJudge(root / "方格子容纳", "square-26")
            initial = original.metric()
            bundle = benchmark.snapshot_baselines(root, ["square-26"])
            self.assertEqual(bundle["square-26"]["tip"], initial["hash"])
            self.assertEqual(original.metric(), initial)

    def test_rank_uses_wall_time_and_excludes_unmetered_runs(self):
        window = 600
        records = [
            {"pack": "square-26", "key": "same", "usage": {
                "input_tokens": 9000, "cached_input_tokens": 6000, "output_tokens": 700,
            }},
            {"pack": "square-26", "key": "same", "usage": {
                "input_tokens": 4000, "cached_input_tokens": 3000, "output_tokens": 300,
            }},
            {"pack": "relay-49-frontier", "key": "same", "usage": {
                "input_tokens": 2000, "cached_input_tokens": 1000, "output_tokens": 500,
            }},
        ]
        result = benchmark.metrics(records, window, live=True)
        self.assertEqual(result["tokens_per_minute"], 650)
        self.assertEqual(result["unique_candidates"], 2)
        self.assertEqual(result["unique_candidates_by_pack"], {"square-26": 1, "relay-49-frontier": 1})
        self.assertEqual(result["cached_input_tokens"], 10000)
        self.assertEqual(result["total_tokens"], 16500)
        self.assertTrue(result["rankable"])
        self.assertFalse(benchmark.metrics(records + [{"usage": None}], window, live=True)["rankable"])
        self.assertFalse(benchmark.metrics(records, window, live=False)["rankable"])

    def test_same_case_starts_identically_and_verifies_on_two_concurrent_lanes(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            seed = LocalJudge(root / "seed", "square-26")
            baseline = seed.store.export(seed.metric()["hash"])
            barrier = Barrier(2, timeout=4)

            def infer(request, seconds, abort):
                barrier.wait()
                candidate = {**request["candidate"], "handoff": "裁判验收样例"}
                # Spread the known legal seed outward to produce a distinct legal layout.
                candidate["solution"] = {"poses": [[x * 2, y * 2, p, q]
                                                    for x, y, p, q in candidate["solution"]["poses"]]}
                return {"text": json.dumps(candidate), "model": "test", "usage": None}

            result = benchmark.run_window(
                root / "run", ["square-26", "square-26"], {"square-26": baseline},
                infer, seconds=5, live=False, max_turns=1,
            )
            self.assertEqual(result["unique_candidates"], 1)
            self.assertEqual(result["rounds"], 2)
            self.assertFalse(result["rankable"])
            self.assertEqual(result["stop"], "round-limit")
            records = [json.loads(path.read_text(encoding="utf-8"))
                       for path in (root / "run").glob("lane-*/*.json")]
            self.assertEqual(len(records), 2)
            self.assertEqual({r["initial_hash"] for r in records}, {seed.metric()["hash"]})
            self.assertTrue(all(r["verification"]["valid"] for r in records))
            self.assertFalse(any(r["verification"]["receipt"]["new_result"] for r in records))


if __name__ == "__main__":
    unittest.main()
