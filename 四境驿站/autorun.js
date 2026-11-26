"use strict";

window.AutoRun = (() => {
  const modes = {
    codex: "Codex 订阅自动运行",
    mcp: "Codex 桌面对话 · MCP",
    local: "本机搜索（不使用订阅）",
  };
  const statuses = {
    idle: "尚未运行", waiting: "等待领取", queued: "等待领取", running: "运行中",
    stopping: "正在停止", completed: "已完成", stopped: "已停止", blocked: "已暂停", failed: "运行失败",
  };
  const isActive = job => ["waiting", "queued", "running", "stopping"].includes(job?.status);

  // Desktop totals are deltas of this workspace's Codex usage counters from
  // delivery of the claimed prompt to the submission receipt (including cache).
  const desktopTrials = {
    "square-18": [[157602, 19.0], [277355, 89.5]],
    "square-19": [[193367, 50.8]],
    "relay-49-frontier": [[199999, 67.8]],
  };
  // Three timed searches with the native solver and judge on the host.
  const localTrials = {
    "square-18": [.326, .259, .265], "square-19": [.396, .390, .386],
    "relay-7": [.001, .002, .002], "relay-49": [1.470, 2.218, 1.256],
    "relay-49-frontier": [1.806, 2.124, 1.971],
  };
  const recentRuns = new Map();
  const promptSizes = new Map();

  function visibleTextSize(prompt) {
    if (!prompt) return null;
    // Count compact numeric groups and punctuation in the witness separately
    // from prose. This is a payload heuristic, not a provider token counter.
    const candidate = prompt.trim().split("\n").at(-1);
    const text = prompt + "\n" + candidate;
    const chunks = text.match(/\d{1,3}|[a-zA-Z_]{1,4}|[^\s]/gu);
    return chunks?.length || null;
  }

  function estimateRun(pack, mode, rounds) {
    const history = recentRuns.get(pack)?.[mode] || [];
    let times = history.map(row => row.seconds).filter(v => Number.isFinite(v) && v >= 0);
    let tokens = history.map(row => row.tokens).filter(v => Number.isSafeInteger(v) && v > 0);
    let basis;
    if (mode === "mcp") {
      const trials = desktopTrials[pack] || (pack === "relay-49" ? desktopTrials["relay-49-frontier"] : []);
      if (!times.length) times = trials.map(row => row[1]);
      if (!tokens.length) tokens = trials.map(row => row[0]);
      basis = trials.length ? `${pack === "relay-49" ? "同规模关卡" : "本关"}桌面长对话 ${trials.length} 轮实测。总 token 含缓存输入；耗时从接手算起。`
        : "桌面用量取决于对话长度与工具调用，等待玩家接手的时间另计。";
    } else if (mode === "local") {
      if (!times.length) times = localTrials[pack] || [];
      basis = `本机搜索和裁判耗时，参考 ${times.length} 轮实测。`;
    } else {
      basis = tokens.length || times.length ? `本关订阅回执含 ${tokens.length} 轮总 token 和 ${times.length} 轮耗时。`
        : "订阅模式尚无本关实测。文本量按题面及候选格式粗估，推理消耗另计。";
    }
    function range(values, format) {
      if (!values.length) return "待首轮实测";
      const low = format(Math.min(...values) * rounds), high = format(Math.max(...values) * rounds);
      return low === high ? `约 ${low}` : `${low} – ${high}`;
    }
    let tokenText = mode === "local" ? "0 token" : range(tokens, value => {
      const rounded = Number(value.toPrecision(2));
      return `${rounded.toLocaleString("zh-CN")} token`;
    });
    if (!tokens.length && mode === "codex" && promptSizes.has(pack)) {
      tokenText = "文本约 " + Number((promptSizes.get(pack) * rounds).toPrecision(2)).toLocaleString("zh-CN") + " token + 推理";
    }
    let timeText = range(times, value => value < 1 ? "不足 1 秒" : duration(Math.round(value)));
    if (!times.length && mode === "codex") {
      timeText = "待实测（单轮响应限时 3 分钟）";
    }
    const sorted = [...times].sort((a,b) => a-b);
    const mid = Math.floor(sorted.length / 2);
    const secondsPerRound = sorted.length ? (sorted[mid] + sorted[Math.floor((sorted.length-1)/2)]) / 2 : null;
    return {tokenText, timeText, secondsPerRound, basis: rounds > 1 ? basis + ` 按 ${rounds} 轮估算。` : basis};
  }

  const style = document.createElement("style");
  style.textContent = `
    .auto-run{--auto-ink:var(--forest,var(--ink));--auto-soft:#edf1e8;display:grid;
      grid-template-columns:minmax(0,1fr) minmax(0,.96fr);gap:20px;margin:0 0 20px;
      padding:16px 20px;border:1px solid var(--line);border-top:2px solid var(--auto-ink);
      background:var(--panel,var(--white));color:var(--ink);min-width:0}
    .auto-run[hidden],.auto-run [hidden]{display:none!important}
    .auto-run h2{margin:0 0 9px;font:750 1.2rem/1.3 var(--serif,"Songti SC",SimSun,serif);color:var(--auto-ink)}
    .auto-run form{display:grid;grid-template-columns:minmax(10.8rem,1.46fr) minmax(4.4rem,.55fr) auto;
      align-items:end;gap:8px;margin:0}
    .auto-run label{display:grid;gap:4px;min-width:0;font-size:.8rem;color:var(--muted)}
    .auto-run select,.auto-run button{min-height:42px;max-width:100%;font:inherit;font-size:.86rem;border-radius:4px}
    .auto-run select{width:100%;min-width:0;padding:6px 7px}
    .auto-run button{padding:7px 11px;white-space:nowrap;border:1px solid var(--auto-ink);color:#fffdf8;background:var(--auto-ink);font-weight:680}
    .auto-run button:hover:not(:disabled){background:var(--pine,var(--green));border-color:var(--pine,var(--green))}
    .auto-run button[data-stop]{background:transparent;color:var(--auto-ink)}
    .auto-run button[data-stop]:hover:not(:disabled){background:var(--auto-soft);color:var(--auto-ink)}
    .auto-run :focus-visible{outline:3px solid #bf6834;outline-offset:3px}
    .auto-run .auto-estimate{margin-top:9px;padding:7px 9px;border-left:2px solid var(--line);
      background:var(--auto-soft);font-size:.81rem;overflow-wrap:anywhere}
    .auto-run .auto-estimate-values{display:flex;flex-wrap:wrap;gap:2px 14px;margin:0;
      color:var(--auto-ink);font-variant-numeric:tabular-nums}
    .auto-run .auto-estimate-basis{margin:3px 0 0;font-size:.77rem;color:var(--muted)}
    .auto-run .auto-hint,.auto-run .auto-note,.auto-run .auto-empty{margin:8px 0 0;font-size:.81rem;
      color:var(--muted);overflow-wrap:anywhere}
    .auto-run .auto-result{border-left:1px solid var(--line);padding-left:18px;min-width:0}
    .auto-run .auto-top,.auto-run .auto-meta{display:flex;align-items:baseline;justify-content:space-between;gap:5px 10px;flex-wrap:wrap}
    .auto-run .auto-top strong{font-size:.97rem;color:var(--auto-ink);overflow-wrap:anywhere}
    .auto-run[data-state="failed"] .auto-top strong,.auto-run[data-state="blocked"] .auto-top strong,.auto-run .auto-note.error{color:#843822}
    .auto-run .auto-meta{font-size:.79rem;color:var(--muted);font-variant-numeric:tabular-nums}
    .auto-run .auto-live{margin-top:6px;color:var(--muted);font-size:.79rem;font-variant-numeric:tabular-nums}
    .auto-run .auto-live progress{display:block;width:100%;height:7px;margin:4px 0 5px;accent-color:var(--auto-ink)}
    .auto-run .auto-rates{display:flex;flex-wrap:wrap;gap:1px 12px}
    .auto-run .auto-rates span:empty{display:none}
    .auto-run .auto-top a{font-size:.76rem;text-underline-offset:3px}
    .auto-run .auto-handoff{margin-top:10px;font-size:.86rem}
    .auto-run .auto-handoff a{color:var(--auto-ink);font-weight:680;text-underline-offset:3px}
    .auto-run .auto-handoff details{margin-top:6px}
    .auto-run .auto-handoff summary{cursor:pointer;color:var(--muted)}
    .auto-run .auto-handoff textarea{box-sizing:border-box;margin-top:6px;width:100%;font:inherit}
    .auto-run .subscription{margin-top:10px;border-top:1px solid var(--line);padding-top:8px;
      font-size:.82rem;line-height:1.55;text-align:left}
    .auto-run .subscription summary{cursor:pointer;font-weight:650;color:var(--auto-ink)}
    .auto-run .subscription p{margin:8px 0}
    .auto-run .subscription-actions{display:flex;align-items:center;flex-wrap:wrap;gap:6px 10px}
    .auto-run .subscription button{color:var(--auto-ink);background:transparent;min-height:34px}
    .auto-run .subscription button:hover:not(:disabled){background:var(--auto-soft)}
    .auto-run .subscription a{color:var(--auto-ink);text-underline-offset:3px}
    .auto-run .subscription .cap-choice{display:flex;align-items:baseline;gap:7px;margin:8px 0}
    .auto-run .subscription .cap-check{display:flex;align-items:flex-start;gap:7px;margin:8px 0}
    .auto-run .cap-choice input{width:5em;min-width:0;min-height:34px;padding:4px 6px}
    .auto-run .cap-check input{width:15px;height:15px;min-width:15px;min-height:15px;margin:3px 0 0}
    .auto-run ol{list-style:none;padding:0;margin:7px 0 0;max-height:11rem;overflow:auto}
    .auto-run li{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:3px 8px;padding:5px 0;
      border-top:1px solid var(--line);font-size:.8rem;overflow-wrap:anywhere}
    .auto-run li span:last-child{color:var(--muted);font-variant-numeric:tabular-nums}
    @media(max-width:910px){.auto-run form{grid-template-columns:minmax(0,1fr) minmax(4.4rem,.55fr)}.auto-run button{grid-column:1/-1}}
    @media(max-width:650px){.auto-run{grid-template-columns:minmax(0,1fr);gap:13px;padding:13px;margin-bottom:15px}
      .auto-run .auto-result{border-left:0;padding:10px 0 0;border-top:1px solid var(--line)}}
  `;
  document.head.append(style);

  const markup = `
    <div>
      <h2 id="auto-title">自动研究</h2>
      <form>
        <label>执行方式<select name="mode" aria-describedby="auto-hint">${Object.entries(modes).map(([value, label]) => `<option value="${value}">${label}</option>`).join("")}</select></label>
        <label>轮数<select name="rounds"><option value="1">1 轮</option><option value="3">3 轮</option><option value="5">5 轮</option></select></label>
        <button type="submit" aria-describedby="auto-estimate" disabled>开始自动运行</button>
        <button data-stop type="button" hidden>停止运行</button>
      </form>
      <div id="auto-estimate" class="auto-estimate" data-estimate role="status" aria-atomic="true" hidden>
        <p class="auto-estimate-values">
          <span>预计 Token <strong data-estimate-token></strong></span>
          <span>预计用时 <strong data-estimate-time></strong></span>
        </p>
        <p class="auto-estimate-basis" data-estimate-basis></p>
      </div>
      <p id="auto-hint" class="auto-hint"></p>
      <div class="auto-handoff" data-handoff hidden>
        <a data-codex>在 Codex 中接手本局</a>
        <details><summary>在已有对话中接手</summary>
          <textarea data-prompt aria-label="本局接手指令" rows="3" readonly></textarea>
        </details>
      </div>
      <details class="subscription" data-subscription hidden>
        <summary>个人套餐连接与周上限</summary>
        <p data-account role="status"></p>
        <div class="subscription-actions">
          <button type="button" data-connect>获取官方授权链接</button>
          <a data-login-link hidden target="_blank" rel="noopener noreferrer">前往 OpenAI 授权</a>
          <button type="button" data-refresh>刷新登录状态</button>
        </div>
        <p><a href="https://chatgpt.com/settings/usage" target="_blank" rel="noopener noreferrer">打开 ChatGPT 用量设置</a>，为本游戏设置 1–99% 的周上限。回到这里填写实际设置值。</p>
        <label class="cap-choice">官方已设周上限 <input data-cap type="number" min="1" max="99" step="1" inputmode="numeric">%</label>
        <label class="cap-check"><input data-attest type="checkbox">我已在官方为本游戏设置上述上限，并确认仅使用个人套餐，不使用充值额度。</label>
        <button type="button" data-confirm>保存我的确认</button>
        <p data-auth-note role="status" hidden></p>
      </details>
    </div>
    <div class="auto-result">
      <div class="auto-top"><strong role="status" data-status>正在查看任务…</strong><a data-ledger download hidden>下载运行记录</a></div>
      <div class="auto-meta"><span data-progress></span><span data-time>用时 —</span></div>
      <div class="auto-live" data-live hidden>
        <div data-work hidden aria-live="polite"></div>
        <progress aria-label="整局轮次进度" data-progress-bar value="0" max="1"></progress>
        <div class="auto-rates"><span data-remaining></span><span data-speed></span><span data-output-speed></span></div>
      </div>
      <p class="auto-note" data-note aria-live="polite" hidden></p>
      <p class="auto-empty" data-empty>逐轮回执将在这里显示。</p>
      <ol aria-label="逐轮回执" data-receipts hidden></ol>
    </div>
  `;

  function duration(value) {
    const total = Math.max(0, Number(value) || 0);
    if (total > 0 && total < 0.1) return "少于 0.1 秒";
    if (total < 10) return `${Number(total.toFixed(1))} 秒`;
    const seconds = Math.floor(total);
    const minutes = Math.floor(seconds / 60);
    return [minutes ? `${minutes} 分` : "", `${seconds % 60} 秒`].filter(Boolean).join(" ");
  }

  function writeText(element, text) {
    if (element.textContent !== text) element.textContent = text;
  }

  function measuredPace(job) {
    let measuredRounds = 0, measuredSeconds = 0, outputTokens = 0, outputSeconds = 0;
    for (const receipt of job?.receipts || []) {
      const seconds = receipt.active_seconds ?? (job.mode === "mcp" ? null : receipt.seconds);
      if (!Number.isFinite(seconds) || seconds <= 0) continue;
      measuredRounds++;
      measuredSeconds += seconds;
      if (Number.isFinite(receipt.output_tokens) && receipt.output_tokens >= 0) {
        outputTokens += receipt.output_tokens;
        outputSeconds += seconds;
      }
    }
    return {
      secondsPerRound: measuredRounds ? measuredSeconds / measuredRounds : null,
      tokensPerSecond: outputSeconds ? outputTokens / outputSeconds : null,
    };
  }

  async function requestJSON(path, payload) {
    const options = {cache: "no-store"};
    if (payload) Object.assign(options, {
      method: "POST", headers: {"Content-Type": "application/json"}, body: JSON.stringify(payload),
    });
    const response = await fetch(path, options);
    const result = await response.json();
    if (!response.ok || result.error) {
      throw new Error(result.error?.message || result.error || result.message || "本机服务未能处理这次请求。");
    }
    return result;
  }

  async function request(path, payload) {
    const result = await requestJSON(path, payload);
    if (!Object.hasOwn(statuses, result.status)) throw new Error("本机服务未返回可读取的任务状态。");
    return result;
  }

  function setupSubscription(panel) {
    const find = selector => panel.querySelector(selector);
    const account = find("[data-account]"), note = find("[data-auth-note]");
    const connect = find("[data-connect]"), login = find("[data-login-link]"), refresh = find("[data-refresh]");
    const cap = find("[data-cap]"), attest = find("[data-attest]"), confirm = find("[data-confirm]");
    let state = null, busy = false;

    function render() {
      const allowed = state?.connected && state?.plan_permission;
      const percent = cap.valueAsNumber;
      connect.disabled = refresh.disabled = busy;
      cap.disabled = attest.disabled = !allowed || busy;
      confirm.disabled = !allowed || busy || !attest.checked || !Number.isInteger(percent) || percent < 1 || percent > 99;
      writeText(account, !state ? "正在读取本机授权状态…"
        : state.ready ? `已连接个人套餐。你已确认本游戏的周上限为 ${state.weekly_limit_percent}%。下次点击「开始自动运行」即可开局。`
        : state.error_message || (!state.connected ? "本机游戏尚未连接个人套餐。请先获取授权链接。"
        : !state.plan_permission ? "已登录。请重新授权本游戏使用个人套餐。"
        : "已获套餐授权。请在官方页面设置本游戏的周上限，并在此确认。"));
    }

    async function readStatus() {
      const value = await requestJSON("/api/subscription/status");
      if (typeof value.connected !== "boolean" || typeof value.ready !== "boolean") throw new Error("无法读取本机的订阅连接状态。");
      state = value;
      if (state.connected && state.plan_permission) {
        login.hidden = true;
        login.removeAttribute("href");
      }
      if (state.limit_confirmed && !cap.value && Number.isInteger(state.weekly_limit_percent)) {
        cap.value = state.weekly_limit_percent;
      }
    }

    async function act(operation) {
      if (busy) return;
      busy = true;
      note.hidden = true;
      render();
      try {
        if (operation === "connect") {
          login.hidden = true;
          login.removeAttribute("href");
          const result = await requestJSON("/api/subscription/connect", {});
          const url = new URL(result.url);
          if (url.origin !== "https://auth.openai.com" || url.username || url.password) throw new Error("本机服务返回的官方授权链接无效。");
          login.href = url.href;
          login.hidden = false;
          writeText(note, "请打开官方授权链接。完成后回到此处，点击「刷新登录状态」。");
          note.hidden = false;
        } else {
          if (operation === "confirm") {
            await requestJSON("/api/subscription/confirm", {percent: cap.valueAsNumber});
            attest.checked = false;
          }
          await readStatus();
        }
      } catch (error) {
        if (operation === "status") state = null;
        writeText(note, error.message || "本机订阅设置暂不可用。");
        note.hidden = false;
      } finally {
        busy = false;
        render();
        if (!state && !note.hidden) writeText(account, "本机授权状态待确认。");
      }
    }

    panel.addEventListener("toggle", () => { if (panel.open && !panel.hidden) act("status"); });
    refresh.addEventListener("click", () => act("status"));
    connect.addEventListener("click", () => act("connect"));
    confirm.addEventListener("click", () => { if (!confirm.disabled) act("confirm"); });
    cap.addEventListener("input", render);
    attest.addEventListener("change", render);
    render();
  }

  function attach(root, onStoreChange) {
    root.innerHTML = markup;
    root.hidden = false;
    const find = selector => root.querySelector(selector);
    const form = find("form");
    const mode = form.elements.namedItem("mode");
    const rounds = form.elements.namedItem("rounds");
    const start = find('button[type="submit"]');
    const stop = find("[data-stop]");
    const estimate = find("[data-estimate]");
    const estimateToken = find("[data-estimate-token]");
    const estimateTime = find("[data-estimate-time]");
    const estimateBasis = find("[data-estimate-basis]");
    const hint = find("#auto-hint");
    const status = find("[data-status]");
    const progress = find("[data-progress]");
    const time = find("[data-time]");
    const live = find("[data-live]");
    const work = find("[data-work]");
    const progressBar = find("[data-progress-bar]");
    const remaining = find("[data-remaining]");
    const speed = find("[data-speed]");
    const outputSpeed = find("[data-output-speed]");
    const note = find("[data-note]");
    const receiptList = find("[data-receipts]");
    const empty = find("[data-empty]");
    const ledger = find("[data-ledger]");
    const subscription = find("[data-subscription]");
    const handoff = find("[data-handoff]");
    const codexLink = find("[data-codex]");
    const handoffPrompt = find("[data-prompt]");
    handoffPrompt.addEventListener("focus", () => handoffPrompt.select());
    setupSubscription(subscription);
    let pack = "", displayedHash = "", ready = false, permitted = true, job = null, action = "", timer, version = 0;
    let connectionError = "", actionError = "", pendingStart = null, previousReceipts = "";
    let observedClaim = null;

    function render() {
      const active = isActive(job);
      const jobStatus = job?.status || "idle";
      const stage = job?.stage;
      const selectedMode = active ? job.mode : mode.value;
      subscription.hidden = selectedMode !== "codex";
      if (subscription.hidden) subscription.open = false;
      const waiting = job?.mode === "mcp" && active && (stage === "等待 ChatGPT 领取" || jobStatus === "waiting" || jobStatus === "queued");
      handoff.hidden = !(waiting && job.id);
      if (!handoff.hidden) {
        const prompt = `用 research_games MCP 接手 ${pack} 的现有对局 ${job.id}，逐轮领取、提交并读回裁判回执，直到本局结束。`;
        if (handoffPrompt.value !== prompt) {
          handoffPrompt.value = prompt;
          const link = new URL("codex://threads/new");
          link.searchParams.set("prompt", prompt);
          codexLink.href = link.href;
        }
      } else {
        handoffPrompt.value = "";
        codexLink.removeAttribute("href");
        handoff.querySelector("details").open = false;
      }
      root.dataset.state = jobStatus;
      mode.disabled = rounds.disabled = active || Boolean(action);
      start.hidden = active;
      start.disabled = !job || !ready || !permitted || Boolean(action) || Boolean(connectionError);
      stop.hidden = !active;
      stop.disabled = Boolean(action) || jobStatus === "stopping";
      estimate.hidden = !pack;
      let referencePace = null;
      if (!estimate.hidden) {
        const {tokenText, timeText, basis, secondsPerRound} = estimateRun(pack, selectedMode, active ? job.rounds : Number(rounds.value));
        referencePace = secondsPerRound;
        writeText(estimateToken, tokenText);
        writeText(estimateTime, timeText);
        writeText(estimateBasis, basis);
      }
      writeText(start, action === "start" ? "正在开局…" : selectedMode === "mcp" ? "提交到对话队列" : "开始自动运行");
      writeText(stop, action === "stop" || jobStatus === "stopping" ? "正在停止…" : "停止运行");
      const explanation = selectedMode === "codex" ? "连接个人套餐后，本机会自动领题和交卷。需要授权或确认周上限时，这里会显示原因。"
        : selectedMode === "local" ? "在这台电脑上搜索候选，然后交给同一本地裁判复核。"
        : waiting ? "在 Codex 的「烧额游戏」工作区发送接手指令。发送一次即可继续本局全部轮次。"
        : active ? "对话通过 MCP 提交方案后，这里会显示裁判回执。"
        : "选好轮数并提交，随后在 Codex 中接手。每轮结果会自动回到这里。";
      writeText(hint, explanation);
      writeText(status, waiting ? "等待 Codex 通过 MCP 领取"
        : !job && connectionError ? "暂时无法读取任务"
        : !job ? "正在查看任务…"
        : jobStatus === "idle" && !permitted ? "当前题目已达成目标"
        : active && stage ? stage
        : statuses[jobStatus]);
      const error = actionError || connectionError;
      const summary = error || job?.message || (stage && stage !== status.textContent && !waiting ? stage : "");
      writeText(note, summary);
      note.hidden = !summary;
      note.classList.toggle("error", Boolean(error) || jobStatus === "blocked" || jobStatus === "failed");
      writeText(progress, job?.rounds ? `${modes[job.mode] || "自动运行"} · 已交 ${job.completed || 0} / ${job.rounds} 轮` : "");
      writeText(time, job?.id ? `用时 ${duration(job.elapsed_seconds)}` : "用时 —");
      live.hidden = !job?.id || !job.rounds;
      if (!live.hidden) {
        work.hidden = !active || !job.work;
        if (!work.hidden) {
          writeText(work, `${job.work.stage} · 本轮已检查 ${job.work.checked.toLocaleString("zh-CN")} 个候选`);
        }
        const completed = job.completed || 0;
        progressBar.max = job.rounds;
        progressBar.value = completed;
        const {secondsPerRound, tokensPerSecond} = measuredPace(job);
        const legacyReceipts = (job.receipts || []).filter(r => Number.isFinite(r.seconds) && r.seconds > 0);
        const legacySeconds = legacyReceipts.reduce((sum, r) => sum + r.seconds, 0);
        const legacyRate = legacySeconds ? 60 * legacyReceipts.length / legacySeconds : 0;
        writeText(speed, secondsPerRound ? `实测 ${Number((60 / secondsPerRound).toPrecision(2))} 轮/分钟`
          : legacyRate ? `交卷 ${Number(legacyRate.toPrecision(2))} 轮/分钟（含领取等待）` : active ? "待测速" : "");
        writeText(outputSpeed, tokensPerSecond === null ? "" : `实测输出 ${Number(tokensPerSecond.toPrecision(2))} token/秒`);
        let remainingText = "";
        if (active && jobStatus !== "stopping" && action !== "stop") {
          const pace = secondsPerRound || referencePace;
          const roundElapsed = Number.isFinite(job.turn_active_seconds) ? Math.max(0, job.turn_active_seconds) : 0;
          const source = secondsPerRound ? "按已交轮次" : "按历史参考";
          const roundsLeft = Math.max(0, job.rounds - completed);
          const secondsAfterThisRound = pace * Math.max(0, roundsLeft - 1);
          const secondsLeft = secondsAfterThisRound + Math.max(0, pace - roundElapsed);
          remainingText = !roundsLeft ? "预计剩余 等待裁判完成"
            : !pace ? "预计剩余 待测"
            : roundElapsed < pace ? `预计剩余约 ${duration(secondsLeft)}（${source}）`
            : secondsAfterThisRound > 0 ? `本轮已超参考，后续轮次预计 ${duration(secondsAfterThisRound)}（${source}）`
            : "预计剩余 本轮已超参考用时";
        }
        writeText(remaining, remainingText);
      }
      ledger.hidden = !job?.id;

      const receipts = Array.isArray(job?.receipts) ? job.receipts : [];
      const fingerprint = JSON.stringify(receipts);
      if (fingerprint !== previousReceipts) {
        previousReceipts = fingerprint;
        receiptList.replaceChildren(...receipts.map(receipt => {
          const item = document.createElement("li");
          const result = document.createElement("span");
          const elapsed = document.createElement("span");
          const verdict = receipt.accepted ? "已存档" : "未更新";
          result.textContent = `第 ${receipt.turn} 轮 · ${verdict}${receipt.message ? " · " + receipt.message : ""}`;
          elapsed.textContent = Number.isFinite(receipt.seconds) ? duration(receipt.seconds) : "";
          item.append(result, elapsed);
          return item;
        }));
      }
      empty.hidden = receipts.length > 0;
      writeText(empty, job?.id && !active ? "本局没有裁判回执。" : "逐轮回执将在这里显示。");
      receiptList.hidden = !receipts.length;
    }

    function apply(result) {
      if (result.pack_id !== pack) return;
      if (!Object.hasOwn(result, "turn_active_seconds") && result.mode === "mcp"
          && result.status === "running" && result.stage?.includes("已接手")) {
        const key = result.id + "/" + result.completed;
        if (observedClaim?.key !== key) observedClaim = {key, start: performance.now()};
        result.turn_active_seconds = (performance.now() - observedClaim.start) / 1000;
      } else {
        observedClaim = null;
      }
      if (result.estimate_samples) recentRuns.set(pack, result.estimate_samples);
      if (pendingStart && result.id && result.id !== job?.id) pendingStart = null;
      const firstSnapshot = job === null;
      const previous = job?.id === result.id ? job : null;
      const priorReceiptCount = previous?.receipts?.length || 0;
      const newlyAccepted = result.receipts?.slice(priorReceiptCount).some(receipt => receipt.accepted);
      const terminal = result.id && !isActive(result) && (result.id !== previous?.id || result.status !== previous.status);
      job = result;
      connectionError = "";
      if (isActive(job)) actionError = "";
      if (isActive(job)) {
        if ([...mode.options].some(option => option.value === job.mode)) mode.value = job.mode;
      }
      if (isActive(job) || (firstSnapshot && job.id)) {
        if ([...rounds.options].some(option => Number(option.value) === job.rounds)) rounds.value = String(job.rounds);
      }
      render();
      if (job.mode === "codex" && job.status === "blocked" && terminal && !subscription.hidden) subscription.open = true;
      if (terminal && result.mode === "codex" && !result.estimate_samples && result.completed) {
        const selectedPack = pack;
        requestJSON("/api/auto/ledger?pack=" + encodeURIComponent(pack)).then(ledger => {
          if (job?.id !== ledger.id || pack !== selectedPack) return;
          const samples = (ledger.records || []).map(r => {
            const u = r.model_response?.usage || {};
            const tokens = u.total_tokens ?? (Number.isFinite(u.input_tokens) && Number.isFinite(u.output_tokens)
              ? u.input_tokens + u.output_tokens : null);
            return {seconds: r.seconds, tokens};
          });
          recentRuns.set(pack, {...recentRuns.get(pack), codex: samples});
          render();
        }).catch(() => {});
      }
      if (newlyAccepted || terminal || (result.best_hash && displayedHash && result.best_hash !== displayedHash)) {
        onStoreChange(pack);
      }
    }

    function schedule() {
      clearTimeout(timer);
      timer = setTimeout(read, isActive(job) ? 1100 : 4500);
    }

    async function read() {
      const current = version;
      try {
        const result = await request("/api/auto?pack=" + encodeURIComponent(pack));
        if (current === version) apply(result);
      } catch {
        if (current !== version) return;
        connectionError = "暂时无法联系自动研究服务，页面会自动重连。";
        render();
      } finally {
        if (current === version) schedule();
      }
    }

    async function run(kind, payload) {
      const current = ++version;
      clearTimeout(timer);
      action = kind;
      actionError = "";
      render();
      let uncertain = false;
      try {
        const result = await request("/api/auto/" + kind, payload);
        if (current !== version) return;
        if (kind === "start") pendingStart = null;
        apply(result);
      } catch (error) {
        if (current !== version) return;
        actionError = error.message || "操作未确认，正在重新读取任务。";
        uncertain = true;
      } finally {
        if (current === version) {
          action = "";
          render();
          if (uncertain) read();
          else schedule();
        }
      }
    }

    form.addEventListener("submit", event => {
      event.preventDefault();
      if (start.disabled || start.hidden) return;
      const payload = {pack_id: pack, rounds: Number(rounds.value), mode: mode.value};
      if (!pendingStart || Object.keys(payload).some(key => pendingStart[key] !== payload[key])) {
        pendingStart = {...payload, request_id: crypto.randomUUID()};
      }
      run("start", pendingStart);
    });
    mode.addEventListener("change", render);
    rounds.addEventListener("change", render);
    stop.addEventListener("click", () => {
      if (!stop.disabled && !stop.hidden) run("stop", {id: job.id, pack_id: pack});
    });
    render();

    return {
      setPack(id, canStart = true, prompt = "", hash = "") {
        if (prompt) promptSizes.set(id, visibleTextSize(prompt));
        displayedHash = hash;
        ready = true;
        permitted = canStart;
        if (pack === id) { render(); return; }
        ++version;
        clearTimeout(timer);
        pack = id;
        job = null;
        mode.value = new URLSearchParams(location.search).get("mode") === "mcp" ? "mcp" : "codex";
        rounds.value = "1";
        pendingStart = null;
        action = connectionError = actionError = "";
        ledger.href = "/api/auto/ledger?pack=" + encodeURIComponent(pack);
        render();
        read();
      },
      setReady(value) {
        ready = value;
        render();
      },
    };
  }

  return {attach};
})();
