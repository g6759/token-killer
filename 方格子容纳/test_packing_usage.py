import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from uuid import uuid4

from packing_usage import CodexUsage


def counts(n):
    return dict(input_tokens=8*n, cached_input_tokens=2*n,
                output_tokens=2*n, total_tokens=10*n)


def event(second, total, last=None):
    info = {"total_token_usage": counts(total), "last_token_usage": counts(last or total)}
    return {"timestamp": f"1970-01-01T00:00:{second:02}.000Z", "type": "event_msg",
            "payload": {"type": "token_count", "info": info}}


class UsageTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.thread = str(uuid4())
        folder = self.root / "1970/01/01"
        folder.mkdir(parents=True)
        self.path = folder / f"rollout-1970-01-01T00-00-00-{self.thread}.jsonl"
        self.header = {"type": "session_meta", "timestamp": "1970-01-01T00:00:00Z",
                       "payload": {"id": self.thread}}
        self.append(self.header)
        self.usage = CodexUsage(self.root)

    def append(self, *items):
        with self.path.open("ab") as stream:
            for item in items:
                stream.write(json.dumps(item).encode() + b"\n")

    def sample(self, start, end=None):
        return self.usage.sample(self.thread, start, end)

    def test_interval_includes_boundary_and_ignores_duplicate(self):
        self.append(event(5, 7), event(10, 10, 3), event(11, 10, 3))
        self.assertEqual(self.sample(10, 10), counts(3))
        self.append(event(15, 14, 4))
        self.assertEqual(self.sample(10, 11), counts(3))
        self.assertEqual(self.sample(12), counts(4))
        self.assertEqual(self.sample(6, 9), counts(0))
        with ThreadPoolExecutor(max_workers=5) as pool:
            self.assertEqual(list(pool.map(lambda _: self.sample(10, 10), range(20))),
                             [counts(3)] * 20)

    def test_no_evidence_and_partial_line(self):
        self.assertIsNone(self.sample(1))
        blob = json.dumps(event(8, 4)).encode() + b"\n"
        with self.path.open("ab") as stream:
            stream.write(blob[:-10])
        self.assertIsNone(self.sample(1))
        with self.path.open("ab") as stream:
            stream.write(blob[-10:])
        self.assertEqual(self.sample(1), counts(4))
        self.assertIsNone(self.sample(1, 6))

    def test_unverifiable_start_and_reset(self):
        self.append(event(3, 100, 2), event(5, 104, 4), event(8, 3), event(10, 8, 5))
        self.assertIsNone(self.sample(1))
        self.assertIsNone(self.sample(1, 5))
        self.assertIsNone(self.sample(4))
        self.assertEqual(self.sample(9), counts(5))
        self.assertEqual(self.sample(4, 5), counts(4))

    def test_bad_sources(self):
        self.assertIsNone(self.usage.sample("../*", 1))
        self.assertIsNone(self.usage.sample(str(uuid4()), 1))
        self.assertIsNone(self.sample(float("nan")))
        self.assertIsNone(self.sample(3, 2))
        self.path.write_bytes(b"")
        self.append(dict(self.header, payload={"id": str(uuid4())}), event(5, 5))
        self.assertIsNone(self.sample(1))

    def test_replacement_revalidates_identity_and_drops_cache(self):
        self.append(event(8, 4))
        self.assertEqual(self.sample(1), counts(4))
        self.path.rename(self.path.with_suffix(".old"))
        self.append(dict(self.header, payload={"id": str(uuid4())}), event(8, 40))
        self.assertIsNone(self.sample(1))

    def test_truncate_and_regrow_between_polls(self):
        self.append(event(8, 4))
        self.assertEqual(self.sample(1), counts(4))
        self.path.write_bytes(b"")
        self.append(dict(self.header, payload={"id": str(uuid4())}), event(8, 40), event(9, 41))
        self.assertIsNone(self.sample(1))

    def test_large_irrelevant_partial_record(self):
        self.append(event(5, 4))
        with self.path.open("ab") as stream:
            stream.write(b'{"type":"response_item","payload":{"text":"' + b'x' * 500000)
        self.assertEqual(self.sample(1), counts(4))
        with self.path.open("ab") as stream:
            stream.write(b'more"}}\n')
        self.append(event(8, 5, 1))
        self.assertEqual(self.sample(6), counts(1))

    def test_invalid_counter_and_null_info(self):
        bad = event(9, 10)
        bad["payload"]["info"]["total_token_usage"]["input_tokens"] = True
        self.append(event(5, 5), dict(event(6, 5), payload={"type":"token_count", "info":None}))
        self.assertEqual(self.sample(2, 6), counts(5))
        self.append(bad)
        self.assertIsNone(self.sample(6))


if __name__ == "__main__":
    unittest.main()
