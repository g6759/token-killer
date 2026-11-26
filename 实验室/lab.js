"use strict";

(() => {
  const en = new Map([
    ["研究游戏", "Research games"], ["Golomb 尺", "Golomb ruler"], ["超维递归模拟器", "Hyperdimensional Recursive Simulator"],
    ["在三元向量中选点，使任意三点不共线。", "Choose ternary vectors with no three points on a line."],
    ["移动刻度，用更短的尺量出不同距离。", "Move the marks to fit distinct distances on a shorter ruler."],
    ["调整 Lorenz 初值，观察两条轨迹何时分开。", "Change a Lorenz initial condition and watch two trajectories diverge."],
    ["解题实验室 · 本地对局与模型吞吐对照", "Puzzle lab · local games and model throughput comparisons"],
    ["正在连接本机", "Connecting locally"], ["本机已连接", "Connected locally"],
    ["本机连接中断", "Local connection lost"], ["项目成绩 ↗", "Project scores ↗"],
    ["对局", "Games"], ["方格子容纳", "Square packing"],
    ["压缩 26 个单位正方形的外框。", "Minimize the enclosing square for 26 unit squares."],
    ["本地最佳边长", "Local best side"], ["进入方格冲榜", "Open Square Challenge"],
    ["四境驿站", "Four Realms Relay"],
    ["改进 26 份名单，覆盖 49 站之间的站点对。", "Improve 26 lists covering pairs among 49 stations."],
    ["已覆盖站点对", "Covered station pairs"], ["进入四境驿站", "Open Four Realms Relay"],
    ["模型吞吐对照", "Model throughput comparison"], ["等待实跑数据", "Waiting for live run data"],
    ["混合双路冲榜", "Mixed two-lane challenge"], ["连续冲榜", "Continuous challenge"],
    ["开始三组对照", "Start three-way comparison"],
    ["双路与四路", "Two vs four lanes"], ["方格与接力 · 各 40 路、各 10 分钟", "Square vs relay · 40 lanes each, 10 min each"],
    ["四路与八路", "Four vs eight lanes"], ["八路与十六路", "Eight vs sixteen lanes"],
    ["单题方格与多题组合", "One square puzzle vs portfolio"],
    ["多题十六路与二十四路", "Portfolio: 16 vs 24 lanes"],
    ["多题十六路与全接力", "Portfolio vs all-relay, 16 lanes"],
    ["全接力二十四路与十六路", "All-relay: 24 vs 16 lanes"],
    ["全接力二十路与十六路", "All-relay: 20 vs 16 lanes"],
    ["全接力二十二路与二十路", "All-relay: 22 vs 20 lanes"],
    ["全接力二十三路与二十二路", "All-relay: 23 vs 22 lanes"],
    ["二十二路单目标与双目标", "22 lanes: one vs two targets"],
    ["开始并发对照", "Start concurrency comparison"], ["继续实跑", "Resume live run"],
    ["离线演练", "Offline rehearsal"], ["停止", "Stop"],
    ["裁判回执、模型输出与任务日志保留原文。", "Judge receipts, model output, and task logs remain in their original language."],
    ["参加玩法挑战", "Join the game challenge"],
    ["速度挑战按一个完整十分钟窗口比较有效 token/分钟。总量挑战累加互不重叠的完整窗口，记录总有效 token、实际用时和通过裁判的不同候选。", "For speed, compare effective tokens per minute over one full ten-minute window. For total volume, add complete non-overlapping windows and report effective tokens, elapsed time and distinct candidates accepted by the judge."],
    ["复算和投稿说明", "Verification and submission guide"], ["向 GitHub 提交玩法或成绩", "Submit a method or result on GitHub"],
    ["最近通过几何裁判的试摆", "Recent trials accepted by the geometry judge"],
    ["对照结果，可横向滚动", "Comparison results; scroll horizontally"],
    ["混合并发度结果，可横向滚动", "Concurrency results; scroll horizontally"],
    ["选择并发对照路数", "Choose lane comparison"], ["跑法", "Run mode"],
    ["有效 token / 分钟", "Effective tokens / min"], ["有效 token 总量", "Total effective tokens"], ["原始总 token", "Raw total tokens"],
    ["输出 / 分钟", "Output / min"], ["缓存输入", "Cached input"],
    ["去重有效候选", "Distinct valid candidates"], ["模型回合", "Model rounds"],
    ["26 块单局", "26 squares · one game"], ["26 块双局", "26 squares · two games"],
    ["方格＋接力双路", "Square + relay · two lanes"], ["方格＋接力四路", "Square + relay · four lanes"],
    ["方格＋接力八路", "Square + relay · eight lanes"],
    ["方格＋接力十六路", "Square + relay · sixteen lanes"],
    ["多题十六路", "Portfolio · sixteen lanes"], ["多题二十四路", "Portfolio · twenty-four lanes"],
    ["全接力十六路", "All relay · sixteen lanes"], ["全接力二十路", "All relay · twenty lanes"],
    ["全接力二十二路", "All relay · twenty-two lanes"],
    ["全接力二十三路", "All relay · twenty-three lanes"],
    ["全接力二十四路", "All relay · twenty-four lanes"],
    ["二十二路双目标接力", "Relay · 22 lanes, two targets"],
    ["方格分支搜索 · 40 路", "Square frontier search · 40 lanes"],
    ["接力分支搜索 · 40 路", "Relay frontier search · 40 lanes"],
    ["并发对照结果", "Concurrency comparison results"],
    ["连接 ChatGPT 订阅", "Connect ChatGPT plan"], ["ChatGPT 订阅已连接", "ChatGPT plan connected"],
    ["正在检查本机连接", "Checking local connection"], ["连接账号", "Connect account"],
    ["在官方页面授权 ↗", "Authorize on official page ↗"],
    ["官方用量设置 ↗", "Official usage settings ↗"],
    ["已保存的应用周上限", "Saved application weekly cap"],
    ["应用周上限，百分比", "Application weekly cap, percent"], ["确认", "Confirm"],
    ["最近一次游戏周限额记录", "Latest game weekly usage record"],
    ["尚无游戏周限额记录；下次获准游戏运行后会显示开始与结束读数。", "No game weekly usage record yet. Start and end readings will appear after the next permitted run."],
    ["未知", "Unknown"], ["未取得读数", "No reading"],
    ["游戏窗口", "Game window"], ["账号共享周用量 · 开始", "Account weekly use · start"],
    ["账号共享周用量 · 结束", "Account weekly use · end"],
    ["同一周窗口变化", "Change within the same weekly window"],
    ["周窗口变化", "Weekly window change"],
    ["开始与结束窗口不同或读数缺失，变化不可比", "Start and end windows differ or readings are missing; the change is not comparable"],
    ["本次游戏回包", "Game response total"], ["有效游戏 token 未提供", "Effective game tokens unavailable"],
    ["应用周上限 · 本地声明", "Application weekly cap · local declaration"],
    ["未记录本地声明", "No local declaration recorded"],
    ["应用实际已用", "Actual application use"],
    ["未知；目前没有应用实际用量读数", "Unknown; no actual application usage reading is available"],
    ["本机服务未返回状态", "Local service did not return a state"],
    ["操作失败", "Operation failed"],
    ["正在停止，已收到的回执会保留", "Stopping; received receipts will be kept"],
    ["正在运行本组对局", "Running this comparison"],
    ["本次对照中断", "This comparison was interrupted"],
    ["模型实跑已归档", "Live model run archived"],
    ["已有裁判演练结果，等待模型实跑", "Judge rehearsal saved; waiting for a live model run"],
    ["应用周上限已确认。实跑每组 10 分钟。", "Application weekly cap confirmed. Each live run lasts ten minutes."],
    ["请完成官方页面授权", "Complete authorization on the official page"],
    ["请在官方用量设置保存低于 100% 的周上限，再填入确认。", "Save a weekly cap below 100% in official usage settings, then confirm it here."],
    ["登录后可授权本地应用使用套餐。", "Sign in to authorize this local application to use your plan."],
  ]);
  const patterns = [
    [/^(\d+) 块方格$/, "$1 squares"],
    [/^ · (\d+) \/ (\d+) 轮(.*)$/, " · $1 / $2 rounds$3"],
    [/^(.+)的实测吞吐最高$/, "$1 had the highest measured throughput"],
    [/^已跑满 (\d+) \/ 2 组(.*)$/, "$1 / 2 groups completed$2"],
    [/^最近一次(.+) · 已跑满 10 分钟(.*)$/, "Latest $1 · completed ten minutes$2"],
    [/^最近一次(.+) · 提前结束(.*)$/, "Latest $1 · ended early$2"],
    [/^成果楼 · ([\d,]+) 层$/, "Result tower · $1 floors"],
    [/^(.+)连续冲榜 · 第 (\d+) 组(.*)$/, "$1 continuous challenge · group $2$3"],
    [/^三组对照中，(.+)最快$/, "$1 was fastest of three modes"],
    [/^冲榜每组预计 ([\d,.]+) 分钟(.*)$/, "Estimated $1 min per challenge group$2"],
    [/^(.+)冲榜$/, "$1 challenge"],
    [/^(.+)去重候选 · (.+)$/, "$1 distinct candidates · $2"],
    [/^([\d,.]+) 个百分点$/, "$1 percentage points"],
    [/^([\d,.]+) 有效游戏 token$/, "$1 effective game tokens"],
  ];
  const parts = new Map([
    ["最近一次", "Latest "], ["已跑满 10 分钟", "completed ten minutes"],
    ["提前结束", "ended early"], ["已跑 ", "ran for "], ["有效 token/分钟", "effective tokens/min"],
    ["轮 · 有效 ", "rounds · effective "], ["新纪录 ", "new records "],
    [" 项", " entries"], [" 轮", " rounds"], [" 秒", " sec"],
    ["已完成指定组数", "completed requested groups"], ["已停止", "stopped"],
    ["已中断", "interrupted"], ["已恢复历史记录", "restored previous record"],
    ["已结束", "ended"], ["已完成 ", "completed "], [" 组", " groups"],
    ["累计 ", "total "], [" 个回包", " responses"],
    [" 分钟", " min"], ["约 ", "about "],
  ]);
  const originalText = new WeakMap(), renderedText = new WeakMap();
  const originalAttrs = new WeakMap(), renderedAttrs = new WeakMap();
  let language;
  const requested = new URLSearchParams(location.search).get("lang");
  let saved = null;
  try { saved = localStorage.getItem("research-games-language"); } catch {}
  language = ["en", "zh-CN"].includes(requested) ? requested
    : ["en", "zh-CN"].includes(saved) ? saved
    : navigator.language?.toLowerCase().startsWith("en") ? "en" : "zh-CN";
  if (["en", "zh-CN"].includes(requested)) {
    try { localStorage.setItem("research-games-language", requested); } catch {}
  }
  function translate(value) {
    const text = value.trim();
    let result = en.get(text);
    if (!result) for (const [pattern, replacement] of patterns) {
      if (pattern.test(text)) { result = text.replace(pattern, replacement); break; }
    }
    return result ? value.replace(text, result) : value;
  }
  function text(node) {
    if (node.parentElement?.closest("script,style,textarea,[data-native],.language-switch")) return;
    if (!originalText.has(node) || node.nodeValue !== renderedText.get(node)) originalText.set(node, node.nodeValue);
    let value = language === "en" ? translate(originalText.get(node)) : originalText.get(node);
    if (language === "en" && value === originalText.get(node)
        && node.parentElement?.closest('#mixed-result,#continuous-progress,#featured-estimate,#packing-job,#relay-job,#portfolio-candidates')) {
      for (const [from, to] of parts) value = value.replaceAll(from, to);
      for (const [from, to] of en) value = value.replaceAll(from, to);
    }
    renderedText.set(node, value);
    if (node.nodeValue !== value) node.nodeValue = value;
  }
  function element(node) {
    if (node.closest("script,style,textarea,[data-native],.language-switch")) return;
    const originals = originalAttrs.get(node) || {}, shown = renderedAttrs.get(node) || {};
    for (const name of ["aria-label", "placeholder", "title", "data-label"]) {
      if (!node.hasAttribute(name)) continue;
      const current = node.getAttribute(name);
      if (!(name in originals) || current !== shown[name]) originals[name] = current;
      const value = language === "en" ? translate(originals[name]) : originals[name];
      shown[name] = value;
      if (current !== value) node.setAttribute(name, value);
    }
    originalAttrs.set(node, originals); renderedAttrs.set(node, shown);
  }
  function walk(root) {
    if (root.nodeType === Node.TEXT_NODE) { text(root); return; }
    if (root.nodeType !== Node.ELEMENT_NODE) return;
    element(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) walker.currentNode.nodeType === Node.TEXT_NODE ? text(walker.currentNode) : element(walker.currentNode);
  }
  function apply(next) {
    language = next;
    document.documentElement.lang = next;
    document.title = next === "en" ? "token-killer · Puzzle Lab" : "token-killer · 解题实验室";
    for (const code of ["zh", "en"]) document.getElementById("lang-" + code).setAttribute("aria-pressed", String(code === (next === "en" ? "en" : "zh")));
    for (const id of ["packing-link", "relay-link", "cap-link", "ruler-link", "lorenz-link"]) {
      const link = document.getElementById(id);
      if (!link?.href) continue;
      const target = new URL(link.href);
      target.searchParams.set("lang", next);
      link.href = target.href;
    }
    walk(document.body);
  }
  for (const code of ["zh", "en"]) document.getElementById("lang-" + code).addEventListener("click", () => {
    const value = code === "en" ? "en" : "zh-CN";
    try { localStorage.setItem("research-games-language", value); } catch {}
    const url = new URL(location.href);
    url.searchParams.set("lang", value);
    history.replaceState(history.state, "", url.href);
    apply(value);
  });
  new MutationObserver(records => {
    for (const record of records) {
      if (record.type === "characterData") text(record.target);
      else if (record.type === "attributes") element(record.target);
      else for (const node of record.addedNodes) walk(node);
    }
  }).observe(document.body, {subtree:true,childList:true,characterData:true,attributes:true,
    attributeFilter:["aria-label","placeholder","title","data-label"]});
  apply(language);
  window.GameLanguage = {get current() { return language; }};
})();

