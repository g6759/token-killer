"""Real browser, MCP, usage ledger and independent CPU workers for one batch."""

import asyncio
import json
import os
import subprocess
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path
import time
import unittest
from unittest.mock import patch
from uuid import uuid4

from test_web_codex import CHROMIUM, NODE, PLAYWRIGHT, isolated_game
from server import HERE
from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client
from packing_usage import CodexUsage
from packing_batches import PackingBatches


BROWSER_SCRIPT = r"""
const assert = require('node:assert/strict');
const [pw, browserPath, origin] = process.argv.slice(1);
(async () => {
  const browser = await require(pw).chromium.launch({executablePath: browserPath, headless: true});
  try {
    const page = await browser.newPage({viewport: {width: 390, height: 900}});
    page.setDefaultTimeout(20000);
    const result = page.waitForResponse(r => r.url().endsWith('/batch/start') && r.request().method() === 'POST');
    await page.goto(origin + '/?pack=square-50');
    await page.locator('#batchStart').click();
    const {batch} = await (await result).json();
    assert.deepEqual(batch.rows.map(r => r.n), [26, 37, 50]);
    assert.equal(batch.parallel, 3);
    await page.waitForFunction(() => document.querySelector('#batchPrompt').value.includes('packing_batch_status'));
    assert.equal(await page.locator('#batchTower li').count(), 0);
    console.log(JSON.stringify({batch: batch.id}));

    const chosen = page.getByRole('button', {name: '停止 50 块方格', exact: true});
    await page.waitForFunction(() => document.querySelectorAll('#batchRows article[data-status="running"]').length === 3);
    await page.waitForFunction(() => document.querySelector('#batchUsage').textContent.includes('111'));
    await page.waitForFunction(() => {
      const cells = document.querySelectorAll('article[data-batch-row="square-50"] .batch-stats dd');
      return cells.length >= 3 && Number(cells[2].textContent.replace(/[^0-9]/g, '')) > 0;
    });
    const stopResponse = page.waitForResponse(r => r.url().endsWith('/batch/stop') && r.request().method() === 'POST');
    await chosen.click();
    const stopped = (await (await stopResponse).json()).batch;
    assert.equal(stopped.rows.find(r => r.n === 50).status, 'stopped');
    assert.equal(stopped.usage.total_tokens, 111);
    await page.reload();
    await page.waitForFunction(() => document.querySelectorAll('#batchRows article[data-status="done"]').length === 2);
    await page.waitForFunction(() => document.querySelector('#batchUsage').textContent.includes('111'));
    assert.equal(await page.evaluate(() => document.documentElement.scrollWidth), 390);
    const finished = await page.evaluate(async () => (await (await fetch('/api/packing/batch')).json()).batch);
    assert.equal(await page.locator('#batchTower li').count(), finished.floors.length);
    for (const floor of finished.floors) assert.ok(Number(floor.after_side) < Number(floor.before_side));
    console.log(JSON.stringify({finished}));
  } finally {
    await browser.close();
  }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipUnless(NODE.is_file() and PLAYWRIGHT.is_dir() and CHROMIUM, "local Playwright/Chromium required")
class PackingBatchWebTests(unittest.TestCase):
    def test_browser_to_single_mcp_session_and_three_local_processes(self):
        timer = lambda seconds: datetime.fromtimestamp(seconds, timezone.utc).isoformat()
        with tempfile.TemporaryDirectory() as directory:
            temp = Path(directory)
            thread = str(uuid4())
            log_dir = temp / datetime.now(timezone.utc).strftime("%Y/%m/%d")
            log_dir.mkdir(parents=True)
            log = log_dir / f"rollout-{datetime.now(timezone.utc):%Y-%m-%dT%H-%M-%S}-{thread}.jsonl"

            def append(value):
                with log.open("a", encoding="utf-8") as stream:
                    stream.write(json.dumps(value) + "\n")

            def record(second, input_count, cached, output):
                totals = {"input_tokens": input_count, "cached_input_tokens": cached,
                          "output_tokens": output, "total_tokens": input_count + output}
                append({"timestamp": timer(second), "type": "event_msg", "payload":
                        {"type": "token_count", "info": {"total_token_usage": totals}}})

            append({"type": "session_meta", "timestamp": timer(time.time() - 2), "payload": {"id": thread}})
            record(time.time() - 1, 100, 10, 10)
            with (patch.object(PackingBatches, "_usage", autospec=True) as meter,
                  patch("packing_batches.WORK_LIMIT", 18_000), isolated_game() as (origin, _)):
                reader = CodexUsage(temp)
                meter.side_effect = lambda self, batch: (reader.sample(batch["thread_id"], batch["started"], batch.get("ended"))
                                                         if batch.get("thread_id") else None)

                async def play():
                    params = StdioServerParameters(command=sys.executable,
                                                   args=["-B", "-X", "utf8", str(HERE / "server.py")],
                                                   env={"GAME_MCP_REGISTRY": os.environ["GAME_MCP_REGISTRY"]})
                    async with stdio_client(params) as (read, write), ClientSession(read, write) as session:
                        await session.initialize()
                        browser = await asyncio.create_subprocess_exec(
                            str(NODE), "-e", BROWSER_SCRIPT, str(PLAYWRIGHT), str(CHROMIUM), origin,
                            stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                        )
                        try:
                            line = await browser.stdout.readline()
                            if not line:
                                self.fail((await browser.stderr.read()).decode("utf-8", errors="replace"))
                            batch_id = json.loads(line)["batch"]
                            # One model call is shared by all three plans. Explicitly
                            # replay a cumulative counter notification to catch double counting.
                            record(time.time(), 197, 30, 24)
                            record(time.time(), 197, 30, 24)
                            quota = {"ordinaryUsageAllowed": True,
                                     "primary": {"usedPercent": 2, "windowDurationMins": 10080}}
                            plans = [{"pack_id": f"square-{n}", "turn": 1, "strategy": s}
                                     for n, s in ((26, "compact"), (37, "boundary"), (50, "mixed"))]
                            response = await session.call_tool("plan_packing_batch", {
                                "batch_id": batch_id, "thread_id": thread, "quota": quota, "plans": plans})
                            self.assertFalse(response.isError, response.content)
                            self.assertEqual(response.structuredContent["batch"]["usage"]["total_tokens"], 111)
                            out, err = await browser.communicate()
                            self.assertEqual(browser.returncode, 0, err.decode("utf-8", errors="replace"))
                            view = json.loads(out)["finished"]
                            a, b, c = view["rows"]
                            self.assertEqual([r["status"] for r in view["rows"]], ["done", "done", "stopped"])
                            self.assertGreater(c["total_checked"], 0)
                            first, second = a["receipts"][0], b["receipts"][0]
                            self.assertNotEqual(first["pid"], second["pid"])
                            self.assertLess(max(first["started"], second["started"]), min(first["ended"], second["ended"]))
                            self.assertEqual(view["usage"]["total_tokens"], 111)
                            self.assertEqual(view["completed_rounds"], 2)
                            self.assertLess(view["progress_pct"], 100)
                        finally:
                            if browser.returncode is None:
                                browser.terminate()
                                await browser.wait()

                asyncio.run(asyncio.wait_for(play(), timeout=60))


if __name__ == "__main__":
    unittest.main()
