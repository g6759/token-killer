"""Verify the compact entry point using a temporary game store and real MCP."""

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from zipfile import ZipFile

from test_web_codex import CHROMIUM, NODE, PLAYWRIGHT, isolated_game
from server import HERE
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


SCRIPT = r"""
const assert = require('node:assert/strict');
const [pw, exe, origin, out] = process.argv.slice(1);
(async () => {
  const browser = await require(pw).chromium.launch({executablePath: exe, headless: true});
  try {
    const page = await browser.newPage({viewport:{width:390,height:850}});
    page.setDefaultTimeout(16000);
    await page.goto(origin + '/lite');
    await page.locator('#bundle').waitFor({state:'visible'});
    assert.equal(await page.locator('#rounds').inputValue(), '100');
    assert.equal(await page.locator('#issue').isVisible(), false);
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth),390);
    const check = page.waitForResponse(r => r.url().endsWith('/lite/check'));
    await page.locator('#drawing').focus();
    await page.keyboard.press('Enter');
    await page.keyboard.press('Shift+ArrowDown');
    await page.locator('#check').click();
    const data = await (await check).json();
    assert.ok(data.side || data.error);
    await page.locator('#reset').click();
    const incoming = page.waitForEvent('download');
    await page.locator('#bundle').click();
    await (await incoming).saveAs(out);
    const created = page.waitForResponse(r => r.url().endsWith('/lite/start'));
    await page.locator('#rounds').selectOption('20');
    await page.locator('#start').click();
    const {job} = await (await created).json();
    assert.equal(job.rounds,20);
    assert.equal(job.profile,'packing-lite');
    await page.locator('#prompt').waitFor();
    assert.match(await page.locator('#prompt').inputValue(),new RegExp(job.id));
    console.log(job.id);
    await page.getByRole('button',{name:'看试摆 · 碰撞',exact:true}).click();
    assert.match(await page.locator('#trial-status').innerText(),/重叠/);
    await page.waitForFunction(() => /存档|已保存/.test(document.getElementById('receipt').textContent));
    await page.locator('#rounds').selectOption('100');
    const extending = page.waitForResponse(r => r.url().endsWith('/lite/extend'));
    await page.locator('#start').click();
    const extended = (await (await extending).json()).job;
    assert.equal(extended.id,job.id);
    assert.equal(extended.completed,1);
    assert.equal(extended.rounds,100);
    await page.reload();
    await page.waitForFunction(() => document.getElementById('progress').textContent.includes('1/100'));
    const stopping = page.waitForResponse(r => r.url().endsWith('/lite/stop'));
    await page.locator('#stop').click();
    assert.equal((await (await stopping).json()).job.status,'stopped');
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode=1; });
"""


@unittest.skipUnless(NODE.is_file() and PLAYWRIGHT.is_dir() and CHROMIUM, "local Playwright/Chromium required")
class PackingLiteWebTests(unittest.TestCase):
    def test_lite_manual_play_export_and_high_intensity_mcp(self):
        with tempfile.TemporaryDirectory() as directory, isolated_game() as (origin, _):
            archive = Path(directory) / "witness.zip"

            async def play():
                params = StdioServerParameters(command=sys.executable,
                    args=["-B", "-X", "utf8", str(HERE/"server.py")],
                    env={"GAME_MCP_REGISTRY": os.environ["GAME_MCP_REGISTRY"]})
                async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                    await session.initialize()
                    browser = await asyncio.create_subprocess_exec(str(NODE), "-e", SCRIPT, str(PLAYWRIGHT),
                        str(CHROMIUM), origin, str(archive), stdout=asyncio.subprocess.PIPE,
                        stderr=asyncio.subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
                    try:
                        job_id = (await browser.stdout.readline()).decode().strip()
                        if not job_id:
                            self.fail((await browser.stderr.read()).decode("utf-8", errors="replace"))
                        basic = {"pack_id": "square-26", "job_id": job_id}
                        args = {**basic, "thread_id": "00000000-0000-4000-8000-000000000001"}
                        first = await session.call_tool("claim_turn", args)
                        self.assertFalse(first.isError, first.content)
                        turn = first.structuredContent
                        self.assertIn("整体收缩、边界重排、局部转角、替换接触结构", turn["prompt"])
                        candidate = json.loads(turn["prompt"].splitlines()[-1])
                        bad = [p[:] for p in candidate["solution"]["poses"]]
                        bad[1] = bad[0][:]
                        preview = await session.call_tool("report_packing_trial", {
                            "job_id":job_id, "turn":1, "checked":1, "stage":"固定接触图",
                            "summary":"比较两块方格的可移动范围。", "solution":{"poses":bad}})
                        self.assertFalse(preview.isError, preview.content)
                        source = HERE.parent/"方格子容纳/reference_layouts.json"
                        candidate.update(solution=json.loads(source.read_text("utf-8"))["square-26"]["finer"],
                                         handoff="验收夹具复算已公开的摆法")
                        receipt = await session.call_tool("submit_turn", {**basic, "candidate": candidate, "turn": 1})
                        self.assertFalse(receipt.isError, receipt.content)
                        self.assertEqual(receipt.structuredContent["turn"], 2)
                        _, err = await browser.communicate()
                        self.assertEqual(browser.returncode, 0, err.decode("utf-8", errors="replace"))
                    finally:
                        if browser.returncode is None:
                            browser.terminate()
                            await browser.wait()

            asyncio.run(asyncio.wait_for(play(), timeout=50))
            with ZipFile(archive) as source:
                witness = next(f for f in source.namelist() if f.endswith(".yaml"))
                self.assertIn(b"packing.squares:Witness/v2", source.read(witness))


if __name__ == "__main__":
    unittest.main()
