from pathlib import Path
from tempfile import TemporaryDirectory
import unittest

from continuous_history import latest_history, new_history_path, write_history
from workspace_io import save


class ContinuousHistoryTests(unittest.TestCase):
    def test_recovery_counts_timestamped_pending_once_and_uses_written_window_summary(self):
        with TemporaryDirectory() as scratch:
            results = Path(scratch)
            scenario = "relay-twenty-two"
            settled = results / "吞吐对照-混合冲榜-20261123-010000-000001"
            partial = results / "吞吐对照-混合冲榜-20261123-011000-000001"
            active = partial / scenario
            lane = active / "lane-1"
            lane.mkdir(parents=True)
            (settled / scenario).mkdir(parents=True)
            closed = {"live": True, "stop": "window", "errors": [], "comparable": True,
                      "uncached_input_tokens": 50, "output_tokens": 10, "metered_rounds": 4, "project_records": 1}
            save(settled / scenario / "summary.json", closed)
            save(active / "window.json", {"live": True, "seconds": 600})
            used = {"input_tokens": 20, "cached_input_tokens": 4, "output_tokens": 7}
            pending_usage = {"input_tokens": 10, "cached_input_tokens": 0, "output_tokens": 2}
            save(lane / "0001.json", {"in_window": True, "response_id": "again", "usage": used})
            save(lane / "pending-0001.json", {"state": "received", "received_elapsed": 2,
                                              "reply": {"response_id": "again", "usage": used}})
            save(lane / "pending-0002.json", {"state": "verified", "received_elapsed": 8,
                                              "reply": {"response_id": "confirmed", "usage": pending_usage}})
            save(lane / "pending-0003.json", {"state": "possibly-sent", "received_elapsed": 10,
                                              "reply": {"response_id": "unconfirmed", "usage": used}})
            save(lane / "pending-0004.json", {"state": "received",
                                              "reply": {"response_id": "missing-time", "usage": used}})
            save(lane / "pending-0005.json", {"state": "received", "received_elapsed": 601,
                                              "reply": {"response_id": "too-late", "usage": used}})
            history = new_history_path(results)
            write_history(history, scenario, 2, [settled, partial])

            session = latest_history(results)
            self.assertEqual((session["window"], session["completed"], session["end_reason"]), (2, 1, "recovered"))
            self.assertEqual((session["effective_tokens"], session["metered_rounds"], session["new_records"]),
                             (95, 6, 1))

            save(active / "summary.json", {**closed, "stop": "error", "errors": ["服务中断"],
                                            "uncached_input_tokens": 26, "output_tokens": 9,
                                            "metered_rounds": 2, "project_records": 0, "comparable": False})
            self.assertEqual(latest_history(results), {**session, "message": "服务中断"})


if __name__ == "__main__":
    unittest.main()