const count = new Intl.NumberFormat("zh-CN");
const status = document.getElementById("benchmark-status");
const replay = document.getElementById("replay");
const live = document.getElementById("live");
const mixedStart = document.getElementById("mixed-start");
const continuousStart = document.getElementById("continuous-start");
const continuousProgress = document.getElementById("continuous-progress");
const featuredEstimate = document.getElementById("featured-estimate");
const mixedResult = document.getElementById("mixed-result");
const concurrencyStart = document.getElementById("concurrency-start");
const concurrencyChoice = document.getElementById("concurrency-choice");
const concurrencyResults = document.getElementById("concurrency-results");
const concurrencyStatus = document.getElementById("concurrency-status");
const portfolioCandidates = document.getElementById("portfolio-candidates");
const packingCases = document.getElementById("packing-cases");
const benchmarkRows = document.getElementById("benchmark-rows");
const concurrencyRows = document.getElementById("concurrency-rows");
const resume = document.getElementById("resume");
const stop = document.getElementById("stop");
const progress = document.getElementById("progress");
const trialLayouts = document.getElementById("trial-layouts");
const tower = document.getElementById("tower");
const towerLabel = document.getElementById("tower-label");
const towerFloors = document.getElementById("tower-floors");
const connection = document.getElementById("connection");
const connect = document.getElementById("connect");
const planStatus = document.getElementById("plan-status");
const planTitle = document.getElementById("plan-title");
const authorize = document.getElementById("authorize");
const limit = document.getElementById("limit");
const weeklyMeter = document.createElement("div");
weeklyMeter.className = "weekly-meter";
weeklyMeter.setAttribute("aria-live", "polite");
planStatus.insertAdjacentElement("afterend", weeklyMeter);
let isRequesting = false;
let updating = false;
let shownTrials = "";
let featuredMixed = "mixed";

