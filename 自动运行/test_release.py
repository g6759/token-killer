from pathlib import Path
from shutil import copy2
from tempfile import TemporaryDirectory
import unittest
from uuid import uuid4
from zipfile import ZipFile

import release
import scoreboard


class ReleaseTests(unittest.TestCase):
    def test_zip_keeps_public_proofs_and_excludes_local_records(self):
        if not (release.ROOT / "public" / "leaderboard.json").exists():
            self.skipTest("The audited leaderboard is not part of a source checkout")
        with TemporaryDirectory() as scratch:
            archive = Path(scratch) / "release.zip"
            release.build_archive(release.ROOT, archive)
            with ZipFile(archive) as package:
                names = set(package.namelist())
                self.assertEqual(package.testzip(), None)
                self.assertTrue({"lab.py", "scoreboard.py", "public/leaderboard.json",
                                 "启动解题实验室.cmd", "LICENSE", "游戏MCP/autoplay.py"} <= names)
                self.assertTrue(all("sqlite3" not in name and "验收/" not in name and "运行中/" not in name
                                    and "复验/" not in name and ".codex/" not in name and ".dpapi" not in name
                                    for name in names))

    def test_source_only_is_an_exact_allowlist_and_needs_no_leaderboard(self):
        with TemporaryDirectory() as scratch:
            workspace = Path(scratch) / "checkout"
            workspace.mkdir()
            expected = {Path(directory, filename).as_posix()
                        for directory, filenames in release.SOURCE_ONLY.items()
                        for filename in filenames}
            for name in expected:
                source = release.ROOT / name
                target = workspace / name
                target.parent.mkdir(parents=True, exist_ok=True)
                copy2(source, target)
            marker = uuid4().hex
            (workspace / "public").mkdir()
            (workspace / "public" / "leaderboard.json").write_text(marker, encoding="utf-8")
            (workspace / ".env").write_text(marker, encoding="utf-8")
            (workspace / "usage-report.json").write_text(marker, encoding="utf-8")
            archive = Path(scratch) / "source.zip"
            release.build_archive(workspace, archive, release.SOURCE_ONLY, source_only=True)
            with ZipFile(archive) as package:
                self.assertIsNone(package.testzip())
                self.assertEqual(set(package.namelist()), expected | {"LICENSE"})
                self.assertEqual(package.read("LICENSE"), package.read("四境驿站/LICENSE"))
                self.assertEqual((package.getinfo("start.sh").external_attr >> 16) & 0o777, 0o755)
                self.assertTrue({"lab.py", "start.sh", "README.en.md", "CONTRIBUTING.md",
                                 "自动运行/test_lab.py", "方格子容纳/test_game.py",
                                 "四境驿站/test_solo_server.py", ".github/workflows/ci.yml"} <= expected)
                self.assertNotIn(marker.encode(), b"".join(package.read(name) for name in package.namelist()))

    def test_source_only_rejects_other_manifests_and_receipts(self):
        with TemporaryDirectory() as scratch:
            archive = Path(scratch) / "source.zip"
            with self.assertRaises(ValueError):
                release.build_archive(release.ROOT, archive, release.SOURCE, source_only=True)
            with self.assertRaises(ValueError):
                release.build_archive(release.ROOT, archive, release.SOURCE_ONLY,
                                      source_only=True, evidence_run=Path(scratch))

    def test_public_verifier_from_synthetic_scores(self):
        with TemporaryDirectory() as scratch:
            workspace = Path(scratch) / "checkout"
            for directory, filenames in release.VERIFIER.items():
                if directory == "public":
                    continue
                for filename in filenames:
                    source = release.ROOT / directory / filename
                    target = workspace / directory / filename
                    target.parent.mkdir(parents=True, exist_ok=True)
                    copy2(source, target)
            license_path = workspace / "四境驿站" / "LICENSE"
            license_path.parent.mkdir(parents=True, exist_ok=True)
            copy2(release.ROOT / "四境驿站" / "LICENSE", license_path)
            scoreboard.export(workspace, workspace / "public")
            archive = Path(scratch) / "verifier.zip"
            release.build_archive(workspace, archive, release.VERIFIER)
            with ZipFile(archive) as package:
                self.assertIsNone(package.testzip())
                self.assertIn("public/leaderboard.json", package.namelist())
                self.assertNotIn("public/index.html", package.namelist())


if __name__ == "__main__":
    unittest.main()
