"""Browser regressions for the packing board with isolated HTTP responses."""

import os
from pathlib import Path
import subprocess
import unittest


HERE = Path(__file__).resolve().parent
NODE_DIR = Path.home() / ".cache/codex-runtimes/codex-primary-runtime/dependencies/node"
NODE = NODE_DIR / "bin/node.exe"
PLAYWRIGHT = NODE_DIR / "node_modules/playwright"
CHROMIUM = next((Path.home() / "AppData/Local/ms-playwright").glob(
    "chromium_headless_shell-*/chrome-headless-shell-win64/chrome-headless-shell.exe"), None)


BROWSER = r"""
const assert = require('node:assert/strict');
const [modulePath, executablePath, boardPath, autoPath, batchPath, scenario, screenshots] = process.argv.slice(1);
const {chromium} = require(modulePath);
const origin = 'http://127.0.0.1:9876';
const packs = [{id:'square-18', n:18}, {id:'square-19', n:19}];
const snapshot = (n = 18, version = 1) => {
  const id = 'square-' + n, improved = version > 1;
  const side = n === 19 ? '4.88572' : improved ? '4.822876' : '4.823457';
  const area = n === 19 ? '23.8702599184' : improved ? '23.260132911376' : '23.265737430849';
  return {
    state: {pack_id:id, n, side, area, hash:`${id}-${version}`, seed_area:'24'},
    packs, polygons:[[[0,0],[0,1],[1,1],[1,0]]],
    lineage:[{author:'公开构造 · 起点',side:'4.9',area:'24.01'},{author:'测试署名',side,area}],
    prompt:'本地制图台测试题目', title_award:{name:'本地面积纪录',holder:'测试署名',reason:'布局已通过精确几何复核'},
    title_levels:[{name:'方格学徒',reason:'通过精确几何裁判'}],
    reference:{lower:'4.705',upper:'(7+√7)/2 ≈ 4.822875656',upperSource:'构造者',
      math_url:`https://squaremath.org/cases/${n}.html`,
      github_url:`https://github.com/example/packing/blob/main/${n}.txt`,
      note:'公开构造与下界提供不同依据。', source_status:improved ? 'online' : 'bundled',
      gap_ticks:n === 19 ? null : improved ? 0 : 581, checked_at:improved ? '2026-11-20T03:04:05Z' : null,
      math_checked_at:improved ? '2026-11-20T03:04:05Z' : null,
      verified_side:improved ? side : null},
  };
};

(async () => {
  const browser = await chromium.launch({executablePath, headless:true});
  try {
    const context = await browser.newContext({viewport:{width:390,height:844},serviceWorkers:'block'});
    let syncRequests = 0, release, version = 1;
    const batchPacks = [26,37,50].map(n => ({id:`square-${n}`,n,open:true}));
    const defaultPacks = batchPacks.map(pack => pack.id);
    let packingBatch = null;
    const batches = () => ({batch:packingBatch,packs:batchPacks,default_packs:defaultPacks,strategies:[]});
    let pending = new Promise(resolve => { release = resolve; });
    await context.route('**/*', async route => {
      const url = new URL(route.request().url());
      if (url.origin !== origin) return route.abort();
      if (url.pathname === '/') return route.fulfill({path:boardPath,contentType:'text/html; charset=utf-8'});
      if (url.pathname === '/autorun.js') return route.fulfill({path:autoPath,contentType:'text/javascript; charset=utf-8'});
      if (url.pathname === '/batch.js') return route.fulfill({path:batchPath,contentType:'text/javascript; charset=utf-8'});
      if (url.pathname === '/api/packing/batch') return route.fulfill({json:batches()});
      if (url.pathname === '/api/packing/batch/estimate') return route.fulfill({json:{seconds:null,token_min:null,token_max:null}});
      if (url.pathname === '/api/packing/batch/start') {
        const config = route.request().postDataJSON();
        assert.deepEqual({packs:config.packs,rounds:config.rounds,parallel:config.parallel},{packs:defaultPacks,rounds:1,parallel:3});
        packingBatch = {id:'test-batch',status:'waiting',parallel:3,rounds:1,floors:[],progress_pct:0,
          completed_rounds:0,planned_rounds:3,total_checked:0,elapsed_seconds:0,saved_ticks:0,
          rows:batchPacks.map(pack => ({n:pack.n,pack_id:pack.id,status:'waiting',turn:1,rounds:1}))};
        return route.fulfill({json:batches()});
      }
      const pack = url.searchParams.get('pack');
      if (url.pathname === '/api/game') return route.fulfill({json:pack === 'square-19' ? snapshot(19) : snapshot(18,version)});
      if (url.pathname === '/api/auto') {
        const job = {pack_id:pack,status:'idle',best_hash:`${pack}-${version}`};
        if (scenario === 'external') Object.assign(job,{id:'test-job',status:'running',mode:'mcp',
          stage:'Codex 已接手',rounds:3,completed:1,elapsed_seconds:27,turn_active_seconds:4,
          receipts:[{turn:1,accepted:false,active_seconds:20,message:'裁判已验收一轮'}]});
        return route.fulfill({json:job});
      }
      if (url.pathname === '/api/reference/sync') {
        const call = ++syncRequests;
        assert.equal(route.request().method(), 'POST');
        assert.deepEqual(route.request().postDataJSON(), {pack_id:'square-18'});
        if (scenario === 'interactions' || scenario === 'stale') await pending;
        if (call > 1) return route.fulfill({status:502,json:{error:'GitHub 连接超时，无法读取布局'}});
        version = 2;
        return route.fulfill({json:{...snapshot(18,version),sync_status:'online',message:'已核对来源并更新公开构造'}});
      }
      return route.fulfill({status:404,json:{error:'unexpected test route'}});
    });
    const page = await context.newPage();
    const pageErrors = [];
    page.on('pageerror', error => pageErrors.push(error.message));
    await page.goto(origin);
    await page.locator('#side').filter({hasText:'4.823457'}).waitFor();
    const sync = page.getByRole('button',{name:'核对来源并更新成绩'});
    if (scenario === 'display') {
      const score = await page.locator('#area').textContent();
      assert.equal(score,'23.265737430849','render all 12 digits of the certified area');
      await page.getByText('本地面积纪录',{exact:true}).waitFor();
      await page.getByText('布局已通过精确几何复核',{exact:true}).waitFor();
      await page.getByText('署名：测试署名',{exact:true}).waitFor();
      const rules = page.getByText('称号条件',{exact:true});
      await rules.click();
      await page.getByText('方格学徒',{exact:true}).waitFor();
      await rules.click();
      await page.getByText(/0\.000581/).waitFor();
      await page.getByText(/尚未在线核对/).waitFor();
      assert.equal(await page.getByRole('link',{name:/Squaremath/}).getAttribute('href'),'https://squaremath.org/cases/18.html');
      assert.equal(await page.getByRole('link',{name:/GitHub/}).getAttribute('href'),'https://github.com/example/packing/blob/main/18.txt');
      assert.equal(syncRequests,0,'opening the board must not make source sync requests');
      for (const width of [320,390,760,900,1440]) {
        await page.setViewportSize({width,height:width < 761 ? 844 : 1000});
        assert.ok(await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth));
        assert.ok(await page.locator('#area').evaluate(e => {
          const text = document.createRange();
          text.selectNodeContents(e);
          return text.getClientRects().length === 1 && text.getBoundingClientRect().right <= e.closest('.measure').getBoundingClientRect().right;
        }), `full area value fits on one line at ${width}px`);
        if (screenshots && [390,760,1440].includes(width)) await page.screenshot({path:`${screenshots}/packing-board-${width}.png`,fullPage:true});
      }
    } else if (scenario === 'batch') {
      await page.getByRole('button',{name:'创建搜索批次'}).click();
      await page.locator('#batchTakeover').waitFor();
      const floors = page.locator('#batchTower .batch-floor');
      Object.assign(packingBatch,{status:'running',progress_pct:62.5,total_checked:750,completed_rounds:1});
      await page.getByText(/搜索进度 62\.5%/).waitFor();
      assert.equal(await floors.count(),0,'completed search work alone may not raise a floor');
      packingBatch.floors.push(
        {n:26,pack_id:'square-26',turn:1,before_side:'5.6666',after_side:'5.66655',saved_ticks:50},
        {n:50,pack_id:'square-50',turn:1,before_side:'8.123956',after_side:'8.123456',saved_ticks:500,public_candidate:true},
      );
      packingBatch.saved_ticks=550;
      await floors.nth(1).waitFor();
      assert.match(await page.locator('#batchFloorDetail').textContent(),/第 2 层.*新上界候选/);
      await floors.first().click();
      assert.match(await page.locator('#batchFloorDetail').textContent(),/第 1 层.*26 块方格/);
      packingBatch.status='stopped';
      await page.reload();
      await floors.nth(1).waitFor();
      assert.equal(await page.locator('#batchFloorCount').textContent(),'2 层');
      const expectedSize = await page.evaluate(() => document.documentElement.scrollWidth <= innerWidth);
      assert.ok(expectedSize,'batch dashboard fits the mobile viewport');
    } else if (scenario === 'external') {
      const panel = page.locator('#automatic');
      await panel.getByText('实测 3 轮/分钟',{exact:true}).waitFor();
      await panel.getByText(/按已交轮次/).waitFor();
      await page.getByText('手动复制任务和提交方案',{exact:true}).click();
      const draft = page.locator('#candidate');
      await draft.fill('手动方案草稿');
      version = 2;
      await page.locator('#side').filter({hasText:'4.822876'}).waitFor();
      assert.equal(await page.locator('#area').textContent(),'23.260132911376');
      assert.equal(await draft.inputValue(),'手动方案草稿');
      assert.ok(await draft.evaluate(el => document.activeElement === el));
      assert.match(await panel.locator('[data-speed]').textContent(), /实测 3 轮\/分钟/);
      assert.match(await panel.locator('[data-remaining]').textContent(), /按已交轮次/);
      assert.equal(syncRequests,0);
    } else {
      await page.getByText('手动复制任务和提交方案',{exact:true}).click();
      const draft = page.locator('#candidate'), author = page.locator('#author');
      await author.fill('保留署名');
      await draft.fill('{"draft":"保留草稿"}');
      await sync.click();
      assert.ok(await sync.isDisabled());
      await sync.evaluate(el => el.click());
      if (scenario === 'stale') {
        await page.locator('#pack').selectOption('square-19');
        await page.getByRole('heading',{name:'19 块单位方格'}).waitFor();
        const staleResponse = page.waitForResponse(resp => resp.url().endsWith('/api/reference/sync'));
        release();
        await (await staleResponse).finished();
        await page.evaluate(() => new Promise(resolve => requestAnimationFrame(() => requestAnimationFrame(resolve))));
        assert.equal(await page.locator('#side').textContent(),'4.88572');
        await page.getByText(/暂无法计算差距/).waitFor();
        assert.equal(await draft.inputValue(),'{"draft":"保留草稿"}');
        assert.equal(await sync.isEnabled(),true);
        assert.equal(await page.getByText('已核对来源并更新公开构造').count(),0);
      } else {
        await draft.focus();
        await draft.evaluate(el => { el.setSelectionRange(4,7); });
        release();
        await page.locator('#side').filter({hasText:'4.822876'}).waitFor();
        assert.equal(await page.locator('#area').textContent(),'23.260132911376');
        assert.equal(await draft.inputValue(),'{"draft":"保留草稿"}');
        assert.equal(await author.inputValue(),'保留署名');
        assert.deepEqual(await draft.evaluate(el => [document.activeElement === el, el.selectionStart, el.selectionEnd]),[true,4,7]);
        await page.getByText(/达到公开上界/).waitFor();
        await page.getByText('已核对来源并更新公开构造').waitFor();
        await page.getByText(/上次在线核对/).waitFor();
        assert.equal(syncRequests,1,'duplicate clicks must not send a second request');
        await sync.click();
        await page.getByText('GitHub 连接超时，无法读取布局',{exact:true}).waitFor();
        assert.equal(await page.locator('#side').textContent(),'4.822876');
      }
    }
    assert.deepEqual(pageErrors,[]);
  } finally { await browser.close(); }
})().catch(error => { console.error(error); process.exitCode = 1; });
"""


@unittest.skipUnless(NODE.is_file() and PLAYWRIGHT.is_dir() and CHROMIUM, "local Playwright/Chromium required")
class PackingBoardTests(unittest.TestCase):
    def browse(self, scenario):
        result = subprocess.run(
            [str(NODE), "-e", BROWSER, str(PLAYWRIGHT), str(CHROMIUM), str(HERE / "board.html"),
             str(HERE.parent / "四境驿站" / "autorun.js"), str(HERE / "batch.js"), scenario,
             os.environ.get("PACKING_BOARD_SCREENSHOTS", "")],
            capture_output=True, encoding="utf-8", timeout=30,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_certified_measurements_and_sources_visible_without_network_sync(self):
        self.browse("display")

    def test_sync_updates_result_preserves_editing_and_reports_source_failure(self):
        self.browse("interactions")

    def test_slow_source_response_cannot_overwrite_the_selected_problem(self):
        self.browse("stale")

    def test_external_result_refreshes_a_running_board_without_hiding_its_eta(self):
        self.browse("external")

    def test_building_grows_only_from_verified_results_and_retains_floors_after_reload(self):
        self.browse("batch")


if __name__ == "__main__":
    unittest.main()