const names = {single: "26 块单局", "same-game": "26 块双局", mixed: "方格＋接力双路",
  "mixed-four": "方格＋接力四路", "mixed-eight": "方格＋接力八路", "mixed-sixteen": "方格＋接力十六路",
  "mixed-portfolio": "多题十六路", "mixed-portfolio-twenty-four": "多题二十四路",
  "relay-sixteen": "全接力十六路", "relay-twenty": "全接力二十路", "relay-twenty-two": "全接力二十二路",
  "relay-twenty-three": "全接力二十三路", "relay-twenty-four": "全接力二十四路",
  "relay-dual-target": "二十二路双目标接力",
  "square-frontier-1152-40": "方格分支搜索 · 40 路",
  "relay-frontier-1152-40": "接力分支搜索 · 40 路"};
const mixedNames = {mixed: "混合双路", "mixed-four": "混合四路", "mixed-eight": "混合八路",
  "mixed-sixteen": "混合十六路", "mixed-portfolio": "多题十六路",
  "mixed-portfolio-twenty-four": "多题二十四路", "relay-sixteen": "全接力十六路",
  "relay-twenty": "全接力二十路", "relay-twenty-two": "全接力二十二路",
  "relay-twenty-three": "全接力二十三路", "relay-twenty-four": "全接力二十四路",
  "relay-dual-target": "二十二路双目标接力",
  "square-frontier-1152-40": "方格分支搜索 · 40 路",
  "relay-frontier-1152-40": "接力分支搜索 · 40 路"};
