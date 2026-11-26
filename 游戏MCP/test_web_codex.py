"""Exercise one-click subscription jobs against temporary local game servers."""

from contextlib import contextmanager
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from threading import Thread
import unittest
from unittest.mock import patch
from urllib.request import ProxyHandler, Request, build_opener


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "方格子容纳"))
from game import PackingServer
from auto_jobs import publish_origin


class SubscriptionFixture:
    def __init__(self, blocked):
        self.blocked = blocked
        self.checks = 0
        self.model_calls = 0
        self.prompts = []
        self.reference = json.loads((ROOT / "方格子容纳" / "reference_layouts.json").read_text("utf-8"))

    def infer(self, prompt, *, cancelled=None, on_ready=None):
        self.checks += 1
        if self.blocked:
            from subscription_worker import PreflightError
            raise PreflightError("充值余额可用，无法保证仅使用套餐。")
        on_ready()
        self.model_calls += 1
        self.prompts.append(prompt)
        candidate = next(json.loads(line) for line in reversed(prompt.splitlines())
                         if line.startswith('{"pack_id"'))
        candidate.update(solution=self.reference[candidate["pack_id"]]["finer"],
                         handoff="测试夹具提交公开构造；本次验证没有调用模型。")
        return {"text": json.dumps(candidate, ensure_ascii=False),
                "usage": {"output_tokens": 21}, "model": "test-fixture"}


@contextmanager
def isolated_game(blocked=False, real_preflight=False):
    connect = socket.socket.connect

    def only_loopback(sock, address):
        if address[0] not in {"127.0.0.1", "::1"}:
            raise AssertionError("the test server can connect only to loopback")
        return connect(sock, address)

    with (
        tempfile.TemporaryDirectory() as data,
        patch.object(socket.socket, "connect", only_loopback),
        patch.dict(os.environ, GAME_MCP_REGISTRY=str(Path(data) / "registry"), GAME_MCP_DATA_DIR=data),
        patch("subscription_worker._data_dir", return_value=Path(data) / "test-account"),
        patch("subscription_worker._subscription", None),
    ):
        fixture = SubscriptionFixture(blocked)
        with PackingServer(Path(data) / "packing.sqlite3", 0) as server:
            if not real_preflight:
                server.subscription_factory = lambda: fixture
            publish_origin(server, "packing")
            thread = Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                yield server.origin, fixture
            finally:
                server.shutdown()
                thread.join(5)


def game_request(origin, path="/api/auto/ledger?pack=square-18", payload=None):
    body = None if payload is None else json.dumps(payload).encode("utf-8")
    request = Request(origin + path, data=body, headers={"Origin": origin, "Content-Type": "application/json"})
    with build_opener(ProxyHandler({})).open(request, timeout=5) as response:
        return json.load(response)


