'use strict';

(() => {
  const en = new Map([
    ['方格冲榜', 'Square Packing Challenge'], ['完整研究台 ↗', 'Research Lab ↗'],
    ['把 26 个方格压进最小正方形', 'Pack 26 unit squares into the smallest square'],
    ['方格边长都是 1。允许旋转和边角接触，包围边长越小，成绩越好.', 'Each square has side length 1. Rotations and edge or corner contact are allowed. A smaller enclosing square is better.'],
    ['方格边长都是 1。允许旋转和边角接触，包围边长越小，成绩越好。', 'Each square has side length 1. Rotations and edge or corner contact are allowed. A smaller enclosing square is better.'],
    ['摆放图', 'Packing board'], ['拖动方格或按回车选中', 'Drag a square or press Enter to select'],
    ['左转 2°', 'Rotate left 2°'], ['右转 2°', 'Rotate right 2°'], ['验分', 'Check score'],
    ['保存改进', 'Save improvement'], ['载入最好布局', 'Load best layout'], ['跟踪试摆', 'Follow trials'],
    ['正在读取本机布局。', 'Loading local layout.'], ['本地最好边长', 'Local best side'],
    ['公开最好边长', 'Published best side'], ['Codex 快跑', 'Codex sprint'],
    ['同一条 Codex 对话连续探索不同摆法，每轮复算并交回当前最好的候选。', 'Use one Codex chat to explore arrangements. Each round is checked and returns its best candidate.'],
    ['轮数', 'Rounds'], ['创建快跑', 'Create sprint'], ['停止', 'Stop'], ['进展', 'Progress'],
    ['候选与速度', 'Candidates and pace'], ['预计用时与日志 token', 'Estimated time and logged tokens'],
    ['等待实跑样本', 'Waiting for measured rounds'], ['非缓存输入', 'Uncached input'],
    ['模型输出', 'Model output'], ['缓存输入', 'Cached input'], ['会话日志合计', 'Session log total'],
    ['套餐余量由平台另行统计。', 'The platform tracks plan allowance separately.'],
    ['裁判回执、研究日志和生成的接手指令保留原文。', 'Judge receipts, research logs, and generated handoff prompts remain in their original language.'],
    ['复制到当前 Codex 对话，接手这局。', 'Copy this into your current Codex chat to take over this run.'],
    ['复制接手指令', 'Copy handoff prompt'], ['研究现场', 'Research activity'],
    ['实际试摆、研究说明和裁判反馈会显示在这里。', 'Actual trials, research notes, and judge feedback appear here.'],
    ['GitHub 公开榜', 'GitHub public board'], ['正在核对本地成绩。', 'Checking the local score.'],
    ['下载复核包', 'Download review bundle'], ['打开 GitHub 投稿草稿 ↗', 'Open GitHub submission draft ↗'],
    ['投稿规则 ↗', 'Submission rules ↗'], ['核对公开上界', 'Check published upper bound'],
    ['26 块方格棋盘。回车选择方格，方向键移动，Q 或 E 旋转。', 'Board of 26 squares. Press Enter to select, arrow keys to move, Q or E to rotate.'],
    ['本地成绩与公开参照', 'Local score and published reference'],
    ['本局会话 token 分项', 'Session token breakdown'],
    ['待创建', 'Not started'], ['这一关有其他对局', 'Another run is active for this puzzle'],
    ['用时待校准', 'Time not calibrated'], ['token 待校准', 'Tokens not calibrated'],
    ['等待绑定对话', 'Waiting for linked chat'], ['已完成。', 'Done.'],
    ['快跑进行中', 'Sprint running'], ['回到手动布局', 'Return to manual layout'],
    ['精确验分通过', 'Exact score verified'], ['当前纪录更紧。', 'The current record is tighter.'],
    ['可以保存。', 'You can save this.'], ['正在创建快跑…', 'Creating sprint…'],
    ['正在延长快跑…', 'Extending sprint…'], ['正在核对公开上界…', 'Checking published bound…'],
    ['接手指令已复制。', 'Handoff prompt copied.'],
    ['指令已选中，可以使用系统复制。', 'Prompt selected. Use the system copy command.'],
    ['本机服务暂时无法连接。', 'Local service is temporarily unavailable.'],
    ['本机服务暂时无法返回数据。', 'Local service did not return data.'],
    ['布局已修改，可以验分。', 'Layout changed. You can check its score.'],
    ['拖动方格，在验分后保存通过的改进。', 'Drag squares, check the score, then save a verified improvement.'],
    ['本地纪录已有更新。保存前请载入最新布局。', 'The local record changed. Load the latest layout before saving.'],
  ]);
  const patterns = [
    [/^约 (\d+) 分 (\d+) 秒$/, 'About $1 min $2 sec'],
    [/^约 (\d+) 秒$/, 'About $1 sec'],
    [/^(\d+) 轮$/, '$1 rounds'], [/^已选中方格 (\d+)$/, 'Square $1 selected'],
    [/^延长至 (\d+) 轮$/, 'Extend to $1 rounds'],
    [/^第 (\d+) 轮试摆 · 通过几何复算，边长 (.+)$/, 'Round $1 trial · geometry verified, side $2'],
    [/^第 (\d+) 轮试摆 · (.+)$/, 'Round $1 trial · $2'],
    [/^第 (\d+) 轮 · (.+)$/, 'Round $1 · $2'],
    [/^看试摆 · (.+)$/, 'View trial · $1'],
    [/^(\d+)\/(\d+) 轮 · (.+)$/, '$1/$2 rounds · $3'],
    [/^累计 ([\d,.]+) 个 · 本轮 ([\d,.]+) 个(.*)$/, 'Total $1 candidates · this round $2$3'],
    [/^累计 ([\d,.]+) 个$/, 'Total $1 candidates'],
    [/^最近核对 (.+)$/, 'Last checked $1'],
    [/^约 (.+)$/, 'About $1'], [/^(.+) 分 (\d+) 秒$/, '$1 min $2 sec'],
    [/^(\d+) 秒$/, '$1 sec'],
    [/^精确验分通过，边长 (.+)$/, 'Exact score verified, side $1'],
  ];
  const originals = new WeakMap(), shown = new WeakMap();
  const attrOriginals = new WeakMap(), attrShown = new WeakMap();
  let language;
  const requested = new URLSearchParams(location.search).get('lang');
  let saved = null;
  try { saved = localStorage.getItem('research-games-language'); } catch {}
  language = ['en', 'zh-CN'].includes(requested) ? requested
    : ['en', 'zh-CN'].includes(saved) ? saved
    : navigator.language?.toLowerCase().startsWith('en') ? 'en' : 'zh-CN';
  if (requested === 'en' || requested === 'zh-CN') {
    try { localStorage.setItem('research-games-language', requested); } catch {}
  }
  const translate = value => {
    const trimmed = value.trim();
    let result = en.get(trimmed);
    if (!result) for (const [pattern, replacement] of patterns) {
      if (pattern.test(trimmed)) { result = trimmed.replace(pattern, replacement); break; }
    }
    return result ? value.replace(trimmed, result) : value;
  };
  function textNode(node) {
    if (node.parentElement?.closest('script,style,textarea,[data-native],.language-switch')) return;
    if (!originals.has(node) || node.nodeValue !== shown.get(node)) originals.set(node, node.nodeValue);
    const desired = language === 'en' ? translate(originals.get(node)) : originals.get(node);
    shown.set(node, desired);
    if (node.nodeValue !== desired) node.nodeValue = desired;
  }
  function element(node) {
    if (node.closest('script,style,textarea,[data-native],.language-switch')) return;
    const original = attrOriginals.get(node) || {};
    const rendered = attrShown.get(node) || {};
    for (const name of ['aria-label', 'placeholder', 'title']) {
      if (!node.hasAttribute(name)) continue;
      const current = node.getAttribute(name);
      if (!(name in original) || current !== rendered[name]) original[name] = current;
      const desired = language === 'en' ? translate(original[name]) : original[name];
      rendered[name] = desired;
      if (current !== desired) node.setAttribute(name, desired);
    }
    attrOriginals.set(node, original); attrShown.set(node, rendered);
  }
  function walk(root) {
    if (root.nodeType === Node.TEXT_NODE) { textNode(root); return; }
    if (root.nodeType !== Node.ELEMENT_NODE) return;
    element(root);
    const walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
    while (walker.nextNode()) {
      if (walker.currentNode.nodeType === Node.TEXT_NODE) textNode(walker.currentNode);
      else element(walker.currentNode);
    }
  }
  function apply(next) {
    language = next;
    document.documentElement.lang = language;
    document.title = language === 'en' ? 'Square Packing Challenge · 26 squares' : '方格冲榜 · 26 块';
    for (const value of ['zh', 'en']) document.getElementById('lang-' + value).setAttribute('aria-pressed', String(value === (language === 'en' ? 'en' : 'zh')));
    walk(document.body);
  }
  function choose(value) {
    try { localStorage.setItem('research-games-language', value); } catch {}
    const url = new URL(location.href);
    url.searchParams.set('lang', value);
    history.replaceState(history.state, '', url.href);
    apply(value);
  }
  document.getElementById('lang-zh').addEventListener('click', () => choose('zh-CN'));
  document.getElementById('lang-en').addEventListener('click', () => choose('en'));
  new MutationObserver(records => {
    for (const record of records) {
      if (record.type === 'characterData') textNode(record.target);
      else if (record.type === 'attributes') element(record.target);
      else for (const node of record.addedNodes) walk(node);
    }
  }).observe(document.body, {subtree:true, childList:true, characterData:true,
    attributes:true, attributeFilter:['aria-label', 'placeholder', 'title']});
  apply(language);
})();