const relayRunNames = {16: "relay-sixteen", 20: "relay-twenty", 22: "relay-twenty-two",
  23: "relay-twenty-three", 24: "relay-twenty-four"};
const comparisons = {
  "square-relay-frontier-comparison": {field: "square_relay_frontier_benchmark", cases: ["square-frontier-1152-40", "relay-frontier-1152-40"]},
  concurrency: {field: "concurrency_benchmark", cases: ["mixed", "mixed-four"]},
  "high-concurrency": {field: "high_concurrency_benchmark", cases: ["mixed-four", "mixed-eight"]},
  "sixteen-lane-comparison": {field: "sixteen_lane_benchmark", cases: ["mixed-eight", "mixed-sixteen"]},
  "portfolio-comparison": {field: "portfolio_benchmark", cases: ["mixed-sixteen", "mixed-portfolio"]},
  "portfolio-scale-comparison": {field: "portfolio_scale_benchmark", cases: ["mixed-portfolio", "mixed-portfolio-twenty-four"]},
  "relay-mix-comparison": {field: "relay_mix_benchmark", cases: ["relay-sixteen", "mixed-portfolio"]},
  "relay-scale-comparison": {field: "relay_scale_benchmark", cases: ["relay-twenty-four", "relay-sixteen"]},
  "relay-twenty-lane-comparison": {field: "relay_twenty_lane_benchmark", cases: ["relay-twenty", "relay-sixteen"]},
  "relay-twenty-two-lane-comparison": {field: "relay_twenty_two_lane_benchmark", cases: ["relay-twenty-two", "relay-twenty"]},
  "relay-twenty-three-lane-comparison": {field: "relay_twenty_three_lane_benchmark", cases: ["relay-twenty-three", "relay-twenty-two"]},
  "relay-target-comparison": {field: "relay_target_benchmark", cases: ["relay-dual-target", "relay-twenty-two"]},
};

