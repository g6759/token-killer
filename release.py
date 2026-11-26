"""Package the local games, an audited leaderboard, or a clean source release."""

import argparse
import json
from pathlib import Path
from tempfile import TemporaryDirectory
from zipfile import ZIP_DEFLATED, ZipFile, ZipInfo

from scoreboard import ROOT, collect, page, verify

LAB_UI = ("index.html", "lab.css", "lab.js", "console.js", "research.html", "research.css", "research.js")
RESEARCH = ("__init__.py", "discrete.py", "lorenz.py")

SOURCE = {
    "": ("lab.py", "scoreboard.py", "benchmark_proof.py", "release.py", "README.md", ".gitignore", "requirements.txt",
         "mcp.config.example.toml", "启动解题实验室.cmd", "启动方格子容纳.cmd", "启动四镜驿站.cmd"),
    "实验室": LAB_UI,
    "research_games": RESEARCH,
    "四境驿站": ("LICENSE", "ai_turn.py", "auto_jobs.py", "auto_search.py", "autorun.js", "baseline.py",
               "frontier_49.json", "mission.py", "neighborhood.py", "packs.py", "relay.py", "relay_store.py",
               "report.py", "solo_server.py", "solo.html", "subscription_worker.py"),
    "方格子容纳": ("batch.js", "board.html", "game.py", "geometry.py", "lite.html", "lite.js", "lite_game.py",
                "lite_submission.py", "packing_batches.py", "packing_catalog.py", "packing_usage.py",
                "reference_layouts.json", "reference_sync.py"),
    "自动运行": ("benchmark.py", "continuous_history.py", "evidence.py", "guided_search.py", "relay_frontier.py",
             "prepared_frontiers.py", "square_frontier.py", "weekly_meter.py", "quota.py", "workspace_io.py", "本地裁判.py"),
    "游戏MCP": ("autoplay.py", "requirements.txt", "server.py"),
    "微观生命海": ("subscription.py",),
    "public": ("index.html", "leaderboard.json", "verifier.zip"),
}
VERIFIER = {
    "": ("scoreboard.py",),
    "research_games": RESEARCH,
    "四境驿站": ("frontier_49.json", "packs.py", "relay_store.py"),
    "方格子容纳": ("geometry.py", "packing_catalog.py", "reference_layouts.json"),
    "public": ("leaderboard.json",),
}

# Deliberately enumerate publishable source. Do not derive this from the worktree,
# git status, or the existing proof bundle: those may contain personal results.
SOURCE_ONLY = {
    "": ("lab.py", "scoreboard.py", "benchmark_proof.py", "release.py",
         "README.md", "README.en.md", "CONTRIBUTING.md", ".gitignore", "requirements.txt",
         "mcp.config.example.toml", "start.sh", "启动解题实验室.cmd",
         "启动方格子容纳.cmd", "启动四镜驿站.cmd"),
    "实验室": LAB_UI,
    "research_games": (*RESEARCH, "test_discrete.py", "test_lorenz.py"),
    "四境驿站": ("LICENSE", "ai_turn.py", "auto_jobs.py", "auto_search.py", "autorun.js",
               "baseline.py", "frontier_49.json", "mission.py", "neighborhood.py", "packs.py",
               "relay.py", "relay_store.py", "report.py", "solo_server.py", "solo.html",
               "subscription_worker.py", "test_auto_jobs.py", "test_auto_search.py", "test_baseline.py",
               "test_cli.py", "test_mission.py", "test_neighborhood.py", "test_packs.py",
               "test_solo_server.py", "test_store.py", "test_subscription_worker.py"),
    "方格子容纳": ("batch.js", "board.html", "game.py", "geometry.py", "lite.html", "lite.js",
                "lite_game.py", "lite_submission.py", "packing_batches.py", "packing_catalog.py",
                "packing_usage.py", "reference_layouts.json", "reference_sync.py", "test_game.py",
                "test_geometry.py", "test_lite_submission.py", "test_live_board.py",
                "test_packing_batches.py", "test_packing_usage.py", "test_reference_sync.py"),
    "自动运行": ("benchmark.py", "codex_driver.py", "continuous_history.py", "desktop_driver.py",
             "evidence.py", "guided_search.py", "prepared_frontiers.py", "relay_frontier.py",
             "run.py", "square_frontier.py", "weekly_meter.py", "quota.py", "test_quota.py", "workspace_io.py", "本地裁判.py",
             "test_benchmark.py", "test_benchmark_proof.py", "test_continuous_history.py",
             "test_driver.py", "test_evidence.py", "test_judge.py", "test_lab.py",
             "test_prepared_frontiers.py", "test_queue.py", "test_relay_frontier.py",
             "test_release.py", "test_scoreboard.py", "test_square_frontier.py", "test_weekly_meter.py"),
    "游戏MCP": ("autoplay.py", "requirements.txt", "server.py", "test_protocol.py",
               "test_packing_lite_web.py", "test_packing_batch_web.py", "test_web_codex.py"),
    "微观生命海": ("subscription.py", "test_subscription.py"),
    ".github/ISSUE_TEMPLATE": ("throughput-method.yml",),
    ".github/workflows": ("ci.yml",),
}