(() => {
  const el = id => document.getElementById(id);
  const canvas = el('drawing');
  const ink = canvas.getContext('2d');
  const scale = 1_000_000;
  const count = new Intl.NumberFormat('zh-CN', {maximumFractionDigits: 1});
  let state, draft, parent, projection, drag, selected = -1, dirty = false, verified = false;
  let fetching = false, changing = false, checking = false, revision = 0, retryStart = null;
  let following = true, pinned = null, trial = null, traceSignature = '';

  const say = (id, text, error = false) => {
    const node = el(id);
    node.textContent = text;
    node.classList.toggle('error', error);
  };

  async function call(path, payload) {
    const options = {cache: 'no-store'};
    if (payload !== undefined) {
      Object.assign(options, {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(payload)});
    }
    let response;
    try {
      response = await fetch('/api/lite/' + path, options);
    } catch {
      throw new Error('本机服务暂时无法连接。');
    }
    const data = await response.json().catch(() => null);
    if (!response.ok || !data || data.error) throw new Error(data?.error || '本机服务暂时无法返回数据。');
    return data;
  }

  const clone = poses => poses.map(pose => pose.slice());
  function duration(seconds) {
    const total = Math.ceil(seconds);
    return total >= 60 ? Math.floor(total / 60) + ' 分 ' + (total % 60) + ' 秒' : total + ' 秒';
  }

  function compactUpper(value) {
    const text = String(value || '—');
    const parts = text.split('.');
    return parts.length === 2 ? parts[0] + '.' + parts[1].slice(0, 9) : text;
  }

  function getPose([x, y, p, q]) {
    const denominator = q*q + p*p;
    return {x: x/scale, y: y/scale, c: (q*q-p*p)/denominator, s: (2*p*q)/denominator};
  }

  function corners(pose) {
    const {x, y, c, s} = getPose(pose);
    return [[-.5,-.5],[.5,-.5],[.5,.5],[-.5,.5]].map(([a,b]) => [x+a*c-b*s,y+a*s+b*c]);
  }

  function loadLayout() {
    if (!state) return;
    draft = clone(state.solution.poses);
    parent = state.score.hash;
    selected = -1;
    dirty = verified = false;
    say('selection', '拖动方格或按回车选中');
    say('board-message', '拖动方格，在验分后保存通过的改进。');
    draw();
    controls();
  }

  function draw() {
    if (!state || !draft) return;
    const {width, height} = canvas.getBoundingClientRect();
    if (width < 10 || height < 10) return;
    const dpr = window.devicePixelRatio || 1;
    canvas.width = Math.round(width*dpr);
    canvas.height = Math.round(height*dpr);
    ink.setTransform(dpr,0,0,dpr,0,0);
    const anchor = state.solution.poses.flatMap(corners);
    const left = Math.min(...anchor.map(v => v[0]));
    const bottom = Math.min(...anchor.map(v => v[1]));
    const side = Number(state.score.side);
    const margin = width < 450 ? 22 : 30;
    const unit = (Math.min(width,height)-2*margin) / side;
    const x = (width-side*unit)/2;
    const y = (height+side*unit)/2;
    projection = {left, bottom, unit, x, y};
    const project = ([a,b]) => [x+(a-left)*unit, y-(b-bottom)*unit];
    ink.fillStyle = '#fdfcf5';
    ink.strokeStyle = '#183f36';
    ink.lineWidth = 1.6;
    ink.fillRect(x,y-side*unit,side*unit,side*unit);
    ink.strokeRect(x,y-side*unit,side*unit,side*unit);
    const shown = trial?.solution?.poses || draft;
    const collision = trial?.collision || [];
    shown.forEach((pose, index) => {
      const points = corners(pose).map(project);
      const rotated = pose[2] > 0;
      ink.beginPath();
      ink.moveTo(...points[0]);
      for (const point of points.slice(1)) ink.lineTo(...point);
      ink.closePath();
      const marked = collision.includes(index+1) || (!trial && index === selected);
      ink.fillStyle = marked ? '#e7b79e' : (rotated ? '#9ac0a8' : '#d9e7d6');
      ink.strokeStyle = marked ? '#963a24' : '#3b725d';
      ink.lineWidth = marked ? 2 : 1;
      ink.fill();
      ink.stroke();
      ink.fillStyle = '#183c31';
      ink.font = (width < 440 ? '11' : '13') + 'px Consolas,monospace';
      ink.textAlign = 'center';
      ink.textBaseline = 'middle';
      const center = getPose(pose);
      ink.fillText(String(index+1), ...project([center.x,center.y]));
    });
  }

  function world(event) {
    const bounds = canvas.getBoundingClientRect();
    const p = projection;
    return [(event.clientX-bounds.left-p.x)/p.unit+p.left, (p.y-event.clientY+bounds.top)/p.unit+p.bottom];
  }

  function select(index) {
    selected = index;
    say('selection', index < 0 ? '拖动方格或按回车选中' : '已选中方格 ' + (index+1));
    controls();
    draw();
  }

  function changePose(pose) {
    draft[selected] = pose;
    dirty = true;
    verified = false;
    say('board-message', '布局已修改，可以验分。');
    draw();
    controls();
  }

  canvas.addEventListener('pointerdown', event => {
    if (!draft || !projection || event.button !== 0 || checking) return;
    manualView();
    const [x,y] = world(event);
    let index = draft.length - 1;
    for (; index >= 0; index--) {
      const tile = getPose(draft[index]);
      const dx = x-tile.x, dy = y-tile.y;
      if (Math.abs(dx*tile.c+dy*tile.s)<=.5 && Math.abs(-dx*tile.s+dy*tile.c)<=.5) break;
    }
    select(index);
    if (index < 0) return;
    canvas.focus({preventScroll:true});
    canvas.setPointerCapture(event.pointerId);
    drag = {id:event.pointerId, x, y, pose:draft[selected].slice()};
  });

  canvas.addEventListener('pointermove', event => {
    if (!drag || drag.id !== event.pointerId || !projection) return;
    const [x,y] = world(event);
    const pose = drag.pose.slice();
    pose[0] += Math.round((x-drag.x)*scale);
    pose[1] += Math.round((y-drag.y)*scale);
    if (pose[0] !== draft[selected][0] || pose[1] !== draft[selected][1]) changePose(pose);
  });
  for (const type of ['pointerup','pointercancel','lostpointercapture']) {
    canvas.addEventListener(type, event => { if (drag?.id === event.pointerId) drag = null; });
  }

  function rotate(degrees) {
    if (!draft || selected < 0 || checking) return;
    manualView();
    const [x,y,p,q] = draft[selected];
    const angle = (2*Math.atan(p/q)+degrees*Math.PI/180+Math.PI/2) % (Math.PI/2);
    const numerator = Math.round(Math.tan(angle/2)*10000);
    let a = numerator, b = 10000;
    while (b) [a,b] = [b,a % b];
    changePose([x,y,numerator/a,10000/a]);
  }

  canvas.addEventListener('keydown', event => {
    if (!draft || checking) return;
    if (['Enter','q','e','Q','E','ArrowLeft','ArrowRight','ArrowUp','ArrowDown'].includes(event.key)) manualView();
    if (event.key === 'Enter') {
      event.preventDefault();
      select((selected+1) % draft.length);
      return;
    }
    const step = event.shiftKey ? 10_000 : 1_000;
    const moves = {ArrowLeft:[-step,0],ArrowRight:[step,0],ArrowDown:[0,-step],ArrowUp:[0,step]};
    if (event.key.toLowerCase() === 'q' || event.key.toLowerCase() === 'e') {
      event.preventDefault();
      if (selected < 0) select(0);
      rotate(event.key.toLowerCase() === 'q' ? 2 : -2);
    } else if (moves[event.key]) {
      event.preventDefault();
      if (selected < 0) select(0);
      const [x,y,p,q] = draft[selected];
      const [dx,dy] = moves[event.key];
      changePose([x+dx,y+dy,p,q]);
    }
  });

  const active = () => !!state?.job?.id && state.job.status === 'running';
  function controls() {
    for (const name of ['rotate-left','rotate-right']) el(name).disabled = selected < 0 || checking;
    el('check').disabled = !draft || !dirty || checking || changing;
    el('save').disabled = !verified || checking || changing || parent !== state?.score?.hash;
    el('reset').disabled = !draft || checking;
    const running = active();
    const target = Number(el('rounds').value);
    for (const option of el('rounds').options) option.disabled = running && Number(option.value) < state.job.rounds;
    el('rounds').disabled = changing;
    el('start').disabled = !state || changing || state.busy || (running && target <= state.job.rounds);
    say('start', running ? (target > state.job.rounds ? '延长至 ' + target + ' 轮' : '快跑进行中') : '创建快跑');
    el('stop').hidden = !active();
    el('stop').disabled = changing;
    el('sync').disabled = !state || changing;
  }

  function manualView() {
    if (!trial && !following) return;
    following = false;
    pinned = null;
    renderTrace();
  }

  function renderTrace() {
    const recent = (state?.trace || []).slice(-6).reverse();
    const drawable = recent.find(event => event.solution);
    const shown = pinned && recent.find(event => event.at === pinned);
    trial = shown || (following ? drawable : null);
    el('follow').hidden = !drawable;
    el('follow').textContent = trial ? '回到手动布局' : '跟踪试摆';
    el('follow').setAttribute('aria-pressed', String(Boolean(trial)));
    el('trial-status').hidden = !trial;
    if (trial) {
      const detail = trial.valid ? '通过几何复算，边长 ' + trial.side : trial.error;
      say('trial-status','第 ' + trial.turn + ' 轮试摆 · ' + detail,!trial.valid);
    }
    el('trace-empty').hidden = recent.length > 0;
    const signature = recent.map(entry => entry.at).join(',') + '|' + (trial?.at || '');
    if (traceSignature !== signature) {
      traceSignature = signature;
      el('trace-list').replaceChildren(...recent.map(entry => {
        const item = document.createElement('li');
        const heading = document.createElement('strong');
        const note = document.createElement('p');
        heading.textContent = '第 ' + entry.turn + ' 轮 · ' + entry.stage;
        note.textContent = entry.summary;
        item.append(heading, note);
        if (entry.solution) {
          const button = document.createElement('button');
          button.type = 'button';
          button.textContent = entry.valid ? '看试摆 · ' + entry.side : '看试摆 · 碰撞';
          button.setAttribute('aria-pressed',String(trial?.at === entry.at));
          button.addEventListener('click', () => {
            pinned = entry.at;
            following = false;
            renderTrace();
            el('drawing').scrollIntoView({block:'nearest'});
          });
          item.append(button);
        }
        return item;
      }));
    }
    draw();
  }

  function estimate() {
    if (!state) return;
    const target = Number(el('rounds').value);
    const rounds = active() ? Math.max(1,Math.max(state.job.rounds,target)-state.job.completed) : target;
    const {seconds_per_round: sec, tokens_per_round: tokens} = state.estimate;
    const time = sec == null ? '用时待校准' : '约 ' + duration(rounds*sec);
    const cost = tokens == null ? 'token 待校准' : '约 ' + count.format(Math.round(rounds*tokens)) + ' token';
    const timeLabel = document.createElement('span');
    timeLabel.textContent = time;
    const costLabel = document.createElement('span');
    costLabel.textContent = cost;
    el('estimate').replaceChildren(timeLabel, ' · ', costLabel);
  }

  function renderUsage(usage) {
    const uncached = usage ? usage.input_tokens - usage.cached_input_tokens : null;
    for (const [id, value] of Object.entries({
      'usage-uncached': uncached,
      'usage-output': usage?.output_tokens,
      'usage-cached': usage?.cached_input_tokens,
      usage: usage?.total_tokens,
    })) {
      say(id, Number.isInteger(value) && value >= 0 ? count.format(value) : '—');
    }
    if (!usage && active()) say('usage', '等待绑定对话');
  }

  function render(next) {
    const previous = state?.job;
    state = next;
    say('score', next.score.side);
    say('target', compactUpper(next.reference.upper));
    el('target').title = next.reference.upper || '';
    say('eligibility', next.submission.message);
    el('bundle').hidden = false;
    el('bundle').download = 'square-26-review.zip';
    el('issue').hidden = !next.submission.eligible || !next.submission.issue_url;
    if (!el('issue').hidden) el('issue').href = next.submission.issue_url;
    const stamp = next.reference.math_checked_at ? new Date(next.reference.math_checked_at) : null;
    say('refreshed', stamp && Number.isFinite(stamp.valueOf()) ? '最近核对 ' + stamp.toLocaleString('zh-CN') : '');

    const job = next.job;
    if (active() && (job.id !== previous?.id || job.rounds !== previous.rounds)) el('rounds').value = String(job.rounds);
    say('progress', job.id ? job.completed + '/' + job.rounds + ' 轮 · ' + job.stage : (next.busy ? '这一关有其他对局' : '待创建'));
    const checked = job.work?.checked;
    const settled = (job.receipts || []).reduce((sum, receipt) => sum + (receipt.work?.checked || 0), 0);
    const perSecond = checked > 0 && job.turn_active_seconds >= 1 ? ' · ' + count.format(checked/job.turn_active_seconds) + ' 个/秒' : '';
    const cumulative = job.id ? '累计 ' + count.format(settled + (checked || 0)) + ' 个' : '—';
    say('pace', checked == null ? cumulative : cumulative + ' · 本轮 ' + count.format(checked) + ' 个' + perSecond);
    renderUsage(next.usage);
    say('receipt', job.receipts?.at(-1)?.message || job.message || '');
    el('guide').hidden = !active();
    if (active()) {
      el('prompt').value = '用 research_games MCP 接手方格冲榜 square-26 的既有快跑对局 ' + job.id
        + '。claim_turn 用当前 CODEX_THREAD_ID 绑定实测用量。每轮根据上一次裁判回执调整研究假设并验证候选，用 report_packing_trial 展示研究摘要和真实试摆，'
        + '再经 submit_turn 交回每轮最佳候选。继续到本局完成或平台明确提示套餐耗尽。';
    }
    if (!draft || (!dirty && parent !== next.score.hash)) loadLayout();
    else if (dirty && parent !== next.score.hash) say('board-message', '本地纪录已有更新。保存前请载入最新布局。', true);
    renderTrace();
    controls();
    estimate();
  }

  async function poll() {
    if (fetching || changing) {
      setTimeout(poll, 500);
      return;
    }
    fetching = true;
    const marker = revision;
    try {
      const next = await call('state');
      if (marker === revision) render(next);
    } catch (error) {
      if (!state && marker === revision) say('board-message', error.message, true);
    } finally {
      fetching = false;
      setTimeout(poll, active() ? 1300 : 4000);
    }
  }

  async function mutate(path, payload, feedback, pending) {
    if (changing) return;
    changing = true;
    revision++;
    controls();
    if (pending) say(feedback, pending);
    try {
      const next = await call(path, payload);
      if (path === 'save') dirty = verified = false;
      render(next);
      say(feedback, next.message || '已完成。');
      return true;
    } catch (error) {
      say(feedback, error.message, true);
      return false;
    } finally {
      changing = false;
      controls();
    }
  }

  function editableSolution() {
    const minX = Math.min(...draft.map(pose => pose[0]));
    const minY = Math.min(...draft.map(pose => pose[1]));
    return {poses:draft.map(([x,y,p,q]) => [x-minX,y-minY,p,q])};
  }

  el('rotate-left').addEventListener('click', () => rotate(2));
  el('rotate-right').addEventListener('click', () => rotate(-2));
  el('reset').addEventListener('click', () => { manualView(); loadLayout(); });
  el('follow').addEventListener('click', () => {
    if (trial) manualView();
    else {
      following = true;
      pinned = null;
      renderTrace();
    }
  });
  el('rounds').addEventListener('change', () => { controls(); estimate(); });
  el('check').addEventListener('click', async () => {
    checking = true;
    controls();
    try {
      const rated = await call('check', {solution: editableSolution()});
      verified = rated.side_ticks < state.score.side_ticks;
      say('board-message', '精确验分通过，边长 ' + rated.side + (verified ? '。可以保存。' : '。当前纪录更紧。'));
    } catch (error) {
      verified = false;
      say('board-message', error.message, true);
    } finally {
      checking = false;
      controls();
    }
  });
  el('save').addEventListener('click', () => mutate('save', {solution:editableSolution(),parent_hash:parent}, 'board-message'));
  el('start').addEventListener('click', async () => {
    const rounds = Number(el('rounds').value);
    if (active()) {
      await mutate('extend', {id:state.job.id,rounds}, 'run-message', '正在延长快跑…');
      return;
    }
    if (retryStart?.rounds !== rounds) {
      retryStart = {rounds,request_id:globalThis.crypto?.randomUUID?.() || 'lite-' + Date.now()};
    }
    if (await mutate('start', retryStart, 'run-message', '正在创建快跑…')) {
      retryStart = null;
      el('copy').focus();
    }
  });
  el('stop').addEventListener('click', () => mutate('stop', {id:state.job.id}, 'run-message'));
  el('sync').addEventListener('click', () => mutate('sync', {}, 'sync-message', '正在核对公开上界…'));
  el('copy').addEventListener('click', async () => {
    try {
      await navigator.clipboard.writeText(el('prompt').value);
      say('run-message', '接手指令已复制。');
    } catch {
      el('prompt').focus();
      el('prompt').select();
      say('run-message', '指令已选中，可以使用系统复制。');
    }
  });

  new ResizeObserver(draw).observe(canvas);
  void poll();
})();