function gameName(pack) {
  if (pack?.startsWith("cap-set")) return "Cap Set · " + (pack.match(/(\d+)d$/)?.[1] || 4) + "D";
  if (pack?.startsWith("golomb-ruler")) return "Golomb · " + (pack.match(/(\d+)$/)?.[1] || 8);
  if (pack === "lorenz-divergence") return "Lorenz";
  const square = /^square-(\d+)$/.exec(pack);
  return square ? square[1] + " 块方格" : "接力";
}

function renderPackingCases(cases) {
  const entries = Object.entries(cases ?? {});
  packingCases.hidden = !entries.length;
  packingCases.textContent = entries.map(([pack, info]) => gameName(pack) + " " + info.score).join(" · ");
}

function renderGame(name, game) {
  const score = document.getElementById(name + "-score");
  score.textContent = game.score ?? "—";
  const link = document.getElementById(name + "-link");
  const target = new URL(game.url);
  target.searchParams.set("lang", window.GameLanguage.current);
  link.href = target.href;
  link.target = "_blank";
  link.rel = "noopener";
  const job = game.job;
  document.getElementById(name + "-job").textContent =
    job.status === "running" || job.status === "stopping"
      ? " · " + job.completed + " / " + job.rounds + " 轮 " + (job.stage || "")
      : "";
}

function recordedPace(entry) {
  if (!entry?.live || !entry.metered_rounds) return {};
  if (entry.stop === "window") {
    return {effective: entry.tokens_per_minute, output: entry.output_per_minute};
  }
  if (!(entry.wall_seconds > 0)) return {};
  const minutes = Math.min(entry.wall_seconds, entry.window_seconds ?? entry.wall_seconds) / 60;
  return {effective: (entry.uncached_input_tokens + entry.output_tokens) / minutes,
    output: entry.output_tokens / minutes};
}

function renderBenchmark(report, rows) {
  for (const row of rows.rows) {
    const name = row.dataset.scenario || row.id;
    const entry = report?.scenarios?.[name];
    const showUsage = entry?.live && entry?.metered_rounds > 0;
    const pace = recordedPace(entry);
    const values = [
      pace.effective,
      showUsage ? entry.uncached_input_tokens + entry.output_tokens : null,
      showUsage ? entry.total_tokens : null,
      pace.output,
      showUsage ? entry.cached_input_tokens : null,
      entry?.unique_candidates,
      entry?.model_rounds,
    ];
    row.querySelectorAll("td").forEach((cell, index) => {
      cell.textContent = Number.isFinite(values[index]) ? count.format(values[index]) : "—";
    });
    row.classList.toggle("winner", report?.winner === name);
  }
}

function renderConcurrency(report, cases) {
  concurrencyResults.hidden = !report;
  portfolioCandidates.hidden = true;
  if (!report) return;
  for (const [index, row] of Array.from(concurrencyRows.rows).entries()) {
    const name = cases[index];
    const entry = report.scenarios[name];
    row.dataset.scenario = name;
    row.cells[0].textContent = names[name] + (entry?.stop !== "window" && entry?.wall_seconds > 0
      ? " · " + count.format(Math.floor(entry.wall_seconds)) + " 秒" : "");
  }
  renderBenchmark(report, concurrencyRows);
  const results = Object.values(report.scenarios);
  const completed = results.filter(entry => entry.stop === "window").length;
  const error = results.flatMap(entry => entry.errors ?? [])[0];
  concurrencyStatus.textContent = report.winner ? names[report.winner] + "的实测吞吐最高"
    : error ? "已跑满 " + completed + " / 2 组 · " + error : "已跑满 " + completed + " / 2 组";
  const portfolioName = cases.findLast(name => name.startsWith("mixed-portfolio")
    && report.scenarios[name]?.unique_candidates_by_pack);
  const portfolio = report.scenarios[portfolioName];
  if (portfolio?.unique_candidates_by_pack && portfolio.packs?.length) {
    const packNames = [...new Set(portfolio.packs)].map(pack => [gameName(pack), portfolio.unique_candidates_by_pack[pack] ?? 0]);
    packNames.sort(([left], [right]) => left.localeCompare(right, "zh-CN", {numeric: true}));
    portfolioCandidates.hidden = false;
    portfolioCandidates.textContent = names[portfolioName] + "去重候选 · " + packNames.map(([label, value]) =>
      label + " " + count.format(value)).join(" · ");
  }
}

