(() => {
  'use strict';

  const byId = id => document.getElementById(id);
  const form = byId('batchForm');
  if (!form) return;

  const endpoint = '/api/packing/batch';
  const statusLabels = {
    waiting: '等 Codex 首轮策略', queued: '排队中', running: '搜索中', review: '等下一轮策略',
    done: '已完成', stopped: '已停止', failed: '失败', paused: '已暂停',
  };
  const activeStates = new Set(['waiting', 'queued', 'running', 'review', 'paused']);
  const numbers = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 2});
  const precise = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 6, useGrouping: false});
  const rowNodes = new Map();
  const floorNodes = [];
  const notifiedScores = new Map();
  const strategyLabels = new Map();
  let requestedBatch = new URLSearchParams(location.search).get('batch');
  let batch = null;
  let packSignature = '';
  let ready = false;
  let modifying = false;
  let disconnected = false;
  let fetching = false;
  let mutationVersion = 0;
  let previewVersion = 0;
  let previewTimer;
  let retryStart;
  let selectedFloor = null;

  function setText(node, text) {
    if (node.textContent !== text) node.textContent = text;
  }

  const put = (id, text) => setText(byId(id), text);
  const isNumber = value => value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
  const count = value => isNumber(value) ? numbers.format(value) : '—';
  const side = value => isNumber(value) ? precise.format(value) : '—';
  const percent = value => isNumber(value) ? `${count(value)}%` : '—';

  function duration(value) {
    if (!isNumber(value) || Number(value) < 0) return '校准中';
    const seconds = Math.ceil(Number(value));
    const hours = Math.floor(seconds / 3600);
    const minutes = Math.floor(seconds % 3600 / 60);
    const rest = seconds % 60;
    const parts = [];
    if (hours) parts.push(`${hours} 小时`);
    if (minutes) parts.push(`${minutes} 分`);
    if (rest || !parts.length) parts.push(`${rest} 秒`);
    return parts.join(' ');
  }

  async function request(path = '', data) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), 6000);
    const options = {cache: 'no-store', signal: controller.signal};
    if (data) Object.assign(options, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(data)});
    try {
      const response = await fetch(endpoint + path, options);
      const body = await response.json().catch(() => null);
      if (!response.ok || !body || body.error) throw new Error(body?.error || '本地批次服务暂时无法读取。');
      return body;
    } catch (error) {
      if (error.name === 'AbortError' || error instanceof TypeError) throw new Error('本地服务未响应，请重试。');
      throw error;
    } finally {
      clearTimeout(timer);
    }
  }

  function message(text, error = false) {
    put('batchMessage', text);
    byId('batchMessage').classList.toggle('batch-error', error);
  }

  function selection() {
    const packs = [...byId('batchPacks').querySelectorAll('input:checked')].map(input => input.value);
    const chosen = Number(form.elements['batch-parallel'].value);
    return {packs, rounds: Number(form.elements['batch-rounds'].value), parallel: Math.min(chosen, packs.length)};
  }

  function updateControls() {
    const {packs} = selection();
    const active = Boolean(batch && activeStates.has(batch.status));
    ['batchChoices', 'batchRounds', 'batchLanes'].forEach(id => { byId(id).disabled = !ready || modifying || active; });
    for (const radio of form.elements['batch-parallel']) radio.disabled = Number(radio.value) > Math.max(1, packs.length);
    byId('batchStart').disabled = !ready || modifying || disconnected || !packs.length || Boolean(active);
    put('batchStart', active ? '已有批次在执行' : '创建搜索批次');
    byId('batchStopAll').hidden = !active;
    byId('batchStopAll').disabled = modifying;
    byId('batchRetry').hidden = !disconnected;
    byId('batchRetry').disabled = fetching;
    rowNodes.forEach(({stop}) => { stop.disabled = modifying; });
  }

  function renderPacks(packs, defaults) {
    const signature = JSON.stringify(packs);
    if (packSignature === signature) return;
    const chosen = packSignature ? selection().packs : defaults;
    packSignature = signature;
    byId('batchPacks').replaceChildren(...packs.map(pack => {
      const label = document.createElement('label');
      const checkbox = document.createElement('input');
      checkbox.type = 'checkbox';
      checkbox.value = pack.id;
      checkbox.disabled = pack.open === false;
      checkbox.checked = !checkbox.disabled && chosen.includes(pack.id);
      label.append(checkbox, `${pack.n} 块`);
      if (checkbox.disabled) label.title = '此题尚未开放';
      return label;
    }));
    fitParallel();
    queuePreview();
  }

  function fitParallel() {
    const {parallel, packs} = selection();
    if (packs.length) form.elements['batch-parallel'].value = String(parallel);
    updateControls();
  }

  function tokenEstimate(estimate) {
    const {token_min: min, token_max: max} = estimate || {};
    if (!isNumber(min) || !isNumber(max)) return '校准中';
    const amount = Number(min) === Number(max) ? count(min) : `${count(min)}–${count(max)}`;
    return `${amount} tokens`;
  }

  function queuePreview() {
    const version = ++previewVersion;
    clearTimeout(previewTimer);
    const payload = selection();
    if (batch && activeStates.has(batch.status)) {
      put('batchEstimateTime', duration(batch.estimate?.seconds));
      put('batchEstimateUsage', tokenEstimate(batch.estimate));
      return;
    }
    put('batchEstimateTime', payload.packs.length ? '校准中' : '请先选择题目');
    put('batchEstimateUsage', payload.packs.length ? '校准中' : '—');
    if (!payload.packs.length) return;
    previewTimer = setTimeout(async () => {
      try {
        const params = new URLSearchParams({rounds: payload.rounds, parallel: payload.parallel});
        for (const pack of payload.packs) params.append('pack', pack);
        const estimate = await request('/estimate?' + params.toString());
        if (version !== previewVersion) return;
        put('batchEstimateTime', duration(estimate.seconds));
        put('batchEstimateUsage', tokenEstimate(estimate));
      } catch {
        if (version !== previewVersion) return;
        put('batchEstimateTime', '暂未取得估计');
        put('batchEstimateUsage', '暂未取得估计');
      }
    }, 180);
  }

  function badge(node, status) {
    node.dataset.status = status || 'idle';
    setText(node, statusLabels[status] || '可创建批次');
  }

  function caseLink(pack) {
    const url = new URL(location.href);
    url.searchParams.set('pack', pack);
    url.hash = 'problemTitle';
    return url.pathname + url.search + url.hash;
  }

  function makeRow(pack) {
    const article = document.createElement('article');
    article.className = 'batch-case';
    article.dataset.batchRow = pack;
    const heading = document.createElement('div');
    heading.className = 'batch-case-head';
    const title = document.createElement('a');
    title.addEventListener('click', event => {
      if (event.button !== 0 || event.metaKey || event.ctrlKey || event.shiftKey || event.altKey) return;
      const picker = byId('pack');
      if (picker.disabled || ![...picker.options].some(option => option.value === pack)) return;
      event.preventDefault();
      if (picker.value !== pack) {
        picker.value = pack;
        picker.dispatchEvent(new Event('change'));
      }
      byId('problemTitle').scrollIntoView({block:'start'});
    });
    const state = document.createElement('span');
    state.className = 'batch-badge';
    heading.append(title, state);
    const stats = document.createElement('dl');
    stats.className = 'batch-stats';
    const fields = ['策略', '当前阶段', '累计候选数', '搜索速度', '最优边长', '距公开上界', '本关剩余', '本关用时'];
    const values = fields.map((field, index) => {
      const group = document.createElement('div');
      const term = document.createElement('dt');
      const value = document.createElement('dd');
      term.textContent = field;
      if (index === 4 || index === 5) value.className = 'batch-side';
      group.append(term, value);
      stats.append(group);
      return value;
    });
    const stop = document.createElement('button');
    stop.type = 'button';
    stop.className = 'btn secondary-btn batch-stop batch-danger';
    stop.textContent = '停止此关';
    stop.addEventListener('click', () => stopBatch(pack));
    const receipt = document.createElement('p');
    receipt.className = 'batch-receipt';
    const proof = document.createElement('a');
    proof.className = 'batch-proof';
    proof.textContent = '下载新上界候选的复核材料 ↓';
    article.append(heading, stats, stop, receipt, proof);
    byId('batchRows').append(article);
    const nodes = {article, title, state, values, stop, receipt, proof};
    rowNodes.set(pack, nodes);
    return nodes;
  }

  function formatGap(ticks) {
    if (!isNumber(ticks)) return '待核对';
    const gap = Number(ticks);
    if (gap === 0) return '已达到';
    return `${gap > 0 ? '+' : '−'}${precise.format(Math.abs(gap) / 1000000)}`;
  }

  function rowRemaining(row) {
    if (row.status === 'waiting') return '待接手';
    if (row.status === 'review') return '待下轮策略';
    if (!activeStates.has(row.status)) return '—';
    if (row.status === 'queued' && !isNumber(row.remaining_seconds)) return '待排入';
    return row.status === 'paused' ? '暂停计时' : duration(row.remaining_seconds);
  }

  function renderRow(row) {
    const {article, title, state, values, stop, receipt, proof} = rowNodes.get(row.pack_id) || makeRow(row.pack_id);
    article.dataset.status = row.status;
    title.href = caseLink(row.pack_id);
    setText(title, `${row.n} 块方格`);
    badge(state, row.status);
    stop.hidden = !activeStates.has(row.status);
    stop.setAttribute('aria-label', `停止 ${row.n} 块方格`);
    const strategy = strategyLabels.get(row.strategy) || row.strategy || '待 Codex 安排';
    const stage = row.stage || statusLabels[row.status] || '—';
    const round = isNumber(row.turn) && isNumber(row.rounds) && Number(row.turn) > 0 ? `第 ${row.turn}/${row.rounds} 轮` : '';
    const rate = isNumber(row.rate) && Number(row.rate) > 0 ? `${count(row.rate)} 个/秒` : '校准中';
    const remaining = rowRemaining(row);
    const progress = isNumber(row.progress_pct) ? ` · ${percent(row.progress_pct)}` : '';
    const label = [round, stage].filter(Boolean).join(' · ');
    const metrics = [strategy, label + progress, count(row.total_checked), rate, side(row.best_side), formatGap(row.gap_ticks), remaining, duration(row.elapsed_seconds)];
    metrics.forEach((text, index) => setText(values[index], text));
    const latestReceipt = row.receipts?.at(-1);
    const receiptCount = latestReceipt ? `最近回执 · 第 ${count(latestReceipt.turn)} 轮复核 ${count(latestReceipt.checked)} 个候选` : '';
    const note = row.note || latestReceipt?.note;
    setText(receipt, [receiptCount, row.improved ? '本批已保存新纪录' : '', note].filter(Boolean).join(' · '));
    proof.hidden = !row.proof_url?.startsWith(`${endpoint}/proof?`);
    if (!proof.hidden) {
      proof.href = row.proof_url;
      proof.download = `${row.pack_id}-public-candidate.json`;
    }
    const score = row.best_hash || String(row.best_side);
    if (notifiedScores.get(row.pack_id) !== score) {
      notifiedScores.set(row.pack_id, score);
      if (typeof refreshAutoPack === 'function') void refreshAutoPack(row.pack_id);
    }
  }

  function renderUsage(usage) {
    const data = [usage?.input_tokens, usage?.cached_input_tokens, usage?.output_tokens, usage?.total_tokens];
    if (!data.some(isNumber)) {
      put('batchUsage', '会话实测用量 · 等待绑定 Codex 会话或回传用量');
      return;
    }
    const [input, cached, output, total] = data.map(count);
    put('batchUsage', `会话实测用量 · 输入 ${input}（其中缓存命中 ${cached}） · 输出 ${output} · 合计 ${total} tokens`);
  }

  function floorDescription(floor, index) {
    const design = `${count(floor.n)} 块方格，第 ${count(floor.turn)} 轮`;
    const measure = `边长 ${side(floor.before_side)} → ${side(floor.after_side)}，缩短 ${side(Number(floor.saved_ticks) / 1000000)}`;
    return `第 ${index + 1} 层 · ${design} · ${measure}${floor.public_candidate ? ' · 新上界候选，待独立复核' : ''}`;
  }

  function showFloor(index) {
    selectedFloor = index;
    floorNodes.forEach((entry, i) => entry.button.setAttribute('aria-pressed', String(i === selectedFloor)));
    put('batchFloorDetail', selectedFloor === null ? '' : floorNodes[selectedFloor].description);
  }

  function renderTower(floors, savedTicks, previousId) {
    let sameBatch = previousId === batch?.id;
    if (!sameBatch || floorNodes.length > floors.length) {
      floorNodes.length = 0;
      byId('batchTower').replaceChildren();
      selectedFloor = null;
      sameBatch = false;
    }
    const priorCount = floorNodes.length;
    const wasFollowing = selectedFloor === null || selectedFloor === priorCount - 1;
    for (let index = priorCount; index < floors.length; index++) {
      const floor = floors[index];
      const item = document.createElement('li');
      const button = document.createElement('button');
      const number = document.createElement('span');
      number.textContent = String(index + 1);
      button.type = 'button';
      button.className = 'batch-floor';
      if (sameBatch) button.classList.add('batch-floor-new');
      if (floor.public_candidate) button.dataset.publicCandidate = '';
      const description = floorDescription(floor, index);
      button.title = description;
      button.setAttribute('aria-label', description);
      button.addEventListener('click', () => showFloor(index));
      button.append(number);
      item.append(button);
      byId('batchTower').append(item);
      floorNodes.push({button, description});
    }
    if (wasFollowing && floors.length > priorCount) {
      showFloor(floors.length - 1);
      byId('batchTowerScroll').scrollTop = 0;
    } else if (!floors.length) showFloor(null);
    put('batchFloorCount', `${count(floors.length)} 层`);
    put('batchImproved', floors.length && isNumber(savedTicks) ? `经验证累计缩短 ${side(Number(savedTicks) / 1000000)} 单位` : '');
  }

  function render(data) {
    ready = true;
    const previousId = batch?.id;
    const wasActive = batch && activeStates.has(batch.status);
    batch = data.batch || null;
    const active = batch && activeStates.has(batch.status);
    (data.strategies || []).forEach(({id, label}) => strategyLabels.set(id, label));
    renderPacks(data.packs || [], data.default_packs || []);
    badge(byId('batchState'), batch?.status);
    byId('batchResults').hidden = !batch;
    byId('batchTakeover').hidden = !batch || !activeStates.has(batch.status);
    renderTower(batch?.floors || [], batch?.saved_ticks, previousId);
    if (previousId !== batch?.id) {
      byId('batchRows').replaceChildren();
      rowNodes.clear();
      notifiedScores.clear();
    }
    if (active && previousId !== batch.id) {
      const currentPacks = new Set(batch.rows.map(row => row.pack_id));
      byId('batchPacks').querySelectorAll('input').forEach(input => { input.checked = currentPacks.has(input.value); });
      form.elements['batch-rounds'].value = String(batch.rounds);
      form.elements['batch-parallel'].value = String(batch.parallel);
      fitParallel();
    }
    if (active || wasActive) queuePreview();
    if (batch) {
      const prompt = `用 research_games MCP 接手方格并行批次 ${batch.id}。从 packing_batch_status 读每关状态，使用当前普通套餐并为每关下发策略，直到这一批完成。`;
      const promptBox = byId('batchPrompt');
      if (promptBox.value !== prompt) promptBox.value = prompt;
      const eta = activeStates.has(batch.status) ? duration(batch.eta_seconds) : '—';
      put('batchTiming', `${count(batch.rows.length)} 关 · ${count(batch.parallel)} 关并行 · 已用 ${duration(batch.elapsed_seconds)} · 预计剩余 ${eta}`);
      put('batchSearchProgress', `搜索进度 ${percent(batch.progress_pct)} · 已交 ${count(batch.completed_rounds)}/${count(batch.planned_rounds)} 轮 · 已检查 ${count(batch.total_checked)} 个候选`);
      renderUsage(batch.usage);
      batch.rows.forEach(renderRow);
    }
    updateControls();
  }

  function latestLink() {
    const url = new URL(location.href);
    url.searchParams.delete('batch');
    byId('batchLatest').href = url.pathname + url.search;
    byId('batchLatest').hidden = !requestedBatch;
  }

  async function poll() {
    if (modifying || fetching) return;
    const version = mutationVersion;
    const reconnecting = disconnected || !ready;
    fetching = true;
    updateControls();
    try {
      const path = requestedBatch ? `?batch_id=${encodeURIComponent(requestedBatch)}` : '';
      const data = await request(path);
      if (version !== mutationVersion) return;
      disconnected = false;
      const previousMessage = batch?.message;
      render(data);
      if (reconnecting || (batch && batch.message !== previousMessage)) {
        message(batch?.message || data.message || (batch ? '已连接本地批次。' : '选择题目后创建批次。'));
      }
    } catch (error) {
      if (version !== mutationVersion) return;
      disconnected = true;
      message(`${error.message} ${batch ? '正在显示上次收到的进展。' : ''}`.trim(), true);
    } finally {
      fetching = false;
      updateControls();
    }
  }

  async function changeBatch(path, payload, pending, success) {
    if (modifying) return;
    mutationVersion++;
    modifying = true;
    updateControls();
    message(pending);
    try {
      const data = await request(path, payload);
      disconnected = false;
      render(data);
      message(data.message || batch?.message || success);
      return true;
    } catch (error) {
      message(error.message, true);
      return false;
    } finally {
      modifying = false;
      updateControls();
    }
  }

  function stopBatch(pack) {
    if (!batch) return;
    void changeBatch('/stop', {batch_id: batch.id, pack_id: pack}, '正在发送停止指令…', '已发出停止指令。');
  }

  form.addEventListener('change', () => {
    fitParallel();
    queuePreview();
  });
  form.addEventListener('submit', async event => {
    event.preventDefault();
    if (byId('batchStart').disabled) return;
    const config = selection();
    const signature = JSON.stringify(config);
    if (retryStart?.signature !== signature) {
      const id = globalThis.crypto?.randomUUID?.() || `${Date.now()}-${Math.random().toString(36).slice(2)}`;
      retryStart = {signature, id};
    }
    const ok = await changeBatch('/start', {...config, request_id: retryStart.id}, '正在创建批次…', '批次已创建，等待 Codex 接手。');
    if (!ok || !batch) return;
    retryStart = null;
    requestedBatch = null;
    const url = new URL(location.href);
    url.searchParams.delete('batch');
    history.replaceState(null, '', url.pathname + url.search + url.hash);
    latestLink();
    byId('batchCopy').focus();
  });

  byId('batchStopAll').addEventListener('click', () => stopBatch(null));
  byId('batchRetry').addEventListener('click', () => { void poll(); });
  byId('batchCopy').addEventListener('click', async () => {
    const prompt = byId('batchPrompt');
    try {
      await navigator.clipboard.writeText(prompt.value);
      message('已复制接手指令，请粘贴到当前 Codex 会话。');
    } catch {
      prompt.focus();
      prompt.select();
      message('已选中接手指令，可以使用系统复制。');
    }
  });

  latestLink();
  void poll();
  setInterval(() => { if (!document.hidden) void poll(); }, 1500);
  document.addEventListener('visibilitychange', () => { if (!document.hidden) void poll(); });
})();
