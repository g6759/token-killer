"use strict";

const el = id => document.getElementById(id);
const url = new URL(location.href);
let saved;
try { saved = localStorage.getItem("research-games-language"); } catch {}
let lang = [url.searchParams.get("lang"), saved].find(v => v === "en" || v === "zh-CN")
  || (navigator.language.toLowerCase().startsWith("en") ? "en" : "zh-CN");
const t = (zh, en) => lang === "en" ? en : zh;
const packs = ["cap-set", ...[5,6,7,8].map(n => "cap-set-" + n + "d"),
  "golomb-ruler", "golomb-ruler-12", "golomb-ruler-16", "lorenz-divergence"];
let game = packs.includes(url.searchParams.get("game")) ? url.searchParams.get("game") : packs[0];
const family = () => game.startsWith("cap-set") ? "cap" : game.startsWith("golomb") ? "ruler" : "lorenz";
const size = id => Number(id.match(/-(\d+)d?$/)?.[1]) || (id === "cap-set" ? 4 : 8);
const nameOf = id => id.startsWith("cap-set") ? "Cap Set · " + size(id) + "D"
  : id.startsWith("golomb") ? t("Golomb 尺 · ", "Golomb ruler · ") + size(id) + t(" 刻度"," marks")
  : t("超维递归模拟器", "Hyperdimensional Recursive Simulator");
let best, result, trace, field, busy = false, generation = 0, notice = ["",""];

function links() {
  url.searchParams.set("lang", lang);
  url.searchParams.set("game", game);
  history.replaceState(null, "", url.href);
  el("back").href = "/?lang=" + lang;
  el("monitor").href = "/?lang=" + lang + "&scenario=" + encodeURIComponent(game) + "#run-console";
}

function localized() {
  document.documentElement.lang = lang;
  document.title = nameOf(game) + " · token-killer";
  el("lang-zh").setAttribute("aria-pressed", String(lang === "zh-CN"));
  el("lang-en").setAttribute("aria-pressed", String(lang === "en"));
  el("title").textContent = nameOf(game);
  el("intro").textContent = family() === "cap"
    ? t("增加三元向量。任意三个不同向量逐位相加，至少有一位在模 3 下非零。","Add ternary vectors. For every three distinct vectors, their coordinate-wise sum must be nonzero modulo three in at least one position.")
    : family() === "ruler"
      ? t("从刻度 0 开始，让每对刻度之间的距离都不同。最末刻度越小越好。","Start at mark zero and give every pair of marks a distinct distance. A shorter final mark improves the record.")
      : t("调整第一条轨迹的起点。第二条的 x 坐标比它大 0.00001。两条轨迹越早达到距离阈值，得分越高。","Set the first trajectory’s starting point. The second starts 0.00001 farther along x. An earlier distance-threshold crossing scores higher.");
  el("puzzle-label").textContent = t("题包","Puzzle");
  el("packs").replaceChildren(...packs.map(id => new Option(nameOf(id), id)));
  el("packs").value = game;
  el("check").textContent = t("本机复验","Check locally");
  el("save").textContent = t("保存新纪录","Save improvement");
  el("reset").textContent = t("载入最佳解","Load best");
  el("demo").textContent = t("载入示例","Load example");
  el("reference").textContent = t("载入历史 401 点构造","Load historical 401-point set");
  el("score-label").textContent = t("候选成绩","Candidate result");
  el("model-title").textContent = t("模型实跑","Subscription run");
  el("model-copy").textContent = t("本页复验只用本机计算。在主控台选择运行时长并查看套餐余量。","Checks here run on your computer. Choose a run length and review plan allowance in the console.");
  el("monitor").textContent = t("用此题包配置运行 →","Configure a run with this puzzle →");
  if (field) el("entry-label").textContent = fieldLabel();
  links();
  display();
}

function fieldLabel() {
  return family() === "cap" ? t("每个点写成一组 0、1、2，用空格或换行分点。","Enter each point as a string of 0s, 1s and 2s. Separate points with spaces or new lines.")
    : family() === "ruler" ? t("刻度位置，用空格或逗号分隔。","Mark positions, separated by spaces or commas.")
    : t("初值 x（1.00 至 2.00）","Initial x (1.00–2.00)");
}

