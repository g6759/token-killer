from pathlib import Path
import subprocess
import sys
import unittest


HERE = Path(__file__).resolve().parent


class BaselineTest(unittest.TestCase):
    def run_baseline(self, *args):
        command = [sys.executable, "-B", "-X", "utf8", str(HERE / "baseline.py"), *args]
        return subprocess.run(command, cwd=HERE, check=True, capture_output=True, encoding="utf-8").stdout

    def test_fixed_seed_one_step_counts(self):
        output = self.run_baseline()
        self.assertIn("枚举 10868 个合法邻居 · 提升 3888 · 持平 1803 · 降低 5177", output)
        self.assertIn("种子 870/1176 · 一步最优 879/1176 · 并列 2 个", output)
        self.assertIn("第 3 组，4 → 17", output)

    def test_six_steps_are_reproducible(self):
        first = self.run_baseline("--steps", "6")
        second = self.run_baseline("--steps", "6")
        self.assertEqual(first, second)
        self.assertIn("870 → 879 → 888 → 896 → 903 → 910 → 916", first)


if __name__ == "__main__":
    unittest.main()