def build_archive(workspace, destination, source_files=SOURCE, *, evidence_run=None, source_only=False):
    workspace = workspace.resolve()
    if source_only:
        if source_files is not SOURCE_ONLY or evidence_run is not None:
            raise ValueError("源码分发只能使用固定白名单，不能包含回执。")
    else:
        content = json.loads((workspace / "public" / "leaderboard.json").read_text(encoding="utf-8"))
        verify(content)
    names = [Path(directory, item).as_posix() for directory, files in source_files.items() for item in files]
    files = {name: name for name in names}
    files["LICENSE"] = "四境驿站/LICENSE"
    evidence = {}
    if evidence_run is not None:
        from benchmark_proof import collect_run

        scores = verify(collect(workspace))
        evidence = {
            "复验/吞吐回执.json": json.dumps(collect_run(evidence_run), ensure_ascii=False, allow_nan=False),
            "复验/leaderboard.json": json.dumps(scores, ensure_ascii=False, allow_nan=False),
            "复验/index.html": page(scores),
        }
    destination.parent.mkdir(parents=True, exist_ok=True)
    with TemporaryDirectory(prefix="release-", dir=destination.parent) as scratch:
        temporary = Path(scratch) / destination.name
        with ZipFile(temporary, "w", compression=ZIP_DEFLATED) as archive:
            for name, source in sorted(files.items()):
                path = (workspace / source).resolve()
                if not path.is_relative_to(workspace):
                    raise ValueError(f"发布文件指向工作区以外。{name}")
                if not path.is_file():
                    raise FileNotFoundError(f"发布文件不存在：{name}")
                info = ZipInfo(name)
                if source_only:
                    info.create_system = 3
                    info.external_attr = (0o100755 if name == "start.sh" else 0o100644) << 16
                archive.writestr(info, path.read_bytes(), compress_type=ZIP_DEFLATED)
            for name, contents in evidence.items():
                archive.writestr(ZipInfo(name), contents.encode("utf-8"), compress_type=ZIP_DEFLATED)
        temporary.replace(destination)
    return len(files) + len(evidence)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out", type=Path, required=True)
    modes = parser.add_mutually_exclusive_group()
    modes.add_argument("--public-verifier", action="store_true")
    modes.add_argument("--evidence-run", type=Path, help="在本地包中加入指定对照的脱敏回执及当前成绩。")
    modes.add_argument("--source-only", action="store_true", help="只打包白名单中的源码和测试，不包含公开成绩或本地数据。")
    args = parser.parse_args()
    try:
        selected = SOURCE_ONLY if args.source_only else VERIFIER if args.public_verifier else SOURCE
        count = build_archive(ROOT, args.out, selected, evidence_run=args.evidence_run,
                              source_only=args.source_only)
        if args.public_verifier and args.out.resolve() == ROOT / "public" / "verifier.zip":
            proof = verify(json.loads((ROOT / "public" / "leaderboard.json").read_text(encoding="utf-8")))
            (ROOT / "public" / "index.html").write_text(page(proof, verifier=True), encoding="utf-8")
    except (OSError, ValueError, KeyError, TypeError) as error:
        parser.exit(1, f"打包失败。{error}\n")
    print(f"{count} 个文件 · {args.out}")


if __name__ == "__main__":
    main()