function loadEditor(solution) {
  const label = document.createElement("label");
  label.id = "entry-label";
  label.htmlFor = "entry";
  label.textContent = fieldLabel();
  field = document.createElement(family() === "cap" ? "textarea" : "input");
  field.id = "entry";
  if (family() === "cap") {
    field.rows = 6;
    field.spellcheck = false;
    field.value = solution.points.map(p => p.join("")).join(" ");
  } else if (family() === "ruler") {
    field.type = "text";
    field.value = solution.marks.join(" ");
  } else {
    Object.assign(field, {type:"range", min:"100", max:"200", step:"1", value:String(solution.initial_x_tick)});
  }
  const value = document.createElement("span");
  const showValue = () => { value.textContent = family() === "lorenz" ? " x = " + (Number(field.value) / 100).toFixed(2) : ""; };
  field.addEventListener("input", () => { showValue(); result = null; trace = null; notice = ["",""]; display(); });
  showValue();
  el("editor").replaceChildren(label, value, field);
}

function candidate() {
  if (family() === "lorenz") return {initial_x_tick:Number(field.value)};
  const values = field.value.trim().split(/[\s,，]+/);
  if (family() === "ruler") {
    if (!values.every(v => /^\d+$/.test(v) && Number.isSafeInteger(Number(v)))) throw Error(t("请输入非负整数刻度。","Enter nonnegative integer marks."));
    return {marks:values.map(Number)};
  }
  if (!values.every(v => v.length === size(game) && /^[012]+$/.test(v))) throw Error(t("请检查点的维数和数字。","Check every point’s dimension and ternary digits."));
  return {points:values.map(v => Array.from(v, Number))};
}

function drawTrace() {
  const canvas = el("plot"), caption = el("plot-caption");
  canvas.hidden = caption.hidden = !trace;
  if (!trace) return;
  const ctx = canvas.getContext("2d");
  const xs = trace.times, ys = trace.distances.map(d => Math.log10(Math.max(1e-8,d)));
  const a = Math.floor(Math.min(...ys)), b = Math.ceil(Math.max(Math.log10(trace.threshold),...ys)) + 0.25;
  const x = v => 52 + v / xs[xs.length-1] * 688;
  const y = v => 261 - (v-a) / (b-a) * 234;
  ctx.clearRect(0,0,760,300);
  ctx.font = "16px sans-serif"; ctx.fillStyle = "#425447"; ctx.strokeStyle = "#cbd2c3";
  for (let tick=a; tick<=b; tick+=2) {
    ctx.beginPath();ctx.moveTo(49,y(tick));ctx.lineTo(740,y(tick));ctx.stroke();ctx.fillText("10^"+tick,3,y(tick)+4);
  }
  for (const tick of [0,10,20,30,40]) ctx.fillText(String(tick),x(tick)-6,286);
  ctx.strokeStyle="#906735";ctx.setLineDash([5,4]);ctx.beginPath();ctx.moveTo(52,y(Math.log10(trace.threshold)));ctx.lineTo(740,y(Math.log10(trace.threshold)));ctx.stroke();ctx.setLineDash([]);
  ctx.beginPath();ctx.strokeStyle="#265841";ctx.lineWidth=2;xs.forEach((v,i)=>i ? ctx.lineTo(x(v),y(ys[i])) : ctx.moveTo(x(v),y(ys[i])));ctx.stroke();ctx.lineWidth=1;
  caption.textContent=t("轨迹距离（对数）随模拟时间的变化。虚线为越线阈值。","Trajectory distance (log scale) against simulation time. The dashed line is the crossing threshold.");
}