BROWSER = r"""
const assert = require('node:assert/strict');
const [modulePath, executablePath, origin, status] = process.argv.slice(1);
const {chromium} = require(modulePath);
(async () => {
  const browser = await chromium.launch({executablePath, headless:true});
  try {
    const context = await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'});
    await context.route('**/*', route => new URL(route.request().url()).origin === origin
      ? route.continue() : route.abort());
    const page = await context.newPage();
    page.setDefaultTimeout(10000);
    const apiWrites = [];
    page.on('request', r => { if (r.method() === 'POST') apiWrites.push(new URL(r.url()).pathname); });
    await page.goto(origin + '/?pack=square-18' + (status === 'create-mcp' ? '&mode=mcp' : ''));
    const panel = page.locator('#automatic');
    const estimate = panel.locator('[data-estimate]');
    if (status === 'mid-round') {
      await panel.locator('button[data-stop]').waitFor();
      assert.ok(await estimate.isVisible());
      assert.equal(await panel.locator('progress').evaluate(p => p.value), 1);
      assert.match(await panel.locator('[data-progress]').innerText(), /1\s*\/\s*3/);
      assert.match(await panel.locator('[data-speed]').innerText(), /实测.*轮\/分钟/);
      assert.match(await panel.locator('[data-remaining]').innerText(), /按已交轮次/);
      assert.match(await panel.locator('[data-status]').innerText(), /已接手/);
      assert.match(await panel.locator('[data-work]').innerText(), /精确复核.*检查 4 个候选/);
      assert.deepEqual(apiWrites, []);
      return;
    }
    if (status === 'estimate') {
      await estimate.waitFor();
      assert.match(await estimate.innerText(), /预计 Token/);
      assert.match(await estimate.innerText(), /预计用时/);
      assert.ok(await estimate.locator('[data-estimate-basis]').innerText());
      const mode = panel.locator('[name=mode]');
      const rounds = panel.locator('[name=rounds]');
      const tokens = estimate.locator('[data-estimate-token]');
      const time = estimate.locator('[data-estimate-time]');
      const codexTokens = await tokens.innerText();
      await mode.selectOption('local');
      assert.notEqual(await tokens.innerText(), codexTokens);
      assert.equal(await tokens.innerText(), '0 token');
      await rounds.selectOption('5');
      assert.equal(await tokens.innerText(), '0 token');
      assert.ok(await time.innerText());
      await page.locator('#pack').selectOption('square-19');
      await page.getByRole('heading', {name:'19 块单位方格'}).waitFor();
      assert.equal(await mode.inputValue(), 'codex');
      assert.equal(await rounds.inputValue(), '1');
      assert.ok(await time.innerText());
      assert.ok(await panel.evaluate(p => p.scrollWidth <= p.clientWidth + 1));
      assert.deepEqual(apiWrites, []);
      return;
    }
    if (status === 'waiting' || status === 'create-mcp') {
      if (status === 'create-mcp') {
        await page.waitForFunction(() => !document.querySelector('#automatic button[type=submit]')?.disabled);
        assert.equal(await panel.locator('[name=mode]').inputValue(), 'mcp');
        await panel.locator('button[type=submit]').click();
      }
      await panel.locator('button[data-stop]').waitFor();
      assert.ok(await estimate.isVisible());
      const progress = panel.getByRole('progressbar', {name:'整局轮次进度'});
      assert.equal(await progress.getAttribute('value'), '0');
      assert.equal(await progress.getAttribute('max'), status === 'waiting' ? '5' : '1');
      assert.match(await panel.locator('[data-speed]').innerText(), /待测速/);
      assert.match(await panel.locator('[data-remaining]').innerText(), /预计剩余/);
      assert.equal(await panel.locator('[name=mode]').inputValue(), 'mcp');
      assert.match(await panel.locator('[data-status]').innerText(), /等待.*领取/);
      const job = await page.evaluate(() => fetch('/api/auto?pack=square-18').then(r => r.json()));
      const link = panel.getByRole('link', {name:'在 Codex 中接手本局'});
      const target = new URL(await link.getAttribute('href'));
      assert.equal(target.protocol, 'codex:');
      assert.equal(target.host + target.pathname, 'threads/new');
      await panel.getByText('在已有对话中接手').click();
      const prompt = await panel.getByRole('textbox', {name:'本局接手指令'}).inputValue();
      assert.equal(target.searchParams.get('prompt'), prompt);
      assert.ok(prompt.includes(job.pack_id) && prompt.includes(job.id));
      assert.ok(await panel.locator('[data-subscription]').isHidden());
      assert.ok(await panel.evaluate(p => p.scrollWidth <= p.clientWidth + 1));
      assert.deepEqual(apiWrites, status === 'waiting' ? [] : ['/api/auto/start']);
      return;
    }
    await page.waitForFunction(() => !document.querySelector('#automatic button[type=submit]')?.disabled);
    assert.equal(await panel.locator('[name=mode]').inputValue(), 'codex');
    const sideBefore = await page.locator('#side').innerText();
    await panel.locator('[name=rounds]').selectOption('3');
    await panel.locator('button[type=submit]').click();
    try {
      await page.locator('#automatic[data-state="' + status + '"]').waitFor();
    } catch (error) {
      console.error(await panel.innerText());
      throw error;
    }
    const rows = await panel.locator('[data-receipts] li').allTextContents();
    if (status === 'blocked') {
      assert.match(await panel.locator('[data-note]').innerText(), /充值|套餐/);
      assert.match(await panel.locator('[data-progress]').innerText(), /0\s*\/\s*3/);
      assert.equal(rows.length, 0);
    } else {
      assert.match(await panel.locator('[data-progress]').innerText(), /3\s*\/\s*3/);
      assert.equal(rows.length, 3);
      assert.match(rows[0], /已存档/);
      assert.equal(await panel.getByRole('progressbar', {name:'整局轮次进度'}).getAttribute('value'), '3');
      await page.waitForFunction(oldSide => Number(document.querySelector('#side').textContent) < Number(oldSide), sideBefore);
    }
    assert.deepEqual(apiWrites, ['/api/auto/start']);
    await page.reload();
    await page.locator('#automatic[data-state="' + status + '"]').waitFor();
    assert.deepEqual(await panel.locator('[data-receipts] li').allTextContents(), rows);
    assert.deepEqual(apiWrites, ['/api/auto/start']);
    assert.ok(await panel.evaluate(p => p.scrollWidth <= p.clientWidth + 1));
    console.log(await panel.locator('[data-progress]').innerText());
  } finally { await browser.close(); }
})().catch(e => { console.error(e); process.exitCode = 1; });
"""


