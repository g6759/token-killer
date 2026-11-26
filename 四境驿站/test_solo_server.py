import copy
import http.client
import json
import subprocess
import sys
import unittest
from contextlib import closing
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread

from solo_server import SoloServer


class SoloServerTest(unittest.TestCase):
    def setUp(self):
        directory = TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.db_path = Path(directory.name) / "solo.sqlite3"
        self.start_server()

    def start_server(self):
        self.server = SoloServer(self.db_path, port=0)
        self.worker = Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01})
        self.worker.start()
        self.addCleanup(self.stop_server, self.server, self.worker)

    @staticmethod
    def stop_server(server, worker):
        if worker.is_alive():
            server.shutdown()
            worker.join(timeout=3)
        server.server_close()

    def request(self, method, path, body=None, headers=None):
        with closing(http.client.HTTPConnection(*self.server.server_address, timeout=3)) as conn:
            conn.request(method, path, body, headers or {})
            response = conn.getresponse()
            payload = response.read().decode("utf-8")
            if response.getheader("Content-Type", "").startswith("application/json"):
                payload = json.loads(payload)
            return response.status, payload, response.headers

    def game(self, pack="relay-7"):
        status, game, _ = self.request("GET", f"/api/game?pack={pack}")
        self.assertEqual(status, 200, game)
        return game

    def test_agent_command_uses_real_loopback_and_stops_after_budget(self):
        status, current, _ = self.request("GET", "/api/game")
        self.assertEqual(status, 200)
        pack = current["state"]["pack_id"]
        self.assertEqual(pack, "relay-49-frontier")
        self.assertEqual(current["state"]["score"], 1160)
        status, handoff, _ = self.request("GET", "/api/mission?pack=" + pack + "&rounds=1")
        self.assertEqual(status, 200)
        self.assertIn(self.server.origin, handoff["prompt"])
        command = [sys.executable, "-B", "-X", "utf8", str(Path(__file__).with_name("ai_turn.py")),
                   "--url", self.server.origin]
        start = subprocess.run(command + ["start", "--pack", pack, "--rounds", "1"],
                               check=True, capture_output=True, encoding="utf-8")
        ticket = json.loads(start.stdout)
        candidate = self.candidate(current)["candidate"]
        source = self.db_path.with_name("candidate.json")
        source.write_text(json.dumps(candidate), encoding="utf-8")
        play = command + ["play", "--id", ticket["id"], "--file", str(source), "--author", "试局", "--turn"]
        reply = subprocess.run(play + ["1"], check=True, capture_output=True, encoding="utf-8")
        receipt = json.loads(reply.stdout)
        self.assertEqual((receipt["accepted"], receipt["remaining"]), (False, 0))
        stop = subprocess.run(play + ["2"], capture_output=True, encoding="utf-8")
        self.assertNotEqual(stop.returncode, 0)

    @staticmethod
    def candidate(game, old=None, new=None):
        blocks = copy.deepcopy(game["covered_blocks"])
        if old is not None:
            blocks[blocks.index(old)] = new
        return {
            "author": "单人玩家",
            "candidate": {
                "pack_id": game["state"]["pack_id"],
                "parent_hash": game["state"]["hash"],
                "solution": {"blocks": blocks},
                "handoff": "本轮整理名单。",
            },
        }

    def submit(self, candidate, **extra_headers):
        headers = {"Origin": self.server.origin, "Content-Type": "application/json"}
        headers.update(extra_headers)
        return self.request("POST", "/api/submit", json.dumps(candidate), headers)

    def test_one_player_can_finish_then_restart_and_view_report(self):
        initial = self.game()
        self.assertEqual(initial["state"]["score"], 17)
        self.assertEqual(len(initial["missing"]), 4)
        self.assertIn(initial["state"]["hash"], initial["prompt"])

        first = self.candidate(initial, [1, 2, 4], [1, 2, 3])
        status, second, _ = self.submit(first)
        self.assertEqual(status, 200, second)
        self.assertEqual(second["state"]["score"], 19)
        self.assertIn("+2", second["message"])
        self.assertEqual(len(second["missing"]), 2)

        status, complete, _ = self.submit(self.candidate(second, [1, 5, 6], [1, 6, 7]))
        self.assertEqual(status, 200, complete)
        self.assertEqual(complete["state"]["score"], 21)
        self.assertTrue(complete["state"]["complete"])
        self.assertEqual(complete["missing"], [])
        self.assertEqual(complete["prompt"], "")
        self.assertEqual([entry["score"] for entry in complete["lineage"]], [17, 19, 21])

        self.stop_server(self.server, self.worker)
        self.start_server()
        self.assertEqual(self.game()["state"], complete["state"])
        status, report, headers = self.request("GET", "/report?pack=relay-7")
        self.assertEqual(status, 200)
        self.assertTrue(headers["Content-Type"].startswith("text/html"))
        self.assertIn("单人玩家", report)
        self.assertEqual(self.game("relay-49")["state"]["n"], 49)

    def test_invalid_and_non_improving_candidates_do_not_save(self):
        invalid = {"author": "玩家", "candidate": {"pack_id": "relay-7"}}
        status, body, _ = self.submit(invalid)
        self.assertEqual(status, 400)
        self.assertIn("error", body)
        self.assertEqual(self.server.store.history("relay-7"), [])

        initial = self.game()
        status, result, _ = self.submit(self.candidate(initial))
        self.assertEqual(status, 400)
        self.assertEqual(result["score"], 17)
        self.assertEqual(result["missing"], initial["missing"])
        self.assertEqual(self.game(), initial)

    def test_host_origin_media_type_and_body_bounds(self):
        game = self.game()
        candidate = self.candidate(game, [1, 2, 4], [1, 2, 3])
        for headers, expected in (
            ({"Origin": "http://example.invalid"}, 403),
            ({"Host": "example.invalid"}, 403),
            ({"Content-Type": "text/plain"}, 415),
        ):
            status, result, response_headers = self.submit(candidate, **headers)
            self.assertEqual(status, expected, result)
            self.assertIn("error", result)
            self.assertIsNone(response_headers.get("Access-Control-Allow-Origin"))

        status, _, _ = self.request("POST", "/api/submit", json.dumps(candidate),
                                    {"Content-Type": "application/json"})
        self.assertEqual(status, 403)
        status, _, _ = self.request("POST", "/api/submit", headers={
            "Origin": self.server.origin,
            "Content-Type": "application/json",
            "Content-Length": str(128 * 1024 + 1),
        })
        self.assertEqual(status, 413)
        self.assertEqual(self.game(), game)

    def test_old_branch_and_duplicate_submission_report_current_best_honestly(self):
        initial = self.game()
        first = self.candidate(initial, [1, 2, 4], [1, 2, 3])
        status, second, _ = self.submit(first)
        self.assertEqual(status, 200)
        status, complete, _ = self.submit(self.candidate(second, [1, 5, 6], [1, 6, 7]))
        self.assertEqual(status, 200)
        status, alternative, _ = self.submit(self.candidate(initial, [1, 5, 6], [1, 6, 7]))
        self.assertEqual(status, 200)
        self.assertIn("分支", alternative["message"])
        self.assertEqual(alternative["state"], complete["state"])

        first["author"] = "另一位署名"
        status, repeat, _ = self.submit(first)
        self.assertEqual(status, 200)
        self.assertIn("已收录", repeat["message"])
        self.assertEqual(repeat["state"], complete["state"])
        self.assertEqual(len(self.server.store.history("relay-7")), 4)


if __name__ == "__main__":
    unittest.main()
