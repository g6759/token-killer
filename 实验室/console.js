"use strict";

(() => {
  const el = id => document.getElementById(id);
  const tr = (zh,en) => document.documentElement.lang === "en" ? en : zh;
  const scenario = el("run-scenario"), duration = el("run-duration");
  const choices = [
    ["single","方格子容纳 · 单路","Square packing · one lane"],
    ["mixed","方格＋接力 · 双路","Square and relay · two lanes"],
    ["relay-frontier-1152-40","四境驿站 · 40 路","Four Realms Relay · 40 lanes"],
    ["square-frontier-1152-40","方格子容纳 · 40 路","Square packing · 40 lanes"],
    ...[4,5,6,7,8].map(n => [n===4 ? "cap-set" : "cap-set-"+n+"d","Cap Set · "+n+"D","Cap Set · "+n+"D"]),
    ...[8,12,16].map(n => [n===8 ? "golomb-ruler" : "golomb-ruler-"+n,"Golomb 尺 · "+n+" 刻度","Golomb ruler · "+n+" marks"]),
    ["lorenz-divergence","超维递归模拟器 · 单路","Hyperdimensional Recursive Simulator · one lane"],
  ];
  const labels = {
    "console-title":["运行控制台","Run console"], "scenario-label":["玩法","Game"],
    "duration-label":["运行时长","Run length"], "run-start":["开始运行","Start run"],
    "run-stop":["停止运行","Stop run"], "manage-plan":["管理订阅","Manage subscription"],
    "allowance-title":["共享套餐余量","Shared plan allowance"], "refresh-allowance":["刷新","Refresh"],
    "speed-label":["有效 token / 分钟","Effective tokens / min"],
    "total-label":["本场累计有效 token","Session effective tokens"],
    "time-label":["当前窗口剩余","Time left in window"],
  };
  const statusLabels = {idle:["就绪","Idle"],running:["运行中","Running"],stopping:["正在停止","Stopping"],error:["已中断","Interrupted"]};
  const query = new URLSearchParams(location.search);
  let state, pending = "", notice = null, selected = choices.some(c=>c[0]===query.get("scenario")) ? query.get("scenario") : "single";
  function format(value) {
    return Number.isFinite(value) ? new Intl.NumberFormat(document.documentElement.lang,{maximumFractionDigits:1}).format(value) : "—";
  }
  function clock(value) {
    const secs = Math.max(0,Math.ceil(value));
    return Math.floor(secs/60) + ":" + String(secs%60).padStart(2,"0");
  }
  function timeAt(value) {
    if (value == null) return "";
    const date = new Date(typeof value==="number" && value<1e12 ? value*1000 : value);
    return Number.isNaN(date.valueOf()) ? "" : new Intl.DateTimeFormat(document.documentElement.lang,{month:"short",day:"numeric",hour:"2-digit",minute:"2-digit"}).format(date);
  }
  function renderAllowance() {
    const panel = el("allowance-windows"), allowance = state?.allowance;
    panel.replaceChildren();
    for (const w of allowance?.windows || []) {
      if (!Number.isFinite(w.remaining_percent)) continue;
      const item=document.createElement("div"), title=document.createElement("span"), amount=document.createElement("strong");
      item.className="quota-window";
      title.textContent=w.minutes===10080 ? tr("周窗口","Weekly window") : w.minutes===300 ? tr("5 小时窗口","5-hour window") : format(w.minutes)+tr(" 分钟窗口","-minute window");
      amount.textContent=format(w.remaining_percent)+"%";
      const bar=document.createElement("progress");bar.max=100;bar.value=w.remaining_percent;bar.setAttribute("aria-label",title.textContent);
      item.append(title,amount,bar);
      const reset=timeAt(w.resets_at);
      if(reset) { const small=document.createElement("small");small.textContent=tr("重置于 ","Resets ")+reset;item.append(small); }
      panel.append(item);
    }
    const sampled=timeAt(allowance?.sampled_at);
    el("allowance-detail").textContent=(allowance?.status==="available" ? tr("可用普通套餐。","Ordinary plan usage is available.")
      : allowance?.status==="blocked" ? tr("套餐已受限，实跑已锁定。","Plan allowance is restricted. Live runs are locked.")
      : tr("普通套餐余量待确认，实跑前会重新检查。","Plan allowance is unconfirmed. It is checked before a live run."))
      + (sampled ? tr(" 最近读取 "," Last checked ")+sampled : "");
  }
  function render() {
    const busy = state?.run==="running" || state?.run==="stopping";
    const session=state?.continuous, progress=state?.progress;
    for(const [id,pair] of Object.entries(labels)) el(id).textContent=tr(...pair);
    const length=duration.value || "1";
    const game=(busy && choices.some(c=>c[0]===session?.scenario)) ? session.scenario : selected;
    if(scenario.dataset.lang!==document.documentElement.lang || !scenario.length) {
      scenario.replaceChildren(...choices.map(([id,zh,en])=>new Option(tr(zh,en),id)));
      duration.replaceChildren(...[1,2,3].map(n=>new Option(n*10+tr(" 分钟"," minutes"),String(n))));
      scenario.dataset.lang=document.documentElement.lang;
    }
    scenario.value=game;duration.value=busy && [1,2,3].includes(session?.window_limit) ? String(session.window_limit) : length;
    scenario.disabled=duration.disabled=Boolean(busy || pending==="session");
    const lanes=game.endsWith("-40") ? 40 : game==="mixed" ? 2 : 1;
    el("run-description").textContent=tr("每窗 10 分钟，","10-minute windows. ")+lanes+tr(" 路同时运行。"," concurrent lane"+(lanes===1?"":"s")+".")
      +(lanes===40 ? tr(" 高并发会更快消耗共享额度。"," High concurrency can consume shared allowance faster.") : "");
    const canStart=state?.subscription?.ready && state?.allowance?.status!=="blocked";
    el("run-start").hidden=Boolean(busy);
    el("run-start").disabled=!canStart || Boolean(pending);
    if(pending==="session") el("run-start").textContent=tr("检查套餐中…","Checking plan…");
    el("run-stop").hidden=!busy;
    el("run-stop").disabled=state?.run==="stopping" || pending==="stop";
    el("refresh-allowance").disabled=Boolean(pending);
    if(pending==="quota") el("refresh-allowance").textContent=tr("读取中…","Reading…");
    el("console-state").textContent=tr(...(statusLabels[state?.run] || ["连接中","Connecting"]));
    if (!busy && session?.end_reason==="quota") el("console-state").textContent=tr("套餐保护已停止运行","Stopped by plan protection");
    const detail=notice ? tr(...notice) : (state?.error || (!state?.subscription?.ready && state ? tr("请先在管理订阅中连接账号并确认应用周上限。","Connect your account and confirm the app’s weekly limit under Manage subscription.") : ""));
    el("run-notice").textContent=detail || "";
    const current=busy ? progress : null;
    const relevant=session?.scenario===game;
    el("console-speed").textContent=format(current?.metered_rounds ? current.tokens_per_minute : relevant ? session.last_window_rate : null);
    el("console-total").textContent=format(relevant && session?.metered_rounds>0 ? session.effective_tokens :
      current?.metered_rounds ? current.uncached_input_tokens+current.output_tokens : null);
    el("console-time").textContent=Number.isFinite(current?.remaining_seconds) ? clock(current.remaining_seconds) : "—";
    el("receipt-note").textContent=tr("有效 token = 未缓存输入 + 输出。实跑以模型回执计量。","Effective tokens = uncached input + output, measured from model receipts.")
      +(relevant && session?.completed ? tr(" 已完成窗口 "," Completed windows ")+session.completed+(session.window_limit ? "/"+session.window_limit : "")+"." : "");
    renderAllowance();
  }
  async function command(action,body={}) {
    if(pending) return;
    notice=null;pending=action;render();
    try {
      const response=await fetch("/api/lab/"+action,{method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)});
      const data=await response.json();
      if(!response.ok) throw Error(data.error || tr("操作失败","Request failed"));
      const responseState=await fetch("/api/lab",{cache:"no-store"});
      if(responseState.ok) state=await responseState.json();
      if(action==="stop") notice=["停止请求已收到，现有回执会保留。","Stop requested. Existing receipts are retained."];
    } catch(e) { notice=[e.message,e.message]; }
    finally {pending="";render();}
  }
  el("setup-slot").append(el("plan"));
  el("manage-plan").addEventListener("click",()=>{ el("plan").open=true; });
  scenario.addEventListener("change",()=>{selected=scenario.value;notice=null;render();});
  el("run-start").onclick=()=>command("session",{scenario:scenario.value,windows:Number(duration.value)});
  el("run-stop").onclick=()=>command("stop");
  el("refresh-allowance").onclick=()=>command("quota");
  new MutationObserver(render).observe(document.documentElement,{attributes:true,attributeFilter:["lang"]});
  window.RunConsole={show(next) {state=next;render();}, disconnected() {state=null;notice=["连接中断，等待本机服务。","Connection lost. Waiting for the local service."];render();}};
  render();
})();
