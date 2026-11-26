import json
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


HERE = Path(__file__).resolve().parent
FANO = [[1, 2, 3], [1, 4, 5], [1, 6, 7], [2, 4, 6],
        [2, 5, 7], [3, 4, 7], [3, 5, 6]]


class RelayCliTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.root = Path(tmp.name)

    def run_cli(self, *args, db="local.db", ok=True):
        run = subprocess.run(
            [sys.executable, "-X", "utf8", str(HERE / "relay.py"), "--db", db, *args],
            cwd=self.root, capture_output=True, encoding="utf-8",
        )
        self.assertEqual(run.returncode == 0, ok, run.stderr + run.stdout)
        return run.stdout if ok else run.stderr

    def test_new_player_can_finish_then_transfer_and_view_result(self):
        state = json.loads(self.run_cli("board", "--pack", "relay-7", "--json"))
        seed = state["best"]["hash"]
        self.run_cli("task", "--pack", "relay-7", "--out", "task.txt")
        task = (self.root / "task.txt").read_text(encoding="utf-8-sig")
        self.assertIn(seed, task)
        candidate = {"parent_hash": seed, "pack_id": "relay-7", "solution": {"blocks": FANO},
                     "handoff": "覆盖全部。<script>alert(1)</script>"}
        (self.root / "play.json").write_text(json.dumps(candidate), encoding="utf-8-sig")
        self.assertIn("21/21", self.run_cli("check", "play.json"))
        self.assertEqual(json.loads(self.run_cli("board", "--pack", "relay-7", "--json"))["best"]["hash"], seed)
        self.assertIn("21/21", self.run_cli("submit", "play.json", "--author", "青禾"))
        self.run_cli("export", "--pack", "relay-7", "handoff.json")
        self.run_cli("import", "handoff.json", db="peer.db")
        peer = json.loads(self.run_cli("board", "--pack", "relay-7", "--json", db="peer.db"))
        self.assertEqual(peer["best"]["state"]["score"], 21)
        self.run_cli("board", "--pack", "relay-7", "--html", "report.html", db="peer.db")
        page = (self.root / "report.html").read_text(encoding="utf-8")
        self.assertIn("青禾", page)
        self.assertNotIn("<script>alert(1)</script>", page)
        self.assertIn("21", page)
        self.assertIn("relay-49", self.run_cli("routes"))

    def test_default_cli_starts_the_frontier(self):
        state = json.loads(self.run_cli("board", "--json"))["best"]["state"]
        self.assertEqual((state["pack_id"], state["score"], state["total"]), ("relay-49-frontier", 1160, 1176))
        prompt = self.run_cli("task")
        self.assertIn("父档覆盖 1160/1176 对", prompt)
        self.assertIn('"pack_id": "relay-49-frontier"', prompt)


    def test_invalid_input_and_protected_output(self):
        (self.root / "bad.json").write_text('{"oops": 0}', encoding="utf-8")
        self.assertIn("parent_hash", self.run_cli("submit", "bad.json", "--author", "甲", ok=False))
        (self.root / "notes.txt").write_text("保留", encoding="utf-8")
        self.run_cli("task", "--out", "notes.txt", ok=False)
        self.assertEqual((self.root / "notes.txt").read_text(encoding="utf-8"), "保留")


if __name__ == "__main__":
    unittest.main()