function renderMixed(entry) {
  mixedResult.hidden = !entry;
  if (!entry) return;
  const duration = entry.stop === "window" ? "已跑满 10 分钟"
    : "提前结束" + (entry.wall_seconds > 0 ? " · 已跑 " + count.format(Math.round(entry.wall_seconds)) + " 秒" : "");
  const effective = (entry.uncached_input_tokens || 0) + (entry.output_tokens || 0);
  const speed = recordedPace(entry).effective;
  const pace = Number.isFinite(speed) ? " · " + count.format(speed) + " 有效 token/分钟" : "";
  const reason = entry.errors?.[0] ? " · " + entry.errors[0] : "";
  const varied = entry.packs?.some(pack => pack.startsWith("square-") && pack !== "square-26");
  const onlyRelay = entry.packs?.length && entry.packs.every(pack => pack === "relay-49-frontier");
  const forty = entry.packs?.length === 40 && new Set(entry.packs).size === 1;
  const mode = (forty ? names[(onlyRelay ? "relay" : "square")+"-frontier-1152-40"] : null) || (entry.packs?.length === 1 ? gameName(entry.packs[0]) : null) || (entry.relay_targets === 2 ? names["relay-dual-target"]
    : onlyRelay ? (names[relayRunNames[entry.packs.length]] || "全接力")
    : varied ? (entry.packs.length === 24 ? "多题二十四路" : "多题十六路")
    : ({2: "混合双路", 4: "混合四路", 8: "混合八路", 16: "混合十六路"}[entry.packs?.length] || "混合双路"));
  mixedResult.textContent = "最近一次" + mode + " · " + duration + " · " + count.format(entry.metered_rounds || 0)
    + " 轮 · 有效 " + count.format(effective) + " token" + pace + " · 新纪录 "
    + count.format(entry.project_records || 0) + " 项" + reason;
}

function renderProgress(report) {
  progress.hidden = !report;
  if (!report) return;
  const remaining = Math.max(0, report.remaining_seconds);
  const percent = report.window_seconds ? " · 计时进度 "
    + Math.floor(100 * (report.window_seconds - remaining) / report.window_seconds) + "%" : "";
  const pace = report.metered_rounds
    ? " · " + count.format(report.tokens_per_minute) + " 有效 token/分钟 · 累计有效 "
      + count.format(report.uncached_input_tokens + report.output_tokens) + " · 缓存输入 "
      + count.format(report.cached_input_tokens)
    : "";
  const lanes = report.lanes.map((lane, index) => {
    if (lane.interruption) {
      return (index + 1) + ". " + gameName(lane.pack) + " · " + lane.turns + " 轮 · 本路已停 · " + lane.interruption;
    }
    const speed = lane.metered_rounds ? " · " + count.format(lane.tokens_per_minute) + " token/分钟" : "";
    const hypothesis = lane.hypothesis ? " · " + lane.hypothesis : "";
    const checked = lane.checked ? " · 本机检查 " + count.format(lane.checked) + " 次" : "";
    const timing = lane.request_seconds == null ? "" : " · 请求 " + lane.request_seconds + " 秒，搜索 "
      + lane.local_seconds + " 秒，复算 " + lane.verify_seconds + " 秒";
    const last = lane.latest ? " · " + lane.latest : "";
    return (index + 1) + ". " + gameName(lane.pack) + " · " + lane.turns + " 轮" + speed
      + hypothesis + checked + timing + last;
  });
  const clock = Math.floor(remaining / 60) + " 分 " + (remaining % 60) + " 秒";
  const forecast = report.projected_tokens == null
    ? "" : " · 预计本组 " + count.format(report.projected_tokens) + " 非缓存＋输出";
  progress.textContent = (names[report.scenario] || gameName(report.scenario)) + percent + " · 本组预计剩余 " + clock + pace + forecast
    + "\n" + lanes.join("\n");
}

