import copy
import hashlib
import json
import unittest
from pathlib import Path

from geometry import SCALE, get_square_pack, symmetry_key


class SquarePackingTest(unittest.TestCase):
    def setUp(self):
        self.pack = get_square_pack("square-18")

    def with_isolated_pair(self, first, second):
        # These 16 axis-aligned squares are separate from all pair fixtures.
        background = [
            [x * SCALE, y * SCALE, 0, 1]
            for x in range(4, 8)
            for y in range(4, 8)
        ]
        return {"poses": [first, second, *background]}

    def test_public_reference_seeds_are_legal_and_finer_reproductions_improve(self):
        data_path = Path(__file__).with_name("reference_layouts.json")
        layouts = json.loads(data_path.read_text(encoding="utf-8"))
        for count, seed_side, finer_side in ((18, 4_861_000, 4_822_914), (19, 4_924_000, 4_885_657)):
            with self.subTest(count=count):
                pack_id = f"square-{count}"
                pack = get_square_pack(pack_id)
                self.assertEqual(pack.n, count)
                self.assertEqual(pack.version, "1")
                seed = pack.seed()
                self.assertEqual(seed, layouts[pack_id]["seed"])
                result = pack.evaluate(seed)
                self.assertEqual(result["solution"], seed)
                self.assertEqual(result["side_ticks"], seed_side)
                self.assertLess(result["side_ticks"], 5 * SCALE)
                self.assertEqual(result["score"], result["total"] - seed_side)
                self.assertTrue(any(p > 0 for _, _, p, _ in seed["poses"]))
                finer = layouts[pack_id]["finer"]
                finer_result = pack.evaluate(finer)
                self.assertEqual(finer_result["solution"], finer)
                self.assertEqual(finer_result["side_ticks"], finer_side)
                self.assertGreater(finer_result["score"], result["score"])
                self.assertLess(finer_side ** 2, seed_side ** 2)

    def test_mutating_one_seed_does_not_change_later_seed(self):
        seed = self.pack.seed()
        seed["poses"].clear()
        self.assertEqual(len(self.pack.seed()["poses"]), self.pack.n)

    def test_axis_aligned_edge_contact_is_legal(self):
        solution = self.with_isolated_pair([0, 0, 0, 1], [SCALE, 0, 0, 1])
        self.pack.evaluate(solution)

    def test_axis_aligned_corner_contact_is_legal(self):
        solution = self.with_isolated_pair([0, 0, 0, 1], [SCALE, SCALE, 0, 1])
        self.pack.evaluate(solution)

    def test_one_tick_of_interior_overlap_is_illegal(self):
        solution = self.with_isolated_pair([0, 0, 0, 1], [SCALE - 1, 0, 0, 1])
        with self.assertRaisesRegex(ValueError, r"第 1 个方块与第 2 个方块重叠"):
            self.pack.evaluate(solution)

    def test_rotated_coplanar_edges_may_touch(self):
        solution = self.with_isolated_pair([0, 0, 1, 2], [600_000, 800_000, 1, 2])
        self.pack.evaluate(solution)

    def test_rotated_edge_overlap_below_one_tick_is_illegal(self):
        solution = self.with_isolated_pair([0, 0, 1, 2], [599_999, 800_000, 1, 2])
        with self.assertRaises(ValueError):
            self.pack.evaluate(solution)

    def test_rotated_corner_may_touch_axis_aligned_edge(self):
        solution = self.with_isolated_pair([0, 0, 0, 1], [1_200_000, 0, 1, 2])
        self.pack.evaluate(solution)
        solution["poses"][1][0] -= 1
        with self.assertRaises(ValueError):
            self.pack.evaluate(solution)

    def test_projection_on_the_second_squares_axes_can_separate_a_pair(self):
        solution = self.with_isolated_pair([0, 0, 0, 1], [800_000, 900_000, 1, 2])
        self.pack.evaluate(solution)
        solution["poses"][1][0] -= 1
        with self.assertRaises(ValueError):
            self.pack.evaluate(solution)

    def test_fractional_tick_is_rounded_up_and_drawn_to_original_units(self):
        solution = {
            "poses": [[(i % 5) * SCALE, (i // 5) * SCALE, 0, 1] for i in range(17)]
            + [[4 * SCALE, 4 * SCALE, 1, 4]]
        }
        result = self.pack.evaluate(solution)
        self.assertEqual(result["exact_side"], "88/17")
        self.assertEqual(result["side_ticks"], 5_176_471)
        drawing = self.pack.drawing(solution)
        self.assertEqual(len(drawing), 18)
        self.assertTrue(all(len(polygon) == 4 for polygon in drawing))
        self.assertEqual(drawing[0], [[0.0, 0.0], [1.0, 0.0], [1.0, 1.0], [0.0, 1.0]])
        self.assertEqual(max(x for polygon in drawing for x, y in polygon), 88 / 17)
        self.assertEqual(max(y for polygon in drawing for x, y in polygon), 88 / 17)

    def test_hash_and_score_are_independent_of_pose_order_and_translation(self):
        seed = self.pack.seed()
        translated = {
            "poses": [[x + 123, y + 456, p, q] for x, y, p, q in reversed(seed["poses"])]
        }
        original = copy.deepcopy(translated)
        result = self.pack.evaluate(translated)
        self.assertEqual(translated, original)
        self.assertEqual(result, self.pack.evaluate(seed))
        payload = json.dumps(seed, sort_keys=True, separators=(",", ":")).encode("utf-8")
        self.assertEqual(result["solution_hash"], hashlib.sha256(payload).hexdigest())

    def test_symmetry_key_matches_all_eight_symmetries_of_a_tilted_layout(self):
        original = self.with_isolated_pair([0, 0, 1, 3], [2 * SCALE, 300_000, 1, 4])
        original_result = self.pack.evaluate(original)
        reflection_angles = {(0, 1): (0, 1), (1, 3): (1, 2), (1, 4): (3, 5)}
        transforms = (
            (False, lambda x, y: (x, y)),
            (False, lambda x, y: (-y, x)),
            (False, lambda x, y: (-x, -y)),
            (False, lambda x, y: (y, -x)),
            (True, lambda x, y: (-x, y)),
            (True, lambda x, y: (-y, -x)),
            (True, lambda x, y: (x, -y)),
            (True, lambda x, y: (y, x)),
        )
        for index, (reflected, transform) in enumerate(transforms):
            with self.subTest(transform=index):
                transformed = []
                for x, y, p, q in reversed(original["poses"]):
                    tx, ty = transform(x, y)
                    angle = reflection_angles[p, q] if reflected else (p, q)
                    transformed.append([tx + 10 * SCALE, ty + 20 * SCALE, *angle])
                original_solution = original_result["solution"]
                candidate = self.pack.evaluate({"poses": transformed})
                self.assertEqual(symmetry_key({"poses": transformed}), symmetry_key(original_solution))
                self.assertEqual(symmetry_key(candidate["solution"]), symmetry_key(original_solution))
                if index:
                    self.assertNotEqual(candidate["solution_hash"], original_result["solution_hash"])

    def test_symmetry_key_distinguishes_different_centers_and_angles(self):
        original = self.with_isolated_pair([0, 0, 1, 3], [2 * SCALE, 300_000, 1, 4])
        original_key = symmetry_key(self.pack.evaluate(original)["solution"])
        for point in ([2 * SCALE, 300_001, 1, 4], [2 * SCALE, 300_000, 3, 5]):
            with self.subTest(pose=point):
                changed = self.with_isolated_pair([0, 0, 1, 3], point)
                self.assertNotEqual(symmetry_key(self.pack.evaluate(changed)["solution"]), original_key)

    def test_coordinates_at_both_limits_receive_a_nonnegative_score(self):
        solution = self.with_isolated_pair([0, 0, 1, 2], [100_000_000, 0, 1, 2])
        result = self.pack.evaluate(solution)
        self.assertEqual(result["side_ticks"], 101_400_000)
        self.assertGreaterEqual(result["score"], 0)

    def test_malformed_or_out_of_range_poses_raise_value_error(self):
        cases = (
            [-1, 0, 0, 1], [0, 100_000_001, 0, 1], [True, 0, 0, 1],
            [0, 0.0, 0, 1], [0, 0, 0, True], [0, 0, 0, 2], [0, 0, 2, 4],
            [0, 0, 1, 1], [0, 0, -1, 1], [0, 0, 1, 0], [0, 0, 1, 10_001],
            [0, 0, 1],
        )
        for pose in cases:
            with self.subTest(pose=pose):
                solution = self.pack.seed()
                solution["poses"][0] = pose
                with self.assertRaises(ValueError):
                    self.pack.evaluate(solution)

    def test_invalid_solution_shape_or_unknown_problem_is_rejected(self):
        for solution in (None, [], {}, {"poses": []}):
            with self.subTest(solution=solution), self.assertRaises(ValueError):
                self.pack.evaluate(solution)
        with self.assertRaises(ValueError):
            get_square_pack("square-21")


if __name__ == "__main__":
    unittest.main()
