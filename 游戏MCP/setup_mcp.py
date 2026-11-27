"""Generate copyable local stdio MCP configs for Codex, Claude Code and Antigravity."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys


def configs(root: Path, python: str) -> dict[str, str]:
    command = {
        "command": python,
        "args": ["-B", "-X", "utf8", str(root / "游戏MCP" / "server.py")],
    }
    codex = (
        "# Merge this block into ~/.codex/config.toml; Codex CLI and IDE share it.\n"
        "[mcp_servers.research_game]\n"
        f"command = {json.dumps(python, ensure_ascii=False)}\n"
        f"args = [\"-B\", \"-X\", \"utf8\", {json.dumps(str(root / '游戏MCP' / 'server.py'), ensure_ascii=False)}]\n"
        "startup_timeout_sec = 30\n"
    )
    claude = {
        "mcpServers": {
            "research-game": {
                **command,
            }
        }
    }
    antigravity = {
        "mcpServers": {
            "research-game": {
                **command,
            }
        }
    }
    return {
        "codex.config.toml": codex,
        "claude-code.mcp.json": json.dumps(claude, ensure_ascii=False, indent=2) + "\n",
        "antigravity.mcp_config.json": json.dumps(antigravity, ensure_ascii=False, indent=2) + "\n",
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--out-dir", type=Path, required=True,
                        help="写入独立配置片段的目录，不触碰任何客户端配置文件")
    parser.add_argument("--python", default=sys.executable,
                        help="该游戏运行环境的 Python 可执行文件，默认当前解释器")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    output = args.out_dir.resolve()
    output.mkdir(parents=True, exist_ok=True)
    for name, contents in configs(root, args.python).items():
        (output / name).write_text(contents, encoding="utf-8", newline="\n")
    print(f"已生成 3 份独立配置片段：{output}")
    print("这些文件可手动合并到客户端配置；生成器不会修改任何客户端配置。")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
