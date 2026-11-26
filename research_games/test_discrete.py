"""Public contract, seed, improvement and exact-judge checks for discrete packs."""
import unittest

from research_games import discrete


class CapSetTest(unittest.TestCase):
    def test_all_dimension_packs_have_legal_improvable_seed_and_example(self):
        for pack_id in ("cap-set", "cap-set-5d", "cap-set-6d", "cap-set-7d", "cap-set-8d"):
            with self.subTest(pack_id=pack_id):
                pack = discrete.get_pack(pack_id)
                seed = pack.evaluate(pack.seed())
                example = pack.evaluate(discrete.cap_set_improvement_example(pack_id))
                self.assertEqual(pack.id, pack_id)
                self.assertEqual(pack.pack_id, pack_id)
                self.assertEqual(pack.version, "1")
                self.assertEqual(seed["metrics"]["violating_triples"], 0)
                self.assertGreater(example["score"], seed["score"])

    def test_four_dimensional_reproducible_improvement_is_16_to_18(self):
        pack = discrete.get_pack("cap-set")
        self.assertEqual(pack.evaluate(pack.seed())["score"], 16)
        result = pack.evaluate(discrete.cap_set_improvement_example())
        self.assertEqual(result["score"], 18)
        self.assertEqual(result["total"], 81)

    def test_historical_401_point_reference_is_generated_and_rechecked(self):
        pack = discrete.get_pack("cap-set-8d")
        reference = discrete.cap_set_8d_401_reference()
        result = pack.evaluate(reference)
        self.assertEqual(len(reference["points"]), 401)
        self.assertEqual(result["score"], 401)
        self.assertTrue(result["metrics"]["matches_historical_401_reference"])
        reference["points"].clear()
        self.assertEqual(len(discrete.cap_set_8d_401_reference()["points"]), 401)

    def test_cap_set_rejects_invalid_coordinates_and_collinear_triples(self):
        pack = discrete.get_pack("cap-set")
        with self.assertRaisesRegex(ValueError, r"CAP_COORDINATE:.* / "):
            pack.evaluate({"points": [[0, 0, 0, True]]})
        with self.assertRaisesRegex(ValueError, r"CAP_COLLINEAR:.* / "):
            pack.evaluate({"points": [[0, 0, 0, 0], [1, 0, 0, 0], [2, 0, 0, 0]]})


class GolombRulerTest(unittest.TestCase):
    def test_all_mark_counts_have_legal_improvable_seed_and_sample(self):
        for pack_id in ("golomb-ruler", "golomb-ruler-12", "golomb-ruler-16"):
            with self.subTest(pack_id=pack_id):
                pack = discrete.get_pack(pack_id)
                seed = pack.evaluate(pack.seed())
                example = pack.evaluate(discrete.golomb_ruler_improvement_example(pack_id))
                self.assertEqual(seed["metrics"]["unique_differences"],
                                 pack.mark_count * (pack.mark_count - 1) // 2)
                self.assertGreater(example["score"], seed["score"])

    def test_eight_mark_example_improves_45_to_44(self):
        pack = discrete.get_pack("golomb-ruler")
        self.assertEqual(pack.evaluate(pack.seed())["metrics"]["length"], 45)
        result = pack.evaluate(discrete.golomb_ruler_improvement_example())
        self.assertEqual(result["metrics"]["length"], 44)
        self.assertEqual(result["total"], discrete.GOLUMB_TOTAL)

    def test_known_eight_mark_ruler_improves_again_and_reflection_deduplicates(self):
        pack = discrete.get_pack("golomb-ruler")
        ruler = [0, 1, 4, 9, 15, 22, 32, 34]
        forward = pack.evaluate({"marks": ruler})
        mirror = [ruler[-1] - mark for mark in reversed(ruler)]
        reverse_result = pack.evaluate({"marks": mirror})
        self.assertEqual(forward["metrics"]["length"], 34)
        self.assertGreater(forward["score"], pack.evaluate(pack.seed())["score"])
        self.assertEqual(forward["solution"], reverse_result["solution"])

    def test_duplicate_distances_and_noncanonical_origin_are_rejected(self):
        pack = discrete.get_pack("golomb-ruler")
        with self.assertRaisesRegex(ValueError, r"GOL_DUPLICATE_DIFFERENCE:.* / "):
            pack.evaluate({"marks": [0, 1, 2, 3, 4, 5, 6, 7]})
        with self.assertRaisesRegex(ValueError, r"GOL_MARK_RANGE:.* / "):
            pack.evaluate({"marks": [1, 2, 4, 8, 16, 32, 64, 128]})


if __name__ == "__main__":
    unittest.main()
