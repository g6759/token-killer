"use strict";

(() => {
  const el = id => document.getElementById(id);
  const tr = (zh,en) => document.documentElement.lang === "en" ? en : zh;
  const scenario = el("run-scenario"), duration = el("run-duration");
  const client = el("run-client"), target = el("run-target");
  const external = () => client.value !== "openai";
  const games = [["packing", "方格子容纳 · 26 块", "Square packing · 26 squares"],
                 ["relay", "四境驿站 · 49 站", "Four Realms Relay · 49 stations"]];
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
    "client-label":["执行客户端","Client"], "target-label":["有效 token 目标（可选）","Effective token target (optional)"],
    "client-guide-title":["连接客户端","Connect your client"], "client-guide-link":["查看 MCP 配置","Read MCP setup guide"],
    "copy-config":["复制本机配置","Copy local config"],
    "copy-handoff":["复制接手指令","Copy handoff"],
    "speed-label":["最近 60 秒 · 有效 token / 分钟","Effective tokens / min · last 60 sec"],
    "total-label":["本场累计有效 token","Session effective tokens"],
    "time-label":["当前窗口剩余","Time left in window"],
  };
  const statusLabels = {idle:["就绪","Idle"],running:["运行中","Running"],stopping:["正在停止","Stopping"],error:["已中断","Interrupted"]};
  const query = new URLSearchParams(location.search);
  let state, pending = "", notice = null, selected = choices.some(c=>c[0]===query.get("scenario")) ? query.get("scenario") : "single";
  let selectedGame = "packing", requestId = null;
  try {
    const saved = JSON.parse(localStorage.getItem("tk-console") || "{}");
    if (Array.from(client.options).some(opt => opt.value === saved.client)) client.value = saved.client;
    if (games.some(row => row[0] === saved.game)) selectedGame = saved.game;
  } catch {}
  function saveChoice() {
    try { localStorage.setItem("tk-console", JSON.stringify({client:client.value, game:selectedGame})); } catch {}
  }
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
    const mcp = external();
    const busy = state?.run==="running" || state?.run==="stopping";
    const session=state?.continuous, progress=state?.progress;
    for(const [id,pair] of Object.entries(labels)) el(id).textContent=tr(...pair);
    const menu = document.documentElement.lang + "/" + (mcp ? "mcp" : "native");
    const game = mcp ? selectedGame : (busy && choices.some(c=>c[0]===session?.scenario)) ? session.scenario : selected;
    const rawJob = mcp ? state?.games?.[game]?.job : null;
    const job = rawJob?.client===client.value ? rawJob : null;
    const active = mcp ? job?.status==="running" : busy;
    const conflict = mcp && !job && ["running","stopping"].includes(rawJob?.status);
    if(scenario.dataset.menu!==menu) {
      const lengths=mcp ? [1,3,5,10,25,100] : [1,2,3];
      const previousLength=Number(duration.value);
      scenario.replaceChildren(...(mcp ? games : choices).map(([id,zh,en])=>new Option(tr(zh,en),id)));
      duration.replaceChildren(...lengths.map(n => new Option(mcp ? format(n)+tr(" 轮",n===1?" round":" rounds") : n*10+tr(" 分钟"," minutes"),String(n))));
      if(lengths.includes(previousLength)) duration.value=String(previousLength);
      scenario.dataset.menu=menu;
    }
    scenario.value=game;
    if(active && (mcp ? job?.rounds : session?.window_limit)) duration.value=String(mcp ? job.rounds : session.window_limit);
    if(active) target.value=(mcp ? job?.target_tokens : session?.target_tokens) ?? "";
    scenario.disabled=duration.disabled=target.disabled=Boolean(active || pending);
    client.disabled=Boolean(pending);
    el("native-quota").hidden=mcp;el("client-setup").hidden=!mcp;
    el("manage-plan").hidden=el("setup-slot").hidden=mcp;
    const relevant=!mcp && session?.scenario===game;
    const current=!mcp && busy ? progress : null;
    const record=mcp ? job : relevant ? session : null;
    const accumulated=record?.metered_rounds>0 ? record.effective_tokens :
        current?.metered_rounds ? current.uncached_input_tokens+current.output_tokens : null;
    const rolling=record?.recent_tokens_per_minute ?? current?.recent_tokens_per_minute;
    const maxSeconds=(session?.window_limit || 1)*600;
    const elapsed=mcp ? job?.elapsed_seconds :
        Math.min(maxSeconds, (session?.completed || 0)*600+(current ? current.window_seconds-current.remaining_seconds : 0));
    const countSoFar=mcp ? job?.completed : elapsed;
    const countMax=mcp ? job?.rounds : maxSeconds;
    const percent=record?.target_tokens ? (accumulated ?? 0)*100/record.target_tokens :
        countMax ? (countSoFar || 0)*100/countMax : 0;
    const displayPercent=Math.min(100,Math.max(0,percent));
    el("run-progress").value=record ? displayPercent : 0;
    el("goal-progress").textContent=record ? (record.target_tokens ?
        tr("用量目标 ","Token target ")+format(accumulated ?? 0)+" / "+format(record.target_tokens) :
        mcp ? tr("裁判进度 ","Judged rounds ")+format(job.completed)+" / "+format(job.rounds) :
        tr("定时进度 ","Timed run ")+clock(elapsed)+" / "+clock(maxSeconds))+" · "+format(displayPercent)+"%" :
        tr("等待开始","Ready to start");
    if(active && record?.target_tokens && rolling>0 && accumulated!=null && accumulated<record.target_tokens) {
      el("goal-progress").textContent+=tr(" · 预计剩余 "," · ETA ")+clock((record.target_tokens-accumulated)*60/rolling);
    }
    el("console-speed").textContent=format(rolling);
    el("console-total").textContent=format(accumulated);
    el("console-time").textContent=mcp ? format(job?.elapsed_seconds>0 ? job.completed*60/job.elapsed_seconds : null) :
        Number.isFinite(current?.remaining_seconds) ? clock(current.remaining_seconds) : "—";
    el("run-stop").hidden=!active;
    el("run-stop").disabled=Boolean(pending) || (!mcp && state?.run==="stopping");
    el("run-start").hidden=Boolean(active);
    if(mcp) {
      el("duration-label").textContent=tr("最多轮数","Round limit");
      el("time-label").textContent=tr("已裁判轮数 / 分钟","Judged rounds / min");
      el("run-description").textContent=tr("创建本地对局后，将接手指令粘贴到已连接 MCP 的客户端。","Create a local game, then paste the handoff into your connected MCP client.");
      const configPath={"codex-mcp":"~/.codex/config.toml","claude-code":".mcp.json","antigravity":".agents/mcp_config.json"}[client.value];
      el("client-guide-note").textContent=tr("将本机配置合并到 ","Merge the local configuration into ")+configPath+
        tr("。在客户端查看账号配额。",". Check your allowance in that client.");
      el("handoff-box").hidden=!active;
      if(active) {
        const pack=game==="packing" ? "square-26" : "relay-49-frontier";
        const prompt=tr("接手本地 MCP 对局。题包 ","Play my local MCP game. Pack ")+pack+", job_id "+job.id+
          tr("。调用 claim_turn，按题面给出候选，用 submit_turn 提交。只在有真实的本轮模型用量回执时传 usage_receipt。按返回的下一轮继续，结束即停。",
             ". Call claim_turn, solve the puzzle and submit_turn. Include usage_receipt only when this turn has actual model usage. Follow the returned next turn until the game ends.");
        if(el("handoff-text").value!==prompt) el("handoff-text").value=prompt;
      }
      el("run-start").textContent=tr("创建 MCP 对局","Create MCP game");
      el("run-start").disabled=!state || Boolean(pending || conflict);
      const stage = job?.end_reason==="target" ? tr("已达到目标","Target reached") : job?.end_reason==="unmetered" ? tr("缺少回执，已停止","Stopped · missing usage") :
        active ? (job.work?.stage || (job.completed ? tr("等待下一轮","Waiting for next turn") : tr("等待客户端","Waiting for client"))) :
        job?.status==="completed" ? tr("已完成","Completed") : job?.status==="stopped" ? tr("已停止","Stopped") : tr("就绪","Ready");
      el("console-state").textContent=stage;
      el("run-notice").textContent=notice ? tr(...notice) : conflict ? tr("此题包已有对局，请在原对局结束后再开局。","This puzzle already has an active game.") : "";
      el("receipt-note").textContent=tr("用量取自客户端提供的逐轮回执。无法提供回执时，可留空 token 目标，按轮数运行；设了目标则在首个缺失回执处停止。","Usage comes from per-turn client receipts. Leave the token target blank to play by rounds. A token-target run stops at the first missing receipt.");
      return;
    }
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
    if(busy && relevant && session?.target_reached) el("console-state").textContent=tr("目标已达，等待在途回执","Target reached · collecting in-flight replies");
    if (!busy && session?.end_reason==="quota") el("console-state").textContent=tr("套餐保护已停止运行","Stopped by plan protection");
    const detail=notice ? tr(...notice) : (state?.error || (!state?.subscription?.ready && state ? tr("请先在管理订阅中连接账号并确认应用周上限。","Connect your account and confirm the app’s weekly limit under Manage subscription.") : ""));
    el("run-notice").textContent=detail || "";
    if(!busy && relevant && session?.end_reason==="target") el("console-state").textContent=tr("已达到目标","Target reached");
    el("receipt-note").textContent=tr("有效 token = 未缓存输入 + 输出。达到目标后停止派发，并发中的回执可能令总量超出目标。","Effective tokens = uncached input + output. Dispatch stops at the target; in-flight replies may take the total above it.")
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
      return true;
    } catch(e) { notice=[e.message,e.message]; }
    finally {pending="";render();}
  }
  el("setup-slot").append(el("plan"));
  el("manage-plan").addEventListener("click",()=>{ el("plan").open=true; });
  scenario.addEventListener("change",()=>{if(external())selectedGame=scenario.value;else selected=scenario.value;requestId=null;notice=null;saveChoice();render();});
  client.addEventListener("change",()=>{requestId=null;notice=null;el("config-fallback").hidden=true;saveChoice();render();});
  target.addEventListener("input",()=>{requestId=null;});
  duration.addEventListener("change",()=>{requestId=null;});
  el("run-start").onclick=async ()=>{
    if(!target.reportValidity()) return;
    const config=target.value!=="" ? {target_tokens:Number(target.value)} : {};
    if(external()){
      requestId ??= crypto.randomUUID();
      const success=await command("mcp-start",{client:client.value,game:scenario.value,rounds:Number(duration.value),request_id:requestId,...config});
      if(success) requestId=null;
    } else await command("session",{scenario:scenario.value,windows:Number(duration.value),...config});
  };
  el("run-stop").onclick=()=>external() ? command("mcp-stop",{game:selectedGame,id:state?.games?.[selectedGame]?.job?.id}) : command("stop");
  el("copy-handoff").onclick=async ()=>{
    const field=el("handoff-text");
    try {await navigator.clipboard.writeText(field.value);notice=["接手指令已复制。","Handoff copied."];}
    catch {field.focus();field.select();notice=["已选中指令，可用系统复制。","Handoff selected. Use your system copy command."];}
    render();
  };
  el("copy-config").onclick=async ()=>{
    const selectedClient=client.value;
    try {
      const response=await fetch("/api/lab/mcp-config?client="+encodeURIComponent(selectedClient),{cache:"no-store"});
      if(!response.ok) throw Error(tr("无法读取配置。","Could not load configuration."));
      const {config}=await response.json();
      if(client.value!==selectedClient) return;
      try {
        await navigator.clipboard.writeText(config);
        notice=["本机 MCP 配置已复制。","Local MCP configuration copied."];
      } catch {
        const field=el("config-fallback");
        field.hidden=false;field.value=config;field.focus();field.select();
        notice=["已选中配置，请复制并合并到客户端设置。","Configuration selected. Copy it into your client settings."];
      }
    } catch(error) { notice=[error.message,error.message]; }
    render();
  };
  el("refresh-allowance").onclick=()=>command("quota");
  new MutationObserver(render).observe(document.documentElement,{attributes:true,attributeFilter:["lang"]});
  window.RunConsole={show(next) {state=next;render();}, disconnected() {state=null;notice=["连接中断，等待本机服务。","Connection lost. Waiting for the local service."];render();}};
  render();
})();