NODE_DIR = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node"
NODE = NODE_DIR / "bin/node.exe"
PLAYWRIGHT = NODE_DIR / "node_modules/playwright"
CHROMIUM = next((Path.home() / "AppData/Local/ms-playwright").glob(
    "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe"), None)


@unittest.skipUnless(NODE.is_file() and PLAYWRIGHT.is_dir() and CHROMIUM, "local Playwright/Chromium required")
class OneClickTests(unittest.TestCase):
    def click(self, origin, status):
        completed = subprocess.run([str(NODE), "-e", BROWSER, str(PLAYWRIGHT), str(CHROMIUM), origin, status],
                                   capture_output=True, encoding="utf-8", timeout=40,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        self.assertEqual(completed.returncode, 0, completed.stderr)

    def test_preflight_estimate_tracks_selected_options_without_starting_a_job(self):
        with isolated_game() as (origin, driver):
            self.click(origin, "estimate")
            self.assertEqual((driver.checks, driver.model_calls), (0, 0))

    def test_active_turn_shows_eta_and_speed_after_a_receipt(self):
        with isolated_game() as (origin, _):
            pack = "square-18"
            job = game_request(origin, "/api/auto/start", {"pack_id": pack, "mode": "mcp", "rounds": 3})
            args = {"pack_id": pack, "id": job["id"]}
            claim = game_request(origin, "/api/auto/claim", args)
            candidate = json.loads(claim["prompt"].splitlines()[-1])
            game_request(origin, "/api/auto/submit", {**args, "turn": 1, "candidate": candidate})
            game_request(origin, "/api/auto/claim", args)
            game_request(origin, "/api/auto/progress", {**args, "turn": 2, "checked": 4, "stage": "精确复核"})
            self.click(origin, "mid-round")

    def test_click_reaches_judge_three_times_without_chat(self):
        with isolated_game() as (origin, driver):
            self.click(origin, "completed")
            job = game_request(origin)
            self.assertEqual((job["mode"], job["status"], job["completed"]), ("codex", "completed", 3))
            self.assertEqual([row["turn"] for row in job["records"]], [1, 2, 3])
            self.assertTrue(job["records"][0]["accepted"])
            self.assertEqual(driver.model_calls, 3)
            self.assertIn(job["records"][0]["message"], driver.prompts[1])
            self.assertEqual([row["usage"]["output_tokens"] for row in job["provider_usage"]], [21, 21, 21])

    def test_credit_guard_shows_stop_reason_without_inference_or_retry(self):
        with isolated_game(blocked=True) as (origin, driver):
            self.click(origin, "blocked")
            job = game_request(origin)
            self.assertEqual((job["status"], job["records"]), ("blocked", []))
            self.assertIn("充值", job["message"])
            self.assertIsNone(job["provider_usage"])
            self.assertEqual((driver.checks, driver.model_calls), (1, 0))

    def test_opening_existing_mcp_job_does_not_start_a_worker(self):
        with isolated_game() as (origin, driver):
            pending = game_request(origin, "/api/auto/start",
                                   {"pack_id": "square-18", "mode": "mcp", "rounds": 5})
            self.click(origin, "waiting")
            job = game_request(origin)
            self.assertEqual((job["id"], job["mode"], job["completed"]), (pending["id"], "mcp", 0))
            self.assertEqual((driver.checks, driver.model_calls), (0, 0))

    def test_desktop_link_starts_an_mcp_job_for_the_selected_pack(self):
        with isolated_game() as (origin, driver):
            self.click(origin, "create-mcp")
            self.assertEqual(game_request(origin)["mode"], "mcp")
            self.assertEqual(driver.model_calls, 0)

    @unittest.skipUnless(sys.platform == "win32", "real empty credential storage uses Windows DPAPI")
    def test_new_install_explains_missing_subscription_before_inference(self):
        with isolated_game(real_preflight=True) as (origin, _):
            self.click(origin, "blocked")
            job = game_request(origin)
            self.assertEqual(job["records"], [])
            self.assertIn("授权", job["message"])
            self.assertIn("周上限", job["message"])


if __name__ == "__main__":
    unittest.main()
