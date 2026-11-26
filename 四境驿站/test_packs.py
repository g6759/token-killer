import json
from pathlib import Path
import unittest
from itertools import combinations

from packs import get_pack


FANO_BLOCKS = [
    [1, 2, 3],
    [1, 4, 5],
    [1, 6, 7],
    [2, 4, 6],
    [2, 5, 7],
    [3, 4, 7],
    [3, 5, 6],
]


class CoveragePackTests(unittest.TestCase):
    def test_frontier_seed_removes_one_block_from_the_public_cover(self):
        pack = get_pack("relay-49-frontier")
        baseline = json.loads(Path(__file__).with_name("frontier_49.json").read_text(encoding="utf-8"))
        blocks = baseline["blocks"]
        original = {pair for row in blocks for pair in combinations(row, 2)}
        self.assertEqual(len(blocks), 27)
        self.assertEqual(original, set(combinations(range(1, 50), 2)))
        removed = baseline["removed_block_1_based"] - 1
        self.assertEqual(pack.seed()["blocks"], blocks[:removed] + blocks[removed + 1:])
        result = pack.evaluate(pack.seed())
        self.assertEqual((result["score"], result["total"], len(result["missing"])), (1160, 1176, 16))

    def test_fano_solution_covers_all_pairs_in_any_order(self):
        pack = get_pack("relay-7")
        blocks = [block[::-1] for block in reversed(FANO_BLOCKS)]
        self.assertEqual(
            pack.evaluate({"blocks": blocks}),
            {
                "solution": {"blocks": FANO_BLOCKS},
                "score": 21,
                "total": 21,
                "missing": [],
            },
        )
        self.assertEqual(blocks[0], [6, 5, 3])

    def test_tutorial_seed_allows_two_strict_improvements(self):
        pack = get_pack("relay-7")
        solution = pack.seed()
        floor = pack.evaluate(solution)
        self.assertEqual(floor["score"], 17)
        self.assertEqual(floor["missing"], [[1, 3], [1, 7], [2, 3], [6, 7]])

        blocks = solution["blocks"]
        blocks[blocks.index([1, 2, 4])] = [1, 2, 3]
        self.assertEqual(pack.evaluate(solution)["score"], 19)
        blocks[blocks.index([1, 5, 6])] = [1, 6, 7]
        self.assertEqual(pack.evaluate(solution)["score"], 21)
        self.assertEqual(pack.seed(), floor["solution"])

    def test_research_seed_replays_at_870_out_of_1176(self):
        pack = get_pack("relay-49")
        self.assertEqual((pack.n, pack.k, pack.count), (49, 11, 26))
        self.assertIsInstance(pack.version, str)
        self.assertTrue(pack.version and pack.title)
        solution = pack.seed()
        report = pack.evaluate(solution)
        self.assertEqual(report["score"], 870)
        self.assertEqual(report["total"], 1176)
        self.assertEqual(report["solution"], solution)
        # Verify all missing pairs from membership in the original rows.
        expected_missing = [
            [left, right]
            for left, right in combinations(range(1, 50), 2)
            if not any(left in row and right in row for row in solution["blocks"])
        ]
        self.assertEqual(len(report["missing"]), 306)
        self.assertEqual(report["missing"], expected_missing)
        solution["blocks"][0][0] = 0
        self.assertEqual(pack.seed(), report["solution"])

    def test_rejects_wrong_shape_counts_and_extra_keys(self):
        pack = get_pack("relay-7")
        cases = [
            None,
            [],
            {},
            {"blocks": None},
            {"blocks": FANO_BLOCKS, "score": 21},
            {"blocks": FANO_BLOCKS[:-1]},
            {"blocks": FANO_BLOCKS + [[1, 2, 4]]},
            {"blocks": [[1, 2]] + FANO_BLOCKS[1:]},
            {"blocks": [[1, 2, 3, 4]] + FANO_BLOCKS[1:]},
            {"blocks": [(1, 2, 3)] + FANO_BLOCKS[1:]},
        ]
        for solution in cases:
            with self.subTest(solution=solution), self.assertRaises(ValueError):
                pack.evaluate(solution)

    def test_rejects_non_integer_and_out_of_range_stations(self):
        pack = get_pack("relay-7")
        for station in (True, False, 0, -1, 8, 1.0, "1", None, [1]):
            with self.subTest(station=station), self.assertRaises(ValueError):
                pack.evaluate({"blocks": [[station, 2, 3]] + FANO_BLOCKS[1:]})

    def test_rejects_duplicate_stations_and_reordered_duplicate_rows(self):
        pack = get_pack("relay-7")
        for block in ([1, 1, 3], [5, 4, 1]):
            with self.subTest(block=block), self.assertRaises(ValueError):
                pack.evaluate({"blocks": [block] + FANO_BLOCKS[1:]})

    def test_rejects_unknown_pack_id(self):
        with self.assertRaises(ValueError):
            get_pack("unknown")


if __name__ == "__main__":
    unittest.main()
