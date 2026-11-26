"""Independent public-contract and reference checks for the Lorenz pack."""
import importlib.util
import math
import unittest

SPEC = importlib.util.find_spec("research_games.lorenz")
if SPEC is not None:
    from research_games import lorenz


class AvailabilityTest(unittest.TestCase):
    def test_numeric_kernel_exists(self):
        self.assertIsNotNone(SPEC, "The Lorenz numerical kernel is not implemented")


@unittest.skipIf(SPEC is None, "Kernel not implemented yet")
class LorenzTest(unittest.TestCase):
    def test_pack_contract_and_seed(self):
        pack = lorenz.get_pack("lorenz-divergence")
        self.assertEqual(pack.version, "1")
        self.assertEqual(pack.pack_id, "lorenz-divergence")
        self.assertEqual(pack.seed(), {"initial_x_tick": 200})
        with self.assertRaises(ValueError):
            lorenz.get_pack("another-pack")

    def test_original_reference_samples_and_ranking(self):
        # Reference observations in the project's proposal, independently made
        # using SciPy DOP853 and RK45. These are not exact analytic constants.
        for tick, expected, score in ((100, 21.55637, 1844), (200, 23.69553, 1630)):
            with self.subTest(tick=tick):
                result = lorenz.get_pack().evaluate({"initial_x_tick": tick})
                self.assertTrue(result["certified"], result)
                self.assertEqual(result["status"], "certified")
                self.assertAlmostEqual(result["crossing_time"], expected, delta=0.001)
                self.assertEqual(result["score"], score)
                self.assertEqual(result["total"], 4000)
                self.assertEqual(len(result["audits"]), 2)
                self.assertNotEqual(result["audits"][0]["method"], result["audits"][1]["method"])
                self.assertTrue(all(a["converged"] for a in result["audits"]))

    def test_invalid_or_unbounded_solutions_are_rejected(self):
        for value in (None, [], {}, {"initial_x_tick": True}, {"initial_x_tick": 100.0},
                      {"initial_x_tick": 99}, {"initial_x_tick": 201},
                      {"initial_x_tick": 100, "score": 9999}):
            with self.subTest(value=value), self.assertRaises(ValueError):
                lorenz.get_pack().evaluate(value)

    def test_insufficient_refinement_budget_is_visible_and_unscored(self):
        result = lorenz.evaluate_solution({"initial_x_tick": 100}, max_refinements=0)
        self.assertFalse(result["certified"])
        self.assertEqual(result["status"], "uncertain")
        self.assertEqual(result["score"], 0)
        self.assertTrue(result["reason"])

    def test_repeated_evaluation_is_reproducible_and_not_mutable_cache(self):
        first = lorenz.get_pack().evaluate({"initial_x_tick": 100})
        second = lorenz.get_pack().evaluate({"initial_x_tick": 100})
        self.assertEqual(first, second)
        first["solution"]["initial_x_tick"] = 199
        first["audits"].clear()
        self.assertEqual(second, lorenz.get_pack().evaluate({"initial_x_tick": 100}))

    def test_animation_samples_are_real_two_trajectories(self):
        result = lorenz.sample_trajectory({"initial_x_tick": 100}, sample_count=201)
        for key in ("times", "trajectory_a", "trajectory_b", "distances"):
            self.assertEqual(len(result[key]), 201)
        self.assertEqual(result["times"][0], 0.0)
        self.assertEqual(result["times"][-1], 40.0)
        self.assertEqual(result["trajectory_a"][0], [1.0, 1.0, 1.0])
        self.assertEqual(result["trajectory_b"][0], [1.00001, 1.0, 1.0])
        self.assertTrue(all(math.isfinite(v) for p in result["trajectory_a"] for v in p))
        for a, b, distance in zip(result["trajectory_a"], result["trajectory_b"], result["distances"]):
            self.assertAlmostEqual(math.dist(a, b), distance, places=12)
        times = result["times"]
        first = next(i for i, d in enumerate(result["distances"]) if d >= 1)
        self.assertLess(times[first - 1], 21.55637)
        self.assertGreater(times[first], 21.55637)

    def test_sample_limits(self):
        for n in (True, 1, 10001, 10.5):
            with self.subTest(n=n), self.assertRaises(ValueError):
                lorenz.sample_trajectory({"initial_x_tick": 100}, sample_count=n)


if __name__ == "__main__":
    unittest.main()
