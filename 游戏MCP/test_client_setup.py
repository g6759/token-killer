from __future__ import annotations

import asyncio
import json
from pathlib import Path
import site
import sys
import tempfile
import unittest
from unittest import IsolatedAsyncioTestCase

deps = Path(__file__).resolve().parent / ".deps"
sys.path.insert(0, str(deps))
site.addsitedir(str(deps))

from 游戏MCP.setup_mcp import configs
from 游戏MCP.server import make_server
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from mcp.shared.memory import create_connected_server_and_client_session


ROOT = Path(__file__).resolve().parents[1]
JOB = "0123456789abcdef0123456789abcdef"


class ClientSetupTests(unittest.TestCase):
    def test_generated_client_shapes_are_separate_and_local(self):
        generated = configs(ROOT, sys.executable)
        self.assertEqual(set(generated), {
            "codex.config.toml", "claude-code.mcp.json", "antigravity.mcp_config.json",
        })
        self.assertIn("[mcp_servers.research_game]", generated["codex.config.toml"])
        for name in ("claude-code.mcp.json", "antigravity.mcp_config.json"):
            doc = json.loads(generated[name])
            entry = doc["mcpServers"]["research-game"]
            self.assertEqual(entry["command"], sys.executable)
            self.assertEqual(entry["args"], ["-B", "-X", "utf8", str(ROOT / "游戏MCP" / "server.py")])

    def test_generated_stdio_command_discovers_tools_from_arbitrary_cwd(self):
        entry = json.loads(configs(ROOT, sys.executable)["claude-code.mcp.json"])["mcpServers"]["research-game"]
        async def exercise():
            with tempfile.TemporaryDirectory() as unrelated_cwd:
                registry = Path(unrelated_cwd) / "registry"
                registry.mkdir()
                params = StdioServerParameters(command=entry["command"], args=entry["args"], cwd=unrelated_cwd,
                                               env={"GAME_MCP_REGISTRY": str(registry)})
                async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                    await session.initialize()
                    result = await session.list_tools()
                    names = {tool.name for tool in result.tools}
                    self.assertIn("game_status", names)
                    self.assertIn("claim_turn", names)
                    self.assertIn("submit_turn", names)
        asyncio.run(asyncio.wait_for(exercise(), timeout=30))


class SyntheticMcpRoundTests(IsolatedAsyncioTestCase):
    async def test_tool_discovery_and_synthetic_handoff_in_memory(self):
        def dispatch(pack, path, payload=None):
            if path.startswith("/api/auto?pack="):
                return {"jobs": [{"id": JOB, "status": "waiting"}]}
            if path == "/api/auto/claim":
                return {"id": payload["id"], "turn": 1, "prompt": "synthetic prompt"}
            if path == "/api/auto/submit":
                return {"id": payload["id"], "accepted": True, "turn": payload["turn"]}
            raise AssertionError((pack, path, payload))

        async with create_connected_server_and_client_session(make_server(8877, dispatch=dispatch)) as client:
            tools = await client.list_tools()
            names = {tool.name for tool in tools.tools}
            self.assertIn("game_status", names)
            self.assertIn("claim_turn", names)
            self.assertIn("submit_turn", names)
            status = await client.call_tool("game_status", {"pack_id": "relay-7"})
            self.assertIn(JOB, str(status.structuredContent))
            claim = await client.call_tool("claim_turn", {"pack_id": "relay-7", "job_id": JOB})
            self.assertIn("synthetic prompt", str(claim.structuredContent))
            receipt = await client.call_tool("submit_turn", {
                "pack_id": "relay-7", "job_id": JOB, "turn": 1, "candidate": {"blocks": []},
            })
            self.assertIn("True", str(receipt.structuredContent))


if __name__ == "__main__":
    unittest.main()
