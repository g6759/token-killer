import unittest

from mission import MissionBook


class MissionTests(unittest.TestCase):
    def test_failed_attempt_costs_one_round_and_retries_do_not_consume_another(self):
        book = MissionBook()
        ticket = book.start("relay-49-frontier", 3)
        calls = []

        def judge(payload):
            calls.append(payload)
            raise ValueError("候选重叠")

        item = {"pack_id": ticket["pack_id"]}
        first = book.play(ticket["id"], 1, {"author": "玩家", "candidate": item}, judge)
        self.assertEqual((first["remaining"], first["accepted"]), (2, False))
        self.assertEqual(first, book.play(ticket["id"], 1, {"author": "玩家", "candidate": item}, judge))
        for index in [2, 3]:
            last = book.play(ticket["id"], index, {"author": "玩家", "candidate": item}, judge)
        self.assertEqual(last["remaining"], 0)
        with self.assertRaises(ValueError):
            book.play(ticket["id"], 4, {"author": "玩家", "candidate": item}, judge)
        self.assertEqual(len(calls), 3)

    def test_cannot_change_pack_or_retry_with_a_different_payload(self):
        book = MissionBook()
        ticket = book.start("square-18", 1)
        packet = {"author": "甲", "candidate": {"pack_id": "square-19"}}
        with self.assertRaises(ValueError):
            book.play(ticket["id"], 1, packet, lambda _: {})
        packet["candidate"]["pack_id"] = "square-18"
        r = book.play(ticket["id"], 1, packet, lambda _: {"message": "有效"})
        self.assertTrue(r["accepted"])
        with self.assertRaises(ValueError):
            book.play(ticket["id"], 1, {**packet, "author": "乙"}, lambda _: {})