function renderTrials(report) {
  const lanes = (report?.lanes || []).map((lane, index) => ({
    label: "通道 " + (index + 1) + " · " + gameName(lane.pack) + " · 第 " + lane.turns + " 轮",
    polygons: lane.polygons,
  })).filter(lane => lane.polygons?.length);
  trialLayouts.hidden = lanes.length === 0;
  const version = JSON.stringify(lanes);
  if (version === shownTrials) return;
  shownTrials = version;
  trialLayouts.replaceChildren();
  for (const lane of lanes) {
    const figure = document.createElement("figure");
    const canvas = document.createElement("canvas");
    const caption = document.createElement("figcaption");
    caption.textContent = lane.label;
    canvas.setAttribute("aria-label", lane.label);
    canvas.width = canvas.height = 284;
    const ctx = canvas.getContext("2d");
    const side = Math.max(...lane.polygons.flat(2));
    const scale = 260 / side;
    for (const [index, corners] of lane.polygons.entries()) {
      ctx.beginPath();
      corners.forEach(([x, y], point) => {
        if (point === 0) ctx.moveTo(12 + x * scale, 272 - y * scale);
        else ctx.lineTo(12 + x * scale, 272 - y * scale);
      });
      ctx.closePath();
      ctx.fillStyle = ["#b4c7ab", "#8fae97", "#e0c89d"][index % 3];
      ctx.fill();
      ctx.strokeStyle = "#4d6e53";
      ctx.stroke();
    }
    figure.append(canvas, caption);
    trialLayouts.append(figure);
  }
}

function renderTower(floors) {
  tower.hidden = floors < 1;
  towerLabel.textContent = "成果楼 · " + count.format(floors) + " 层";
  while (towerFloors.childElementCount > floors) towerFloors.lastElementChild.remove();
  while (towerFloors.childElementCount < floors) towerFloors.append(document.createElement("i"));
}

function renderContinuous(session, busy, error) {
  continuousProgress.hidden = !session;
  if (!session) return;
  const label = names[session.scenario] || mixedNames[session.scenario] || gameName(session.scenario);
  const action = busy ? "第 " + Math.max(1, session.window) + " 组"
    : ({limit: "已完成指定组数", stopped: "已停止", interrupted: "已中断",
        recovered: "已恢复历史记录"}[session.end_reason] ?? "已结束");
  const detail = session.message || error;
  continuousProgress.textContent = label + "连续冲榜 · " + action
    + " · 已完成 " + count.format(session.completed) + (session.window_limit ? " / " + session.window_limit : "") + " 组"
    + " · 累计 " + count.format(session.effective_tokens) + " 有效 token"
    + " · " + count.format(session.metered_rounds) + " 个回包"
    + " · 新纪录 " + count.format(session.new_records) + " 项" + (detail ? " · " + detail : "");
}

function renderWeeklyMeter(meter) {
  weeklyMeter.replaceChildren();
  const heading = document.createElement("h3");
  heading.textContent = "最近一次游戏周限额记录";
  weeklyMeter.append(heading);
  if (!meter) {
    const empty = document.createElement("p");
    empty.textContent = "尚无游戏周限额记录；下次获准游戏运行后会显示开始与结束读数。";
    weeklyMeter.append(empty);
    return;
  }

  const rows = document.createElement("dl");
  const addRow = (label, value) => {
    const term = document.createElement("dt");
    term.textContent = label;
    const detail = document.createElement("dd");
    detail.textContent = value;
    rows.append(term, detail);
  };
  const resetUtc = value => {
    if (typeof value === "number") {
      const milliseconds = value > 1e12 ? value : value * 1000;
      const date = new Date(milliseconds);
      return Number.isNaN(date.valueOf()) ? String(value) : date.toISOString();
    }
    return value ?? "未知";
  };
  const windowText = value => value
    ? value.used_percent + "% · " + value.window_duration_minutes + " 分钟窗口 · 重置 UTC "
      + resetUtc(value.resets_at) + " · 采样 " + value.sampled_at_utc + " · 来源 " + value.source
    : "未取得读数";
  addRow("游戏窗口", meter.game_window);
  addRow("账号共享周用量 · 开始", windowText(meter.before?.codex_account_shared_weekly));
  addRow("账号共享周用量 · 结束", windowText(meter.after?.codex_account_shared_weekly));
  if (meter.comparable) {
    const change = meter.percentage_point_change;
    addRow("同一周窗口变化", (change > 0 ? "+" : "") + change + " 个百分点");
  } else {
    addRow("周窗口变化", "开始与结束窗口不同或读数缺失，变化不可比");
  }
  addRow("本次游戏回包", meter.effective_game_tokens == null
    ? "有效游戏 token 未提供"
    : count.format(meter.effective_game_tokens) + " 有效游戏 token");
  const localLimit = meter.after?.application_local_declaration
    ?? meter.before?.application_local_declaration;
  addRow("应用周上限 · 本地声明", localLimit
    ? localLimit.weekly_limit_percent + "% · 来源：" + localLimit.source
    : "未记录本地声明");
  addRow("应用实际已用", "未知；目前没有应用实际用量读数");
  weeklyMeter.append(rows);
}