function display() {
  const valid = result && result.certified !== false;
  el("score").textContent = !valid ? "—" : family() === "lorenz" ? result.display_crossing_time.toFixed(2)
    : family() === "cap" ? result.solution.points.length + t(" 点"," points") : result.solution.marks.at(-1) + t(" 长度"," length");
  el("parent").textContent = best ? t("当前存档 ","Saved record ") + best.hash.slice(0,10) : "";
  el("audit").replaceChildren();
  const row = (label,value) => { const dt=document.createElement("dt"), dd=document.createElement("dd"); dt.textContent=label; dd.textContent=value; el("audit").append(dt,dd); };
  if (result && family() === "lorenz") {
    row(t("数值复核","Numerical audit"), result.certified ? t("双方法通过","Both methods agree") : result.status === "no_crossing" ? t("观察期内未越线","No crossing observed") : t("未定","Unresolved"));
    row(t("计分精度","Scoring resolution"), "0.01");
  } else if (result) {
    const n = family() === "cap" ? result.solution.points.length : result.solution.marks.length;
    row(t("已检查的点对","Pairs checked"), String(n*(n-1)/2));
    if (result.metrics?.matches_historical_401_reference) row(t("来源","Source"),t("历史 401 点参考解","Historical 401-point reference"));
  }
  el("reference").hidden = game !== "cap-set-8d";
  el("demo").disabled = el("reference").disabled = busy || !field;
  el("check").disabled = el("reset").disabled = busy || !field;
  el("save").disabled = busy || !valid || !best || result.score <= best.state.score;
  if (field) field.disabled = busy;
  el("notice").textContent = t(...notice);
  drawTrace();
}

async function request(path, body) {
  const response = await fetch(path, body ? {method:"POST",headers:{"Content-Type":"application/json"},body:JSON.stringify(body)} : {cache:"no-store"});
  const data = await response.json();
  if (!response.ok) throw Error(data.error || t("复验失败。","Check failed."));
  return data;
}

async function load() {
  const task = ++generation;
  best=result=trace=field=null; busy=true;
  el("editor").replaceChildren();
  notice = ["正在载入并复验本地最佳解…","Loading and verifying the local best…"]; localized();
  try {
    const data = await request("/api/research?game="+encodeURIComponent(game));
    if (task !== generation) return;
    best=data.best; result=data.result; trace=data.trace; loadEditor(best.solution); notice=["",""];
  } catch(e) { if (task === generation) notice=[e.message,e.message]; }
  finally { if(task === generation) { busy=false; display(); } }
}

async function judge(save) {
  if (busy || !best) return;
  const task = generation;
  busy=true; notice=["本机正在复验…","Verifying locally…"]; display();
  try {
    const body = {game,solution:candidate()}; if(save) body.parent=best.hash;
    const data = await request("/api/research/"+(save ? "submit" : "check"),body);
    if (task !== generation) return;
    result=data.result;trace=data.trace;
    if (data.best) best=data.best;
    notice = save ? ["已保存新纪录。","Improvement saved."] : result.certified === false ? ["裁判已返回，候选尚未认证。","The judge returned an uncertified result."] : ["本机复验通过。","Verified locally."];
  } catch(e) { if(task === generation) { result=trace=null; notice=[e.message,e.message]; } }
  finally { if(task === generation) { busy=false; display(); } }
}

async function example(kind) {
  if (busy) return;
  const task=generation;
  busy=true;notice=["",""];display();
  try {
    const data=await request("/api/research?game="+encodeURIComponent(game)+"&example="+kind);
    if(task!==generation) return;
    loadEditor(data.solution);result=trace=null;
    notice=kind==="reference" ? ["已载入历史 401 点参考解，可在本机独立复验。","Historical 401-point set loaded for local verification."] : ["已载入示例，可直接复验或继续修改。","Example loaded. Edit it or run the local check."];
  } catch(e) { if(task===generation) notice=[e.message,e.message]; }
  finally { if(task===generation) {busy=false;display();} }
}

for (const [id,code] of [["lang-zh","zh-CN"],["lang-en","en"]]) el(id).onclick=()=>{
  lang=code; try { localStorage.setItem("research-games-language",lang); } catch {} localized();
};
el("packs").onchange=()=>{ game=el("packs").value; load(); };
el("check").onclick=()=>judge(false); el("save").onclick=()=>judge(true); el("reset").onclick=load;
el("demo").onclick=()=>example("demo");el("reference").onclick=()=>example("reference");
load();
