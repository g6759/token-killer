"""Regression tests for the single-conversation, multi-case packing queue."""

import json
import tempfile
import time
import unittest
from pathlib import Path

from game import PackingServer


ALLOWANCE = {
    "ordinaryUsageAllowed": True,
    "primary": {"usedPercent": 3, "windowDurationMins": 10080},
}
THREAD = "00000000-0000-4000-8000-000000000001"


class PackingBatchTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.db = Path(self.temp.name) / "packing.sqlite3"
        self.server = PackingServer(self.db, 0)
        self.addCleanup(lambda: self.server.server_close())

    def test_pending_batch_is_idempotent_and_has_no_earned_floors(self):
        batches = self.server.batches
        args = (["square-18", "square-19"], 3, 2, "start-1")
        first = batches.start(*args)["batch"]
        self.assertEqual(first["id"], batches.start(*args)["batch"]["id"])
        self.assertEqual(first["status"], "waiting")
        self.assertEqual((first["progress_pct"], first["floors"], first["usage"]), (0, [], None))
        with self.assertRaises(ValueError):
            batches.start(["square-18"], 1, 1, "new-request")
        batches.stop(first["id"], "square-18")
        self.assertEqual(batches.status(first["id"])["batch"]["rows"][0]["status"], "stopped")

    def test_quota_refusal_keeps_waiting_rows_unstarted(self):
        batches = self.server.batches
        batch = batches.start(["square-18"], 1, 1, "start-2")["batch"]
        strategy = [{"pack_id": "square-18", "turn": 1, "strategy": "compact"}]
        with self.assertRaises(ValueError):
            batches.plan(batch["id"], THREAD, strategy, {**ALLOWANCE, "ordinaryUsageAllowed": False})
        view = batches.status(batch["id"])["batch"]
        self.assertIn(view["status"], ("paused", "waiting"))
        self.assertEqual(view["rows"][0]["total_checked"], 0)
        self.assertEqual(view["floors"], [])

    def test_exact_result_creates_floor_but_processed_round_alone_does_not(self):
        batches = self.server.batches
        batch = batches.start(["square-18"], 1, 1, "start-3")["batch"]
        store = self.server.store
        base = store.best("square-18")
        self.assertIsNone(batches._accept(base, base["solution"]))
        from game import HERE
        finer = json.loads((HERE / "reference_layouts.json").read_text("utf-8"))["square-18"]["finer"]
        floor = batches._accept(base, finer)
        self.assertGreater(floor["saved_ticks"], 0)
        self.assertEqual(floor["pack_id"], "square-18")
        self.assertEqual(floor["after_side"], "4.822914")
        self.assertIsNone(batches._accept(base, finer))
        self.assertEqual(batches.status(batch["id"])["batch"]["floors"], [])

    def test_restart_recovers_a_saved_score_and_one_floor(self):
        from game import HERE
        from packing_batches import WORK_LIMIT

        batches = self.server.batches
        batch = batches.start(["square-18"], 1, 1, "restart")["batch"]
        batch_id = batch["id"]
        initial = self.server.store.best("square-18")
        layout = json.loads((HERE / "reference_layouts.json").read_text("utf-8"))["square-18"]["finer"]
        finished = time.time()
        with batches.jobs.transaction() as db:
            record = batches._load(db, batch_id)
            record["status"] = "running"
            record["rows"][0].update(
                status="running", seed=123, strategy="mixed", pid=50, worker_started=finished - 2,
                parent_hash=initial["hash"], pending=[WORK_LIMIT, layout, "已完成搜索", finished])
            batches._save(db, record)
        handoff = f"并行批次 {batch_id} 第 1 轮，种子 123。精确裁判已验证布局。"
        batches._accept(initial, layout, handoff)

        self.server.server_close()
        self.server = PackingServer(self.db, 0)
        for _ in range(2):
            view = self.server.batches.status(batch_id)["batch"]
            self.assertEqual(view["status"], "done")
            self.assertEqual(view["improved_count"], 1)
            self.assertEqual(view["floors"][0]["after_side"], "4.822914")
            self.assertEqual(view["total_checked"], WORK_LIMIT)
        self.assertEqual(len(self.server.store.history("square-18")), 2)

    def test_one_conversation_directs_independent_workers(self):
        batches = self.server.batches
        keys = ["square-18", "square-19"]
        batch = batches.start(keys, 1, 2, "start-4")["batch"]
        plans = [{"pack_id": p, "turn": 1, "strategy": "compact"} for p in keys]
        running = batches.plan(batch["id"], THREAD, plans, ALLOWANCE)["batch"]
        self.assertEqual(running["status"], "running")
        deadline = time.monotonic() + 40
        while time.monotonic() < deadline:
            view = batches.status(batch["id"])["batch"]
            if view["status"] in {"done", "failed"}:
                break
            time.sleep(0.06)
        self.assertEqual(view["status"], "done", view)
        self.assertEqual(view["progress_pct"], 100)
        self.assertDistinctProcesses(view["rows"])

    def assertDistinctProcesses(self, rows):
        first, second = (row["receipts"][-1] for row in rows)
        self.assertNotEqual(first["pid"], second["pid"])
        self.assertLess(max(first["started"], second["started"]), min(first["ended"], second["ended"]))
        self.assertGreater(first["checked"], 0)
        self.assertGreater(second["checked"], 0)


if __name__ == "__main__":
    unittest.main()