async function update() {
  if (updating) return;
  updating = true;
  try {
    const response = await fetch("/api/lab", {cache: "no-store"});
    if (!response.ok) throw new Error("本机服务未返回状态");
    const state = await response.json();
    window.RunConsole?.show(state);
    connection.textContent = "本机已连接";
    renderWeeklyMeter(state.weekly_meter);
    for (const [name, game] of Object.entries(state.games)) renderGame(name, game);
    renderPackingCases(state.packing_cases);
    const report = state.benchmark;
    const busy = state.run === "running" || state.run === "stopping";
    const firstError = Object.values(report?.scenarios ?? {}).flatMap(row => row.errors ?? [])[0];
    renderBenchmark(report, benchmarkRows);
    renderMixed(state.mixed_run);
    const comparison = comparisons[concurrencyChoice.value];
    renderConcurrency(state[comparison.field], comparison.cases);
    renderContinuous(state.continuous, busy, state.error);
    renderProgress(state.progress);
    renderTrials(state.progress);
    const recordCounts = [...Object.values(state.games), ...Object.values(state.packing_cases ?? {})]
      .map(game => game.records);
    if (state.packing_cases !== null && recordCounts.length && recordCounts.every(Number.isInteger)) {
      renderTower(recordCounts.reduce((total, count) => total + count, 0));
    }
    featuredMixed = Object.hasOwn(mixedNames, state.featured_mixed) ? state.featured_mixed : "mixed";
    mixedStart.textContent = mixedNames[featuredMixed] + "冲榜";
    featuredEstimate.hidden = busy || !state.subscription.ready || !state.featured_seconds;
    featuredEstimate.textContent = "冲榜每组预计 " + count.format(state.featured_seconds / 60) + " 分钟"
      + (state.featured_estimate_tokens == null ? "" : " · 约 " + count.format(state.featured_estimate_tokens) + " 有效 token");
    mixedStart.disabled = busy || !state.subscription.ready;
    continuousStart.disabled = busy || !state.subscription.ready;
    concurrencyStart.disabled = busy || !state.subscription.ready;
    live.disabled = busy || !state.subscription.ready;
    resume.hidden = busy || !state.resumable;
    resume.disabled = !state.subscription.ready;
    replay.disabled = busy;
    stop.hidden = !busy;
    stop.disabled = state.run === "stopping";
    status.textContent = state.run === "stopping" ? "正在停止，已收到的回执会保留"
      : busy ? "正在运行本组对局"
      : state.run === "error" ? (state.error || "本次对照中断")
      : report?.winner ? "三组对照中，" + (names[report.winner] || gameName(report.winner)) + "最快"
      : firstError ? firstError
      : report?.live ? "模型实跑已归档"
      : report ? "已有裁判演练结果，等待模型实跑"
      : "等待实跑数据";
    planTitle.textContent = state.subscription.ready ? "ChatGPT 订阅已连接" : "连接 ChatGPT 订阅";
    planStatus.textContent = state.subscription.error_message || (state.subscription.ready
      ? "应用周上限已确认。实跑每组 10 分钟。"
      : state.subscription.pending ? "请完成官方页面授权"
      : state.subscription.connected ? "请在官方用量设置保存低于 100% 的周上限，再填入确认。"
      : "登录后可授权本地应用使用套餐。");
    connect.disabled = busy;
    limit.hidden = !state.subscription.connected || state.subscription.ready;
    if (state.subscription.connected) authorize.hidden = true;
  } catch {
    connection.textContent = "本机连接中断";
    window.RunConsole?.disconnected();
  } finally {
    updating = false;
  }
}

async function act(path, payload = {}) {
  if (isRequesting) return;
  isRequesting = true;
  try {
    const response = await fetch("/api/lab/" + path, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
    });
    const result = await response.json();
    if (!response.ok) throw new Error(result.error || "操作失败");
    if (path === "connect") {
      authorize.href = result.url;
      authorize.hidden = false;
      authorize.focus();
    }
    await update();
  } catch (error) {
    (path === "connect" || path === "confirm" ? planStatus : status).textContent = error.message;
  } finally {
    isRequesting = false;
  }
}

replay.addEventListener("click", () => act("replay"));
live.addEventListener("click", () => act("live"));
mixedStart.addEventListener("click", () => act(featuredMixed));
continuousStart.addEventListener("click", () => act("continuous"));
concurrencyStart.addEventListener("click", () => act(concurrencyChoice.value));
concurrencyChoice.addEventListener("change", update);
resume.addEventListener("click", () => act("resume"));
stop.addEventListener("click", () => act("stop"));
connect.addEventListener("click", () => act("connect"));
limit.addEventListener("submit", event => {
  event.preventDefault();
  act("confirm", {percent: document.getElementById("percent").valueAsNumber});
});

update();
setInterval(update, 2500);
