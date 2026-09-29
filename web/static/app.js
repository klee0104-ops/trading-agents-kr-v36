const $ = (sel) => document.querySelector(sel);

// 서버가 시작될 때마다 새로 만드는 보안 토큰 (index.html에 심어져 있음).
// 상태를 바꾸는 POST 요청에 반드시 붙여야 서버가 받아준다 (CSRF 차단).
const TA_TOKEN = (document.querySelector('meta[name="ta-token"]') || {}).content || "";

async function fetchJSON(url, opts = {}) {
  const method = (opts.method || "GET").toUpperCase();
  if (method !== "GET") {
    opts.headers = Object.assign({}, opts.headers || {}, { "X-TA-Token": TA_TOKEN });
  }
  opts.credentials = "same-origin";
  const res = await fetch(url, opts);
  const data = await res.json();
  if (!res.ok) throw new Error(data.error || `요청 실패 (${res.status})`);
  return data;
}

function toast(msg) {
  const el = document.createElement("div");
  el.className = "toast";
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 4000);
}

function fmtPct(n, digits = 2) {
  const sign = n >= 0 ? "+" : "";
  return `${sign}${n.toFixed(digits)}%`;
}

// ---------------------------------------------------------------- state ---
let lastOutcome = null;
let historyStats = null;
let lastMeta = null;  // {saved_at, mode}

// ------------------------------------------------------------- rendering --
function renderKPI() {
  const pred = lastOutcome?.final_result?.kospi_prediction;
  const stats = historyStats;

  const tiles = [];

  tiles.push({
    label: "오늘 KOSPI 예측",
    value: pred ? (pred.outlook_label || (pred.direction === "up" ? "상승" : "하락")) : "—",
    cls: pred ? pred.direction : "",
    delta: pred ? (pred.up_probability != null ? `상승 확률 ${Math.round(pred.up_probability)}%` : `신뢰도 ${pred.confidence}`) : "실행 전",
  });

  tiles.push({
    label: "과거 전체 적중률",
    value: stats && stats.total ? `${Math.round(stats.accuracy * 100)}%` : "—",
    cls: "",
    delta: stats ? `${stats.correct ?? 0}/${stats.total ?? 0}건 검증됨` : "",
  });

  const coreN = lastOutcome?.final_result?.core_stocks?.length ?? 0;
  const watchN = lastOutcome?.final_result?.watch_stocks?.length ?? 0;
  tiles.push({
    label: "분석 종목 수",
    value: coreN + watchN ? `${coreN + watchN}개` : "—",
    cls: "",
    delta: `핵심 ${coreN} · 관심 ${watchN}`,
  });

  const cases = lastOutcome?.similar_cases ?? [];
  tiles.push({
    label: "유사 사례 검색",
    value: cases.length ? `${cases.length}건` : "0건",
    cls: "",
    delta: cases.length ? `최고 유사도 ${cases[0].similarity}` : "아카이브 데이터 필요",
  });

  $("#kpiRow").innerHTML = tiles.map(t => `
    <div class="tile">
      <div class="label">${escapeHtml(t.label)}</div>
      <div class="value ${["up", "down", "flat"].includes(t.cls) ? t.cls : ""}">${escapeHtml(t.value)}</div>
      <div class="delta">${escapeHtml(t.delta)}</div>
    </div>
  `).join("");
}

function renderPrediction() {
  const result = lastOutcome?.final_result;
  const box = $("#predictionCard");
  if (!result?.kospi_prediction) return;
  const p = result.kospi_prediction;
  const dirCls = p.direction === "up" ? "up" : p.direction === "down" ? "down" : "flat";
  const dirTxt = p.outlook_label ? p.outlook_label : (p.direction === "up" ? "▲ 상승" : "▼ 하락");
  const prob = p.up_probability != null ? Math.round(p.up_probability) : null;
  const facts = factIndex();
  const reasons = (p.reasoning || []).map((r, i) => {
    const f = matchFacts(r, facts);
    return `<li><span class="n">${String(i + 1).padStart(2, "0")}</span><span>${escapeHtml(r)}${f.length ? `<span class="fact">데이터: ${f.map(escapeHtml).join(" · ")}</span>` : ""}</span></li>`;
  }).join("");
  const an = lastOutcome?.analyst_reports || {};
  const votes = Object.keys(ANALYST_LABEL).filter(k => an[k]).map(k => {
    const d = an[k].direction;
    return `<span class="vote ${d === "up" ? "up" : d === "down" ? "down" : ""}">${ANALYST_LABEL[k]} ${d === "up" ? "▲" : d === "down" ? "▼" : "—"}</span>`;
  });
  const risk = lastOutcome?.risk_assessment || {};
  const mod = lastOutcome?.moderation || {};
  const qm = { resolved: "정리됨", contested: "쟁점 남음", unresolved: "미해결" };
  if (risk.risk_level) votes.push(`<span class="vote">리스크 ${escapeHtml(risk.risk_level)}</span>`);
  if (mod.debate_quality) votes.push(`<span class="vote">토론 ${escapeHtml(qm[mod.debate_quality] || mod.debate_quality)}</span>`);
  const ver = lastOutcome?.verification || {};
  const qa = lastOutcome?.final_qa || {};
  box.innerHTML = `
    <h2>오늘의 KOSPI 예측 <span class="h2-sub">대상 거래일 ${escapeHtml(lastOutcome?.target_date ?? "-")}</span></h2>
    <div class="why-head">
      <div class="why-dir ${dirCls}">${escapeHtml(dirTxt)}</div>
      <div class="why-meta">
        ${prob != null ? `<span class="conf-pill">상승 확률 <b class="num">${prob}%</b></span>` : ""}
        <span class="conf-pill">신뢰도 ${escapeHtml(p.confidence ?? "-")}</span>
      </div>
    </div>
    ${prob != null ? `<div><div class="gauge"><div class="needle" style="left:${prob}%"></div></div><div class="gauge-lbl"><span>하락 확신</span><span>50</span><span>상승 확신</span></div></div>` : ""}
    ${p.position_info ? `<div class="why-foot"><b>포지션</b> ${escapeHtml(p.position_info.reason || "")}${lastOutcome?.quant?.realized_vol_20 != null ? ` · 실현변동성 <span class="num">${lastOutcome.quant.realized_vol_20}%</span>` : ""}</div>` : ""}
    ${p.calibration_note ? `<div class="why-foot" style="color:var(--text-muted)">확률 보정: ${escapeHtml(p.calibration_note)}${p.calibration_applied ? ` (원래 ${Math.round(p.up_probability_raw)}%)` : ""}</div>` : ""}
    <div class="section-label">왜 이렇게 판단했나</div>
    <ol class="evidence">${reasons}</ol>
    ${votes.length ? `<div class="section-label">에이전트 판단</div><div class="votes">${votes.join("")}</div>` : ""}
    <div class="why-foot">검증 ① ${ver.verification_passed ? "통과" : "주의"} · 검증 ② ${qa.consistent === true ? "정합" : qa.consistent === false ? "불일치" : "미검증"}${qa.llm_verification_triggered ? " (AI 재검토)" : ""} · <a href="#process" style="color:var(--accent)">분석 과정 전체 보기 →</a></div>
  `;
}

// 판단 근거 문장에 등장하는 지표를 실제 수치와 연결한다 (프롬프트에 들어간 사실만)
function factIndex() {
  const sn = lastOutcome?.snapshot || {};
  const q = lastOutcome?.quant || {};
  const idx = [];
  (sn.us_indices || []).forEach(x => idx.push({ keys: [x.name, x.name === "SOX" ? "반도체 지수" : x.name === "S&P500" ? "S&P" : x.name === "VIX" ? "공포" : x.name === "NASDAQ" ? "나스닥" : x.name === "DOW" ? "다우" : x.name], text: `${x.name} ${fmtPct(x.change_pct)}${x.value != null ? ` (${x.value})` : ""}` }));
  (sn.macro || []).forEach(x => idx.push({ keys: [x.name, x.name.includes("달러") ? "환율" : x.name.includes("금리") ? "금리" : x.name.includes("WTI") ? "유가" : x.name], text: `${x.name} ${fmtPct(x.change_pct)}${x.value != null ? ` (${x.value})` : ""}` }));
  if (sn.kr_flow?.foreign_net_krw_bn != null) idx.push({ keys: ["외국인"], text: `외국인 순매수 ${Number(sn.kr_flow.foreign_net_krw_bn).toLocaleString()}억` });
  if (q.rsi14 != null) idx.push({ keys: ["RSI", "과매수", "과매도"], text: `RSI ${q.rsi14}` });
  if (q.macd) idx.push({ keys: ["MACD", "골든", "데드"], text: `MACD 히스토그램 ${q.macd.hist}` });
  if (q.ma) idx.push({ keys: ["이평", "이동평균", "정배열", "역배열", "20일선", "60일선"], text: `20/60일선 ${q.ma.ma20}/${q.ma.ma60} (${q.ma.state})` });
  if (q.realized_vol_20 != null) idx.push({ keys: ["변동성"], text: `실현변동성 ${q.realized_vol_20}%` });
  if (q.mom_12_1 != null) idx.push({ keys: ["모멘텀"], text: `12-1 모멘텀 ${fmtPct(q.mom_12_1)}` });
  return idx;
}
function matchFacts(text, idx) {
  const out = [];
  idx.forEach(f => { if (f.keys.some(k => k && text.includes(k)) && !out.includes(f.text)) out.push(f.text); });
  return out.slice(0, 3);
}

// ---- V3.18 티커 스트립 + KOSPI 차트 ----
function renderTicker() {
  const box = $("#ticker");
  if (!box) return;
  const sn = lastOutcome?.snapshot || {};
  const items = [];
  (sn.us_indices || []).forEach(x => items.push([x.name, x.value != null ? x.value : "", x.change_pct]));
  (sn.macro || []).forEach(x => items.push([x.name, x.value != null ? x.value : "", x.change_pct]));
  if (sn.kr_flow?.foreign_net_krw_bn != null) items.push(["외국인 순매수(억)", Number(sn.kr_flow.foreign_net_krw_bn).toLocaleString(), null, sn.kr_flow.direction === "buy" ? "up" : "down"]);
  if (!items.length) { box.innerHTML = ""; return; }
  box.innerHTML = items.map(([n, v, c, forced]) => {
    const cls = forced || (typeof c === "number" ? (c > 0 ? "up" : c < 0 ? "down" : "flat") : "flat");
    return `<div class="tk"><span class="nm">${escapeHtml(String(n))}</span><span class="val">${escapeHtml(String(v))}</span><span class="chg ${cls}">${typeof c === "number" ? fmtPct(c) : (forced === "up" ? "▲ 순매수" : "▼ 순매도")}</span></div>`;
  }).join("");
}

// ---- (V3.26) 시계 · 장 상태 · 실시간 국내 지수 ------------------------------------
const KST_FMT = new Intl.DateTimeFormat("ko-KR", { timeZone: "Asia/Seoul", month: "2-digit", day: "2-digit",
  weekday: "short", hour: "2-digit", minute: "2-digit", second: "2-digit", hour12: false });
function kstParts(d = new Date()) { const o = {}; KST_FMT.formatToParts(d).forEach(p => { o[p.type] = p.value; }); return o; }
function marketStatusKst(d = new Date()) {
  const p = kstParts(d);
  if (p.weekday === "토" || p.weekday === "일") return { code: "weekend", label: "휴장(주말)" };
  const m = (Number(p.hour) % 24) * 60 + Number(p.minute);
  if (m < 540) return { code: "pre", label: "장 시작 전" };
  if (m < 930) return { code: "open", label: "장중" };
  if (m < 940) return { code: "closing", label: "종가 확정 중" };
  return { code: "closed", label: "장 마감" };
}
function agoText(iso) {  // 시간대 없는 ISO 는 KST 로 본다
  if (!iso) return "";
  const s = String(iso);
  const t = Date.parse(/[zZ]|[+-]\d\d:?\d\d$/.test(s) ? s : s.replace(" ", "T") + "+09:00");
  if (!Number.isFinite(t)) return "";
  const sec = Math.max(0, Math.round((Date.now() - t) / 1000));
  if (sec < 60) return `${sec}초 전`;
  if (sec < 3600) return `${Math.floor(sec / 60)}분 전`;
  if (sec < 86400) return `${Math.floor(sec / 3600)}시간 ${Math.floor((sec % 3600) / 60)}분 전`;
  return `${Math.floor(sec / 86400)}일 전`;
}
function updateAges() { document.querySelectorAll("[data-ago]").forEach(el => { el.textContent = agoText(el.getAttribute("data-ago")); }); }
function tickClock() {
  const box = $("#clock");
  if (box) {
    const p = kstParts(), st = marketStatusKst();
    box.textContent = `${p.month}.${p.day}(${p.weekday}) ${String(Number(p.hour) % 24).padStart(2, "0")}:${p.minute}:${p.second} KST · ${st.label}`;
    box.className = `badge clock st-${st.code}`;
  }
  updateAges();
}
let liveData = null;
async function loadLive() {
  try {
    const res = await fetch("/api/live", { credentials: "same-origin" });
    if (res.ok) liveData = await res.json();
  } catch (_) { /* offline — 기존 시장 현황판 유지 */ }
  renderBoard();
}
function scheduleLive() {
  const code = marketStatusKst().code;
  const ms = (code === "open" || code === "closing") ? 30000 : 300000;  // 장중 30초, 그 외 5분
  setTimeout(async () => { if (!document.hidden) await loadLive(); scheduleLive(); }, ms);
}

let marketBoard = null;
async function loadMarket() {
  try {
    const res = await fetch("/api/market", { credentials: "same-origin" });
    if (res.ok) marketBoard = await res.json();
  } catch (_) { /* offline */ }
  renderBoard();
}

function renderBoard() {
  const box = $("#marketBoard");
  if (!box) return;
  const sn = lastOutcome?.snapshot || {};
  let rows = marketBoard?.rows || null;
  let live = !!rows;
  if (!rows) {  // 시세를 못 받으면 분석 당시 스냅샷으로 대체
    rows = [];
    (sn.us_indices || []).forEach(x => rows.push({ name: x.name, group: "us", value: x.value, change_pct: x.change_pct, date: x.as_of }));
    (sn.macro || []).forEach(x => rows.push({ name: x.name, group: "macro", value: x.value, change_pct: x.change_pct }));
  }
  let k = rows.find(r => r.group === "kr");
  const lk = (liveData?.rows || []).find(r => r.name === "KOSPI");
  const lq = (liveData?.rows || []).find(r => r.name === "KOSDAQ");
  if (lk && (!k || String(lk.date) >= String(k.date || ""))) k = { ...lk, live: true };
  const cls = c => (typeof c === "number" ? (c > 0 ? "up" : c < 0 ? "down" : "flat") : "flat");
  const fmtV = v => (v == null ? "—" : Number(v).toLocaleString(undefined, { maximumFractionDigits: 2 }));
  const tr = r => `<tr><td class="nm">${escapeHtml(r.name)}</td><td class="val">${fmtV(r.value)}</td><td class="chg ${cls(r.change_pct)}">${typeof r.change_pct === "number" ? fmtPct(r.change_pct) : "—"}</td></tr>`;
  const us = rows.filter(r => r.group === "us"), mc = rows.filter(r => r.group === "macro");
  const flow = sn.kr_flow;
  box.innerHTML = `
    <h2>시장 현황 <span class="h2-sub">${k?.live ? `<span class="live"></span>실시간 · ${escapeHtml(liveData.status?.label || "")}` : live ? `<span class="live"></span>${escapeHtml(marketBoard.fetched_at || "")} 기준` : "분석 당시 스냅샷"}</span></h2>
    ${k ? `<div class="kq ${cls(k.change_pct)}"><div class="nm"><span>KOSPI ${k.is_today ? "오늘" : escapeHtml(k.date || "")}${k.is_today ? (k.live && liveData.status?.code === "open" ? " · 장중" : " · 종가") : " 종가"}</span><span>전일 ${fmtV(k.prev)}</span></div><div class="px">${fmtV(k.value)}</div><div class="ch">${fmtPct(k.change_pct)} (${k.prev != null ? (k.value - k.prev).toFixed(2) : "—"})</div>${k.live ? `<div class="asof">체결 ${escapeHtml(String(k.traded_at || "").replace("T", " ").slice(5, 19))} · <span data-ago="${escapeHtml(liveData.server_time || "")}"></span> 갱신</div>` : ""}</div>` : ""}
    ${lq ? `<table>${tr({ name: "KOSDAQ", value: lq.value, change_pct: lq.change_pct })}</table>` : ""}
    <div class="grp">참고 미국 지수 ${us[0]?.date ? `· ${escapeHtml(us[0].date)}` : ""}</div>
    <table>${us.map(tr).join("")}</table>
    <div class="grp">거시 지표</div>
    <table>${mc.map(tr).join("")}</table>
    ${flow?.foreign_net_krw_bn != null ? `<div class="grp">외국인 수급 ${flow.date ? `· ${escapeHtml(flow.date)}` : ""}</div><table><tr><td class="nm">KOSPI 외국인 순매수</td><td class="val"></td><td class="chg ${flow.direction === "buy" ? "up" : "down"}">${Number(flow.foreign_net_krw_bn).toLocaleString()}억</td></tr></table>` : ""}
    <div class="foot">KOSPI·KOSDAQ: 네이버 증권 마지막 체결가(장중 30초마다). 미국·거시: Yahoo Finance(10분마다, 지연 시세). SOX는 KOSPI와 방향 일치율이 가장 높은 지수입니다.</div>`;
  updateAges();
}

let kospiSeries = null;
async function loadKospi() {
  try {
    const res = await fetch("/api/kospi?days=80", { credentials: "same-origin" });
    if (res.ok) { kospiSeries = await res.json(); }
  } catch (_) { /* offline */ }
  if (!kospiSeries && lastOutcome?.kospi_history) kospiSeries = lastOutcome.kospi_history;
  renderKospiChart();
}

function sma(arr, n) { return arr.map((_, i) => i + 1 < n ? null : arr.slice(i + 1 - n, i + 1).reduce((a, b) => a + b, 0) / n); }

function renderKospiChart() {
  const wrap = $("#kChart");
  if (!wrap) return;
  const s = kospiSeries || lastOutcome?.kospi_history;
  if (!s || !s.closes?.length) { wrap.innerHTML = '<div class="empty">KOSPI 시세를 가져오지 못했습니다 (네트워크 확인).</div>'; return; }
  const closes = s.closes, dates = s.dates, n = closes.length;
  const W = 720, H = 260, padL = 8, padR = 62, padT = 12, padB = 26;
  const ma20 = sma(closes, 20), ma60 = sma(closes, 60);
  const lo = Math.min(...closes) * 0.995, hi = Math.max(...closes) * 1.005;
  const X = i => padL + (i / (n - 1)) * (W - padL - padR);
  const Y = v => padT + (1 - (v - lo) / (hi - lo)) * (H - padT - padB);
  const path = arr => arr.map((v, i) => v == null ? null : `${i === 0 || arr[i - 1] == null ? "M" : "L"}${X(i).toFixed(1)},${Y(v).toFixed(1)}`).filter(Boolean).join(" ");
  const ticks = 4, grid = [];
  for (let k = 0; k <= ticks; k++) { const v = lo + (hi - lo) * k / ticks; grid.push(`<line class="grid-line" x1="${padL}" x2="${W - padR}" y1="${Y(v).toFixed(1)}" y2="${Y(v).toFixed(1)}"></line><text class="axis-text" x="${W - padR + 6}" y="${(Y(v) + 4).toFixed(1)}">${Math.round(v).toLocaleString()}</text>`); }
  const xl = [0, Math.floor(n / 2), n - 1].map(i => `<text class="axis-text" x="${X(i).toFixed(1)}" y="${H - 8}" text-anchor="${i === 0 ? "start" : i === n - 1 ? "end" : "middle"}">${escapeHtml((dates[i] || "").slice(5))}</text>`);
  const last = closes[n - 1], prev = closes[n - 2] ?? last, chg = (last / prev - 1) * 100;
  const p = lastOutcome?.final_result?.kospi_prediction;
  let pred = "";
  if (p?.direction) {
    const up = p.direction === "up", x = X(n - 1) + 22, y = Y(last) + (up ? -22 : 22);
    pred = `<line class="pred-${up ? "up" : "down"}" x1="${X(n - 1).toFixed(1)}" y1="${Y(last).toFixed(1)}" x2="${x}" y2="${y}" stroke-width="2" stroke-dasharray="3 3"></line><circle class="pred-${up ? "up" : "down"}" cx="${x}" cy="${y}" r="5"></circle><text class="axis-text" x="${x + 8}" y="${y + 4}" style="fill:var(--${up ? "up" : "down"})">${up ? "▲" : "▼"}${p.up_probability != null ? " " + Math.round(p.up_probability) + "%" : ""}</text>`;
  }
  wrap.innerHTML = `<svg class="kchart" viewBox="0 0 ${W} ${H}" role="img" aria-label="KOSPI 최근 ${n}거래일 종가">
    <defs><linearGradient id="kgrad" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--accent)" stop-opacity="0.22"/><stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>
    ${grid.join("")}
    <path class="area" d="${path(closes)} L${X(n - 1).toFixed(1)},${(H - padB).toFixed(1)} L${X(0).toFixed(1)},${(H - padB).toFixed(1)} Z"></path>
    <path class="ma60" d="${path(ma60)}"></path><path class="ma20" d="${path(ma20)}"></path><path class="price" d="${path(closes)}"></path>
    ${pred}${xl.join("")}
  </svg>`;
  $("#kPx").textContent = last.toLocaleString(undefined, { minimumFractionDigits: 2, maximumFractionDigits: 2 });
  const c = $("#kChg"); c.textContent = `${fmtPct(chg)} (${(last - prev).toFixed(2)})`; c.className = `px-chg ${chg >= 0 ? "up" : "down"}`;
  $("#kDate").textContent = `${dates[n - 1]} 종가 · 최근 ${n}거래일`;
  const q = lastOutcome?.quant || {};
  const lab = q.labels || {};
  const inds = [];
  if (q.rsi14 != null) inds.push(["RSI(14)", q.rsi14, lab.rsi]);
  if (q.macd) inds.push(["MACD 히스토그램", q.macd.hist, lab.macd]);
  if (q.bb_pct_b != null) inds.push(["볼린저 %B", q.bb_pct_b, lab.bb]);
  if (q.ma) inds.push(["이평 배열", `${q.ma.above_ma20 ? "20일선 위" : "20일선 아래"}`, q.ma.state]);
  if (q.realized_vol_20 != null) inds.push(["실현변동성(연율)", q.realized_vol_20 + "%", lab.vol]);
  if (q.mom_12_1 != null) inds.push(["12-1 모멘텀", fmtPct(q.mom_12_1), lab.momentum]);
  if (q.flow_z5 != null) inds.push(["외국인 5일 z", q.flow_z5, lab.flow]);
  $("#indRow").innerHTML = inds.map(([k, v, t]) => `<div class="ind"><div class="k">${escapeHtml(k)}</div><div class="v">${escapeHtml(String(v))}</div>${t ? `<div class="t">${escapeHtml(String(t))}</div>` : ""}</div>`).join("");
}

function initTheme() {
  let t = "";
  try { t = localStorage.getItem("ta.theme") || ""; } catch (_) {}
  if (t) document.documentElement.setAttribute("data-theme", t);
  const b = $("#btnTheme");
  if (b) {
    const paint = () => { b.textContent = document.documentElement.getAttribute("data-theme") === "light" ? "☾ 어둡게" : "☀ 밝게"; };
    paint();
    b.addEventListener("click", () => {
      const cur = document.documentElement.getAttribute("data-theme") === "light" ? "" : "light";
      if (cur) document.documentElement.setAttribute("data-theme", cur); else document.documentElement.removeAttribute("data-theme");
      try { localStorage.setItem("ta.theme", cur); } catch (_) {}
      paint();
    });
  }
}

const STOCK_RANK = () => Object.fromEntries((lastOutcome?.news_universe || []).map(u => [u.name, u.rank]));

function sigText(sig) {
  return sig === "up" ? "▲ 상승 신호" : sig === "down" ? "▼ 하락 신호" : "— 보합";
}

function stockCard(s) {
  const sig = s.directional_signal;
  const rank = STOCK_RANK()[s.name];
  const mod = s.moderated ? `<div class="mod-badge" title="${escapeHtml(s.moderation_reason || "")}">중재자 조정 · 원래 ${sigText(s.original_signal).replace(" 신호", "")}</div>` : "";
  return `
    <div class="stock-card ${s.moderated ? "moderated" : ""}">
      <div class="name">${escapeHtml(s.name)}${rank ? ` <span class="rank">시총 ${rank}위</span>` : ""}</div>
      <div class="meta">${escapeHtml(s.related_us_indicator || s.sector || "")} · 민감도 ${escapeHtml(s.sensitivity || "-")}</div>
      <div class="sig ${sig === "up" ? "up" : sig === "down" ? "down" : "flat"}">${sigText(sig)}</div>
      ${mod}
      <div class="reason">${escapeHtml(s.moderated && s.moderation_reason ? s.moderation_reason : (s.reason || ""))}</div>
    </div>
  `;
}

function renderStocks() {
  const emptyBox = $("#stocksEmpty");
  if (emptyBox) emptyBox.style.display = lastOutcome?.final_result?.core_stocks?.length ? "none" : "";
  const result = lastOutcome?.final_result;
  if (!result) return;
  $("#stocksCard").style.display = "";
  $("#coreStockGrid").innerHTML = (result.core_stocks || []).map(stockCard).join("");
  $("#watchStockGrid").innerHTML = (result.watch_stocks || []).map(stockCard).join("");
  const sm = result.stock_moderation;
  const note = $("#watchNote");
  if (note) note.textContent = sm ? `오늘 관심 종목은 KOSPI 시총 100 중 무작위 추첨(시총 상위 30에서 절반, 31~100에서 절반). 종목 중재자가 ${sm.reviewed}종목을 검토해 ${sm.adjusted_to_flat}종목을 보합으로 조정했습니다.` : "";
}

function renderDebate() {
  const transcript = lastOutcome?.debate_transcript || [];
  if (!transcript.length) return;
  $("#debateList").className = "";
  $("#debateList").innerHTML = transcript.map(t => `
    <div class="debate-turn">
      <span class="stance ${t.stance === "bull" ? "bull" : "bear"}">${t.stance === "bull" ? "BULL" : "BEAR"}</span>
      <div class="content">
        <div class="round">round ${t.round} · confidence ${escapeHtml(t.confidence ?? "-")}</div>
        ${escapeHtml(t.argument || "")}
      </div>
    </div>
  `).join("");
}

function renderRisk() {
  const risk = lastOutcome?.risk_assessment;
  if (!risk) return;
  $("#riskBox").className = "";
  $("#riskBox").innerHTML = `
    <div class="risk-row">
      <span class="risk-chip">리스크: ${escapeHtml(risk.risk_level || "-")}</span>
      <span class="risk-chip">권장 신뢰도: ${escapeHtml(risk.recommended_confidence || "-")}</span>
    </div>
    <div class="risk-summary">${escapeHtml(risk.summary || "")}</div>
  `;
}

function renderSimilarCases() {
  const cases = lastOutcome?.similar_cases || [];
  const box = $("#similarCases");
  if (!cases.length) {
    box.className = "empty";
    box.textContent = "일치하는 과거 사례가 없습니다 (이벤트 아카이브 데이터가 더 필요할 수 있습니다).";
    return;
  }
  box.className = "";
  box.innerHTML = cases.map(c => `
    <div class="case-item">
      <div class="head">
        <span>${escapeHtml(c.event_date)} · ${escapeHtml(c.stock_name)}</span>
        <span class="chg ${c.direction === "up" ? "up" : "down"}">${fmtPct(c.change_pct)} <span class="sim">(유사도 ${c.similarity})</span></span>
      </div>
      <div class="news">${(c.news_titles || []).map(escapeHtml).join(" · ")}</div>
    </div>
  `).join("");
}

function renderHistory() {
  const stats = historyStats;
  const box = $("#historyBox");
  if (!stats || !stats.total) {
    box.className = "empty";
    box.textContent = "검증된 예측 기록이 없습니다.";
    return;
  }
  box.className = "";
  const pct = Math.round(stats.accuracy * 100);
  const byStock = Object.entries(stats.by_stock || {}).sort((a, b) => b[1].total - a[1].total).slice(0, 8);
  const calib = calibrationTable(stats.calibration, stats.by_outlook, stats.by_debate_quality);
  const sm = stats.stock_moderation || {};
  const smText = sm.watch_total ? `<div class="section-label">종목 신호 적중</div><table class="stat-table"><tbody>
      <tr><td>핵심 2종목</td><td class="num">${sm.core_correct}/${sm.core_total}</td></tr>
      <tr><td>관심 종목 (추첨)</td><td class="num">${sm.watch_correct}/${sm.watch_total}</td></tr>
      <tr><td>중재자가 보합으로 조정한 종목</td><td class="num">${sm.adjusted}건 · 보합 적중 ${sm.flat_correct} · 원래 신호였다면 적중 ${sm.original_would_be_correct}</td></tr>
    </tbody></table>` : "";

  box.innerHTML = `
    <div style="font-size:26px;font-weight:700">${pct}%</div>
    <div class="meter"><div class="fill" style="width:${pct}%"></div></div>
    <div class="delta" style="color:var(--text-muted);font-size:12px;margin-bottom:10px">${stats.correct}/${stats.total}건 적중</div>
    ${byStock.length ? `
      <table class="stat-table">
        <thead><tr><th>종목</th><th class="num">적중</th><th class="num">전체</th></tr></thead>
        <tbody>
          ${byStock.map(([name, s]) => `<tr><td>${escapeHtml(name)}</td><td class="num">${s.correct}</td><td class="num">${s.total}</td></tr>`).join("")}
        </tbody>
      </table>
    ` : ""}
    ${smText}
    ${calib}
  `;
}

function pctText(n, d) {
  return d ? `${n}/${d} (${Math.round((n / d) * 100)}%)` : "—";
}

function renderMeta() {
  const box = $("#runMeta");
  if (!lastMeta) { box.textContent = ""; return; }
  const label = lastMeta.mode === "demo" ? "Mock 데모 결과" : "마지막 실제 분석";
  box.textContent = `${label} · ${lastMeta.saved_at.replace("T", " ")} · `;
  const ago = document.createElement("span");
  ago.setAttribute("data-ago", lastMeta.saved_at);
  ago.textContent = agoText(lastMeta.saved_at);
  box.appendChild(ago);
}

function renderInputs() {
  const box = $("#inputsBox");
  const sn = lastOutcome?.snapshot;
  if (!sn) return;
  box.className = "";
  const row = q => `<tr><td>${escapeHtml(q.name)}</td><td class="num">${q.value != null ? escapeHtml(String(q.value)) : "—"}</td>
    <td class="num ${q.change_pct >= 0 ? "up" : "down"}">${fmtPct(Number(q.change_pct))}</td></tr>`;
  const flow = sn.kr_flow;
  const flowText = flow ? `외국인 ${flow.direction === "buy" ? "순매수" : "순매도"} ${Math.abs(Number(flow.foreign_net_krw_bn)).toLocaleString()}억원 (${escapeHtml(flow.date)})${flow.strong ? " · 대규모" : ""}` : "외국인 수급: 데이터 없음 (pykrx 미설치 또는 조회 실패)";
  box.innerHTML = `
    <table class="stat-table">
      <thead><tr><th>미국 지수 (${escapeHtml(sn.as_of)})</th><th class="num">종가</th><th class="num">등락</th></tr></thead>
      <tbody>${(sn.us_indices || []).map(row).join("")}</tbody>
      ${(sn.macro || []).length ? `<thead><tr><th>거시 지표</th><th class="num">값</th><th class="num">등락</th></tr></thead><tbody>${sn.macro.map(row).join("")}</tbody>` : ""}
    </table>
    <div class="delta" style="font-size:12px;color:var(--text-secondary);margin-top:6px">${flowText}</div>
    ${(sn.data_notes || []).length ? `<div class="delta" style="font-size:12px;color:var(--warning)">${sn.data_notes.map(escapeHtml).join(" · ")}</div>` : ""}`;
}

const ANALYST_LABEL = { TechnicalAnalyst: "기술적", MacroFlowAnalyst: "거시·수급", SemiconductorAnalyst: "반도체", NewsSentimentAnalyst: "뉴스·심리", MarketAnalyst: "통합(다수결)" };

function renderAnalysts() {
  const box = $("#analystsBox");
  const reps = lastOutcome?.analyst_reports;
  if (!reps) { if (lastOutcome) { box.className = "empty"; box.textContent = "이 결과는 분석가 세분화 이전 버전입니다."; } return; }
  box.className = "";
  const arrow = d => d === "up" ? '<span class="up">▲ 상승</span>' : d === "down" ? '<span class="down">▼ 하락</span>' : '<span style="color:var(--text-muted)">보류</span>';
  const c = lastOutcome.analyst_report || {};
  box.innerHTML = `
    <table class="stat-table">
      <thead><tr><th>분석가</th><th class="num">판단</th><th class="num">신뢰도</th></tr></thead>
      <tbody>${Object.entries(reps).map(([n, r]) => `<tr><td>${ANALYST_LABEL[n] || escapeHtml(n)}</td><td class="num">${arrow(r.direction)}</td><td class="num">${escapeHtml(r.confidence || "-")}</td></tr>`).join("")}
      <tr style="font-weight:600"><td>통합 (다수결)</td><td class="num">${arrow(c.direction)}</td><td class="num">${escapeHtml(c.confidence || "-")}</td></tr></tbody>
    </table>
    ${Object.entries(reps).map(([n, r]) => r.summary ? `<div class="reason" style="font-size:12.5px;margin-top:6px"><b>${ANALYST_LABEL[n] || escapeHtml(n)}</b> · ${escapeHtml(r.summary)}</div>` : "").join("")}
    ${(lastOutcome.news_titles || []).length ? `<div class="delta" style="font-size:12px;color:var(--text-muted);margin-top:6px">뉴스 제목 ${lastOutcome.news_titles.length}건 반영${(lastOutcome.news_universe || []).length ? ` · 대상 ${lastOutcome.news_universe.length}종목: ${lastOutcome.news_universe.map(u => escapeHtml(u.name)).join(", ")}` : ""}</div>` : ""}`;
}

function renderModeration() {
  const box = $("#modBox");
  const m = lastOutcome?.moderation;
  if (!m) return;
  box.className = "";
  const q = { resolved: ["한쪽 우세", "ok"], contested: ["팽팽함", ""], unresolved: ["미해결 → 중립 권고", "bad"] }[m.debate_quality] || ["?", ""];
  const side = { bull: "Bull", bear: "Bear", none: "없음" }[m.stronger_side] || "—";
  box.innerHTML = `
    <div class="risk-row">
      <span class="risk-chip ${q[1]}">토론 품질: ${q[0]}</span>
      <span class="risk-chip">근거상 우세: ${escapeHtml(side)}</span>
      ${m.repeated_turns ? `<span class="risk-chip">반복 발언 ${m.repeated_turns}</span>` : ""}
    </div>
    ${m.summary ? `<div class="risk-summary">${escapeHtml(m.summary)}</div>` : ""}
    ${(m.agreed_facts || []).length ? `<div class="section-label">합의된 사실</div><ul class="reasoning">${m.agreed_facts.map(x => `<li>${escapeHtml(x)}</li>`).join("")}</ul>` : ""}
    ${(m.key_disputes || []).length ? `<div class="section-label">쟁점</div><ul class="reasoning">${m.key_disputes.map(d => `<li><b>${escapeHtml(d.topic || "")}</b> — Bull: ${escapeHtml(d.bull || "")} / Bear: ${escapeHtml(d.bear || "")}</li>`).join("")}</ul>` : ""}
    ${(m.noise || []).length ? `<div class="section-label">근거 없이 반복된 주장</div><ul class="reasoning">${m.noise.map(x => `<li>${escapeHtml(x)}</li>`).join("")}</ul>` : ""}`;
}

function renderQA() {
  const o = lastOutcome;
  const box = $("#qaBox");
  if (!o) return;
  box.className = "";
  const dq = o.debate_qa || [];
  const sum = { bull: [0, 0], bear: [0, 0] };
  const unverified = [];
  dq.forEach(r => {
    if (sum[r.stance]) { sum[r.stance][0] += r.verified_count || 0; sum[r.stance][1] += r.total_count || 0; }
    (r.unverified || []).forEach(u => unverified.push(`${r.stance.toUpperCase()} r${r.round}: ${u}`));
  });
  const v = o.verification || {};
  const f = o.final_qa || {};
  const ma = o.market_analysis || {};
  const chip = (ok, okText, badText) =>
    `<span class="risk-chip ${ok === true ? "ok" : ok === false ? "bad" : ""}">${ok === true ? okText : ok === false ? badText : "미검증"}</span>`;
  const issues = (f.issues || []).concat(f.rule_conflict_reasons || []);
  const conflicts = (v.code_checks && v.code_checks.conflicts) || [];

  box.innerHTML = `
    <table class="stat-table">
      <tbody>
        <tr><td>장세 (규칙엔진)</td><td class="num">${ma.regime_trend ? `추세 ${escapeHtml(ma.regime_trend)} · 변동성 ${escapeHtml(ma.regime_volatility)} · 리스크힌트 ${escapeHtml(ma.risk_level_hint)}` : "—"}</td></tr>
        <tr><td>Bull 근거 검증</td><td class="num">${dq.length ? pctText(sum.bull[0], sum.bull[1]) : "—"}</td></tr>
        <tr><td>Bear 근거 검증</td><td class="num">${dq.length ? pctText(sum.bear[0], sum.bear[1]) : "—"}</td></tr>
        <tr><td>검증 ① 리스크 단계</td><td class="num">${chip(v.verification_passed, "통과", "재판단됨")} 최종 리스크 ${escapeHtml(v.final_risk_level || "-")}</td></tr>
        <tr><td>검증 ② 최종 단계</td><td class="num">${chip(f.consistent, "정합", "불일치")} ${f.llm_verification_triggered === true ? "AI 검증 호출됨" : f.consistent === true && f.llm_verification_triggered === false ? "규칙 통과 (AI 호출 생략)" : ""}</td></tr>
      </tbody>
    </table>
    ${conflicts.length ? `<div class="section-label">리스크 단계 상충</div><ul class="reasoning">${conflicts.map(c => `<li>${escapeHtml(c)}</li>`).join("")}</ul>` : ""}
    ${issues.length ? `<div class="section-label">최종 단계 이슈</div><ul class="reasoning">${issues.map(c => `<li>${escapeHtml(c)}</li>`).join("")}</ul>` : ""}
    ${unverified.length ? `<div class="section-label">자료에서 확인되지 않은 근거</div><ul class="reasoning">${unverified.map(c => `<li>${escapeHtml(c)}</li>`).join("")}</ul>` : ""}
    ${!dq.length ? `<div class="delta" style="color:var(--text-muted);font-size:12px">토론 근거 상세는 대시보드에서 실행했거나 V3.6 이후 저장된 결과에만 표시됩니다.</div>` : ""}
  `;
}

function renderAgents() {
  const box = $("#agentBox");
  const agents = historyStats?.by_agent || {};
  const order = ["TechnicalAnalyst", "MacroFlowAnalyst", "SemiconductorAnalyst", "NewsSentimentAnalyst", "MarketAnalyst", "Bull", "Bear", "RiskManager", "Trader"];
  const names = order.filter(n => agents[n]).concat(Object.keys(agents).filter(n => !order.includes(n)));
  if (!names.length) {
    box.className = "empty";
    box.textContent = "검증된 에이전트 기록이 아직 없습니다. 다음 거래일 이후 '지난 예측 검증'을 실행하면 쌓입니다.";
    return;
  }
  box.className = "";
  box.innerHTML = `
    <table class="stat-table">
      <thead><tr><th>에이전트</th><th class="num">방향 적중</th><th class="num">근거 검증률</th><th class="num">기여</th></tr></thead>
      <tbody>
        ${names.map(n => {
          const a = agents[n];
          const contrib = (n !== "Trader" && a.total) ? `${a.contribution > 0 ? "+" : ""}${a.contribution}` : "—";
          return `<tr><td>${ANALYST_LABEL[n] || escapeHtml(n)}</td><td class="num">${a.total ? pctText(a.correct, a.total) : "—"}</td><td class="num">${a.evidence_total ? pctText(a.evidence_verified, a.evidence_total) : "—"}</td><td class="num" title="맞았는데 트레이더가 무시한 횟수 − 틀렸는데 트레이더가 걸러낸 횟수">${contrib}</td></tr>`;
        }).join("")}
      </tbody>
    </table>
    <div class="delta" style="font-size:12px;color:var(--text-muted)">기여 = 이 에이전트가 맞았는데 트레이더가 무시한 횟수 − 틀렸는데 트레이더가 걸러낸 횟수. 양수가 크면 트레이더가 더 들어야 할 에이전트.</div>
  `;
}

function equityCurveSVG(series, w = 520, h = 120) {
  // series: [{name, values(%), color}] — 같은 길이의 누적 수익률(%) 곡선
  const all = series.flatMap(x => x.values);
  if (!all.length) return "";
  const lo = Math.min(0, ...all), hi = Math.max(0, ...all);
  const pad = 6, span = (hi - lo) || 1;
  const n = Math.max(...series.map(x => x.values.length));
  const X = i => pad + (n > 1 ? i / (n - 1) : 0) * (w - 2 * pad);
  const Y = v => h - pad - ((v - lo) / span) * (h - 2 * pad);
  const zero = Y(0);
  return `<svg viewBox="0 0 ${w} ${h}" width="100%" height="${h}" role="img" aria-label="누적 수익률 곡선">
    <line x1="0" y1="${zero}" x2="${w}" y2="${zero}" stroke="var(--gridline, #ccc)" stroke-width="1"></line>
    ${series.map(x => `<polyline fill="none" stroke="${x.color}" stroke-width="2" points="${x.values.map((v, i) => `${X(i).toFixed(1)},${Y(v).toFixed(1)}`).join(" ")}"></polyline>`).join("")}
    ${series.map((x, k) => `<text x="${pad}" y="${14 + k * 14}" font-size="11" fill="${x.color}">${escapeHtml(x.name)} ${x.values[x.values.length - 1].toFixed(2)}%</text>`).join("")}
  </svg>`;
}

function perfRow(label, p, bold) {
  const f = (v, d = 2) => (typeof v === "number" ? v.toFixed(d) : "—");
  return `<tr${bold ? ' style="font-weight:600"' : ""}><td>${escapeHtml(label)}</td>
    <td class="num ${typeof p.total_return_pct === "number" ? (p.total_return_pct >= 0 ? "up" : "down") : ""}">${f(p.total_return_pct)}%</td>
    <td class="num">${p.sharpe_annual == null || (p.days || 0) < 20 ? '<span style="color:var(--text-muted)">표본 부족</span>' : f(p.sharpe_annual)}</td>
    <td class="num">${f(p.max_drawdown_pct)}%</td>
    <td class="num">${p.win_rate == null ? "—" : Math.round(p.win_rate * 100) + "%"}</td>
    <td class="num">${p.traded_days ?? "—"}/${p.days ?? "—"}</td></tr>`;
}

function renderReturns() {
  const box = $("#returnsBox");
  const rb = historyStats?.returns;
  if (!rb || !rb.days) {
    box.className = "empty";
    box.textContent = "검증된 기록이 쌓이면 여기에 수익률(롱=KODEX200, 쇼트=KODEX인버스, 현금)이 표시됩니다.";
    return;
  }
  box.className = "";
  const rows = [["AI 포지션", rb.ai, true], ["KOSPI 바이앤홀드", rb.buy_hold, false]]
    .concat(Object.values(rb.baselines || {}).map(b => [b.label + " (100%)", b, false]));
  const agents = Object.entries(rb.by_agent || {}).map(([n, p]) => [(ANALYST_LABEL[n] || n) + " (방향 100%)", p, false]);
  const verdict = rb.ai_beats_buy_hold && rb.ai_beats_best_baseline
    ? `<div class="delta" style="color:var(--up);font-size:12px">AI 포지션이 바이앤홀드와 최고 단순규칙보다 수익이 높습니다.</div>`
    : `<div class="delta" style="color:var(--critical);font-size:12px">아직 ${!rb.ai_beats_buy_hold ? "바이앤홀드" : "단순규칙(" + escapeHtml(rb.baselines?.[rb.best_baseline]?.label || "") + ")"}가 AI보다 수익이 높습니다.</div>`;
  box.innerHTML = `
    ${equityCurveSVG([{ name: "AI 포지션", values: rb.ai.equity_curve_pct || [], color: "var(--accent, #1f66d1)" },
                      { name: "바이앤홀드", values: rb.buy_hold.equity_curve_pct || [], color: "var(--text-muted, #888)" }])}
    <table class="stat-table">
      <thead><tr><th>방식</th><th class="num">누적</th><th class="num">샤프</th><th class="num">MDD</th><th class="num">승률</th><th class="num">거래일</th></tr></thead>
      <tbody>${rows.map(r => perfRow(...r)).join("")}${agents.length ? `<tr><td colspan="6" class="section-label" style="padding-top:8px">에이전트 말대로 했다면</td></tr>` + agents.map(r => perfRow(...r)).join("") : ""}</tbody>
    </table>
    ${verdict}
    <div class="delta" style="font-size:12px;color:var(--text-muted)">포지션 = (2p−1) × ½켈리 × 목표변동성15%/실현변동성, |2p−1|<0.1이면 현금. 왕복 비용 0.1% 차감. ${rb.proxy_days ? `${rb.proxy_days}일은 ETF 대신 지수 등락률로 근사.` : ""} 표본 ${rb.days}일 — 30일 미만은 참고만.</div>`;
}

function renderLessons() {
  const box = $("#lessonsBox");
  const ls = lastOutcome?.lessons_used || [];
  if (!lastOutcome) return;
  if (!ls.length) {
    box.className = "empty";
    box.textContent = "비슷한 상황의 복기 기록이 아직 없습니다. 빗나가거나 손실 난 날 채점 직후 복기 에이전트가 교훈을 남깁니다 (lessons.json).";
    return;
  }
  box.className = "";
  box.innerHTML = `<ul class="reasoning">${ls.map(r => `<li><b>${escapeHtml(r.target_date)}</b> 예측 ${r.direction === "up" ? "▲" : "▼"} → 실제 ${r.actual_direction === "up" ? "▲" : "▼"}${typeof r.strategy_return_pct === "number" ? ` (${r.strategy_return_pct.toFixed(2)}%)` : ""}: ${escapeHtml(r.lesson || "")}${r.cause ? `<div class="delta" style="font-size:12px;color:var(--text-muted)">원인: ${escapeHtml(r.cause)}</div>` : ""}</li>`).join("")}</ul>
    <div class="delta" style="font-size:12px;color:var(--text-muted)">상황 태그가 겹치는 교훈을 손실 크기·최신순으로 골라 중재자·트레이더에게 전달했습니다. 오늘 태그: ${(lastOutcome.situation_tags || []).map(escapeHtml).join(", ")}</div>`;
}

async function loadPaper() {
  const box = $("#paperBox");
  if (!box) return;
  let s = null;
  try { const r = await fetch("/api/paper", { credentials: "same-origin" }); if (r.ok) s = await r.json(); } catch (_) {}
  if (!s || !s.days) {
    box.className = "empty";
    box.textContent = `아직 체결 기록이 없습니다. 내일 마감 채점(15:45)부터 그날 포지션대로 ETF를 사고팔아 평가합니다. 시작 자금 ${Number(s?.initial_cash || 10000000).toLocaleString()}원.`;
    return;
  }
  box.className = "";
  const won = v => Number(v).toLocaleString(undefined, { maximumFractionDigits: 0 }) + "원";
  const eq = s.equity_curve.map(x => (x.equity / s.initial_cash - 1) * 100);
  const tiles = [["평가금액", won(s.equity), s.total_return_pct > 0 ? "up" : s.total_return_pct < 0 ? "down" : "", `${fmtPct(s.total_return_pct)} (${s.pnl >= 0 ? "+" : ""}${won(s.pnl)})`],
                 ["현금", won(s.cash), "", `보유 ${Object.entries(s.holdings || {}).map(([t, q]) => `${t.startsWith("069500") ? "KODEX200" : "인버스"} ${q}주`).join(", ") || "없음"}`],
                 ["샤프 / MDD", `${s.sharpe_annual ?? "—"} / ${s.max_drawdown_pct}%`, "", `승률 ${s.win_rate == null ? "—" : Math.round(s.win_rate * 100) + "%"}`],
                 ["거래", `${s.trades}건`, "", `수수료+슬리피지 ${won(s.fees)} · ${s.days}일`]];
  box.innerHTML = `
    <div class="kpi-row" style="margin-bottom:14px">${tiles.map(t => `<div class="tile"><div class="label">${t[0]}</div><div class="value ${t[2]}" style="font-size:22px">${t[1]}</div><div class="delta">${t[3]}</div></div>`).join("")}</div>
    ${equityCurveSVG([{ name: "가상 계좌", values: eq, color: "var(--accent)" }])}
    <div class="section-label">최근 일별 평가</div>
    <table class="stat-table"><thead><tr><th>거래일</th><th class="num">목표 포지션</th><th class="num">평가금액</th><th class="num">일수익</th><th class="num">누적</th><th class="num">체결</th></tr></thead>
    <tbody>${(s.recent_days || []).map(d => `<tr><td>${escapeHtml(d.date)}</td><td class="num">${d.target_position > 0 ? "롱" : d.target_position < 0 ? "쇼트" : "현금"} ${Math.round(Math.abs(d.target_position) * 100)}%</td><td class="num">${won(d.equity)}</td><td class="num ${d.day_return_pct >= 0 ? "up" : "down"}">${fmtPct(d.day_return_pct)}</td><td class="num">${fmtPct(d.total_return_pct)}</td><td class="num">${d.trades}</td></tr>`).join("")}</tbody></table>
    <div class="section-label">최근 체결</div>
    <table class="stat-table"><thead><tr><th>일자</th><th>종목</th><th class="num">매매</th><th class="num">수량</th><th class="num">체결가</th><th class="num">수수료</th></tr></thead>
    <tbody>${(s.recent_trades || []).map(t => `<tr><td>${escapeHtml(t.date)}</td><td>${t.ticker.startsWith("069500") ? "KODEX 200" : "KODEX 인버스"}</td><td class="num ${t.side === "buy" ? "up" : "down"}">${t.side === "buy" ? "매수" : "매도"}</td><td class="num">${t.qty}</td><td class="num">${Number(t.price).toLocaleString()}</td><td class="num">${won(t.fee)}</td></tr>`).join("")}</tbody></table>
    <div class="delta" style="color:var(--text-muted)">가상 체결입니다. 아침 분석의 포지션 비중대로 ETF를 사고팔아(분석이 08:40 전에 끝나면 장전 시간외 전일 종가, 아니면 시가 체결) 종가로 평가하며, 다음 날 다시 조정합니다.</div>`;
}

function renderBaselines() {
  const box = $("#baselineBox");
  const bc = historyStats?.baseline_compare;
  const today = lastOutcome?.baselines || {};
  const aiDir = lastOutcome?.final_result?.kospi_prediction?.direction;
  const arrow = d => d === "up" ? '<span class="up">▲</span>' : d === "down" ? '<span class="down">▼</span>' : "—";
  const acc = r => r && r.total ? `${r.correct}/${r.total} (${Math.round(r.accuracy * 100)}%)` : "—";
  const rows = [["AI 멀티에이전트", aiDir, bc?.ai, true]];
  const keys = Object.keys(bc?.baselines || today);
  keys.forEach(k => rows.push([(bc?.baselines?.[k]?.label) || today[k]?.label || k, today[k]?.direction, bc?.baselines?.[k], false]));
  box.className = "";
  const note = !bc || !bc.ai.total
    ? '<div class="delta" style="color:var(--text-muted);font-size:12px">검증된 기록이 쌓이면 같은 날짜 기준으로 적중률을 비교합니다. AI가 단순 규칙보다 꾸준히 높아야 의미가 있습니다.</div>'
    : (bc.best_baseline && bc.baselines[bc.best_baseline].accuracy > bc.ai.accuracy
        ? `<div class="delta" style="color:var(--critical);font-size:12px">현재 "${escapeHtml(bc.baselines[bc.best_baseline].label)}"가 AI보다 적중률이 높습니다.</div>`
        : '<div class="delta" style="color:var(--good);font-size:12px">현재 AI가 모든 단순 규칙 이상입니다.</div>');
  box.innerHTML = `
    <table class="stat-table">
      <thead><tr><th>방식</th><th class="num">오늘 예측</th><th class="num">누적 적중</th></tr></thead>
      <tbody>${rows.map(([n, d, r, ai]) => `<tr${ai ? ' style="font-weight:600"' : ""}><td>${escapeHtml(n)}</td><td class="num">${arrow(d)}</td><td class="num">${acc(r)}</td></tr>`).join("")}</tbody>
    </table>${note}`;
}

function calibrationTable(c, byOutlook, byDq) {
  if (!c || !c.n) return '<div class="delta" style="color:var(--text-muted);font-size:12px">확률 예측이 검증되면 "말한 확률 vs 실제 상승률"을 비교합니다 (V3.6.3 이후 기록).</div>';
  const labels = { strong_up: "강한 상승", mild_up: "약한 상승", flat: "보합", mild_down: "약한 하락", strong_down: "강한 하락" };
  const bo = byOutlook || {};
  return `
    <div class="section-label">확신도 보정 (Brier ${c.brier} · 0.25=동전던지기, 낮을수록 좋음)</div>
    <table class="stat-table">
      <thead><tr><th>말한 확률</th><th class="num">건수</th><th class="num">평균 예측</th><th class="num">실제 상승률</th></tr></thead>
      <tbody>${c.bins.map(b => `<tr><td>${b.range}</td><td class="num">${b.n}</td><td class="num">${b.avg_prob != null ? b.avg_prob + "%" : "—"}</td><td class="num">${b.actual_up_rate != null ? b.actual_up_rate + "%" : "—"}</td></tr>`).join("")}</tbody>
    </table>
    ${Object.keys(byDq || {}).length ? `<div class="section-label">토론 품질별 적중 (중재자)</div><table class="stat-table"><tbody>${
      ["resolved", "contested", "unresolved"].filter(k => byDq[k]).map(k => `<tr><td>${{ resolved: "한쪽 우세", contested: "팽팽함", unresolved: "미해결" }[k]}</td><td class="num">${byDq[k].correct}/${byDq[k].total}</td></tr>`).join("")}</tbody></table>` : ""}
    ${Object.keys(bo).length ? `<div class="section-label">전망 단계별 적중</div><table class="stat-table"><tbody>${
      ["strong_up", "mild_up", "flat", "mild_down", "strong_down"].filter(k => bo[k]).map(k => `<tr><td>${labels[k]}</td><td class="num">${bo[k].correct}/${bo[k].total}</td></tr>`).join("")}</tbody></table>` : ""}`;
}

function renderAll() {
  renderKPI();
  renderBoard();
  renderPrediction();
  renderKospiChart();
  renderStocks();
  renderDebate();
  renderRisk();
  renderSimilarCases();
  renderQA();
  renderMeta();
  renderBaselines();
  renderReturns();
  renderLessons();
  renderInputs();
  renderModeration();
  renderAnalysts();
}

// 수집한 뉴스 링크는 외부 데이터라 http/https만 허용 (javascript: 링크 차단)
function safeUrl(u) {
  try {
    const url = new URL(String(u), location.href);
    return (url.protocol === "http:" || url.protocol === "https:") ? escapeHtml(url.href) : "#";
  } catch (_) {
    return "#";
  }
}

function escapeHtml(str) {
  return String(str).replace(/[&<>"']/g, (c) => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;",
  }[c]));
}

// ------------------------------------------------------------- event chart --
function renderEventChart(events) {
  const wrap = $("#eventChart");
  if (!events.length) {
    wrap.innerHTML = '<div class="empty">이벤트가 없습니다. 아래에서 수집을 먼저 실행하세요.</div>';
    return;
  }
  const w = Math.max(events.length * 46, 400);
  const h = 180;
  const midY = h / 2;
  const maxAbs = Math.max(...events.map(e => Math.abs(e.change_pct)), 1);
  const scale = (midY - 20) / maxAbs;

  const bars = events.map((e, i) => {
    const barW = 26;
    const x = i * 46 + 10;
    const barH = Math.abs(e.change_pct) * scale;
    const y = e.change_pct >= 0 ? midY - barH : midY;
    const color = e.change_pct >= 0 ? "var(--up)" : "var(--down)";
    return `
      <g class="bar-tooltip-target" data-event-id="${Number(e.id)}" data-date="${escapeHtml(e.event_date)}">
        <rect x="${x}" y="${y}" width="${barW}" height="${Math.max(barH, 1)}" rx="3" fill="${color}"></rect>
        <text x="${x + barW / 2}" y="${h - 6}" text-anchor="middle" font-size="9" fill="var(--text-muted)">${escapeHtml(String(e.event_date).slice(5))}</text>
        <title>${escapeHtml(e.event_date)} ${escapeHtml(e.stock_name)} ${e.change_pct > 0 ? "+" : ""}${e.change_pct}%</title>
      </g>
    `;
  }).join("");

  wrap.innerHTML = `
    <svg width="${w}" height="${h}" viewBox="0 0 ${w} ${h}">
      <line x1="0" y1="${midY}" x2="${w}" y2="${midY}" stroke="var(--baseline, var(--gridline))" stroke-width="1"></line>
      ${bars}
    </svg>
  `;

  wrap.querySelectorAll(".bar-tooltip-target").forEach(g => {
    g.addEventListener("click", () => loadNewsForEvent(g.dataset.eventId, g.dataset.date));
  });
}

async function loadNewsForEvent(eventId, dateLabel) {
  const panel = $("#newsPanel");
  panel.innerHTML = '<div class="empty">불러오는 중…</div>';
  try {
    const news = await fetchJSON(`/api/events/${eventId}/news`);
    if (!news.length) {
      panel.innerHTML = `<div class="section-label">${escapeHtml(dateLabel)} 관련 뉴스</div><div class="empty">수집된 뉴스가 없습니다.</div>`;
      return;
    }
    panel.innerHTML = `
      <div class="section-label">${escapeHtml(dateLabel)} 관련 뉴스 (${news.length}건)</div>
      ${news.slice(0, 15).map(n => `
        <div class="news-row">
          <a href="${safeUrl(n.link)}" target="_blank" rel="noopener noreferrer">${escapeHtml(n.title)}</a>
          <span class="src">${escapeHtml(n.source || "")}</span>
        </div>
      `).join("")}
    `;
  } catch (e) {
    panel.innerHTML = `<div class="empty">뉴스를 불러오지 못했습니다: ${escapeHtml(e.message)}</div>`;
  }
}

async function loadEventStocks(selectAfter) {
  const stocks = await fetchJSON("/api/events/stocks");
  const sel = $("#eventStockSelect");
  const current = selectAfter || sel.value;
  sel.innerHTML = '<option value="">종목 선택…</option>' + stocks.map(s => `<option value="${escapeHtml(s)}">${escapeHtml(s)}</option>`).join("");
  if (current && stocks.includes(current)) {
    sel.value = current;
    await loadEvents(current);
  }
}

async function loadEvents(stockName) {
  if (!stockName) {
    renderEventChart([]);
    return;
  }
  const events = await fetchJSON(`/api/events?stock=${encodeURIComponent(stockName)}`);
  renderEventChart(events);
  $("#newsPanel").innerHTML = "";
}

// ------------------------------------------------------------------ init --
async function checkHealth() {
  try {
    const h = await fetchJSON("/api/health");
    const badge = $("#apiBadge");
    if (h.readonly) {
      // 보기 전용 모드: 실행/수집 UI를 숨긴다 (서버도 POST를 거부함)
      ["#btnDemo", "#btnReal"].forEach(s => { const b = $(s); if (b) b.hidden = true; });
      const form = document.querySelector(".collect-form");
      if (form) form.hidden = true;
      badge.className = "badge ok";
      badge.innerHTML = '<span class="dot"></span>보기 전용 모드';
      return;
    }
    if (h.has_api_key) {
      badge.className = "badge ok";
      badge.innerHTML = '<span class="dot"></span>API 키 설정됨';
    } else {
      badge.className = "badge warn";
      badge.innerHTML = '<span class="dot"></span>API 키 미설정 (데모만 가능)';
      $("#btnReal").disabled = true;
      $("#btnReal").title = "터미널에서 python cli.py set-key gemini 로 키를 등록하세요";
    }
  } catch {
    const badge = $("#apiBadge");
    badge.className = "badge warn";
    badge.innerHTML = '<span class="dot"></span>서버 연결 실패';
  }
}

async function loadHistory() {
  try {
    const h = await fetchJSON("/api/history");
    historyStats = h.stats;
    renderHistory();
    renderAgents();
    renderBaselines();
    renderKPI();
  } catch (e) {
    toast("과거 기록을 불러오지 못했습니다: " + e.message);
  }
}

async function runPipeline(mode) {
  const btn = mode === "real" ? $("#btnReal") : $("#btnDemo");
  const otherBtn = mode === "real" ? $("#btnDemo") : $("#btnReal");
  const originalText = btn.textContent;
  const previousDisabled = [btn.disabled, otherBtn.disabled];
  btn.disabled = true;
  otherBtn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> 실행 중…`;
  try {
    lastOutcome = await fetchJSON("/api/run", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ mode }),
    });
    lastMeta = { saved_at: new Date().toISOString().slice(0, 19), mode };
    renderAll();
    if (mode === "real") await loadHistory();
  } catch (e) {
    toast("실행 실패: " + e.message);
  } finally {
    [btn.disabled, otherBtn.disabled] = previousDisabled;
    btn.textContent = originalText;
  }
}

async function submitCollect() {
  const name = $("#collectName").value.trim();
  const start = $("#collectStart").value;
  const end = $("#collectEnd").value;
  const threshold = parseFloat($("#collectThreshold").value) || 3.0;
  if (!name || !start || !end) {
    toast("종목명/시작일/종료일을 모두 입력하세요.");
    return;
  }
  const btn = $("#btnCollect");
  const original = btn.textContent;
  btn.disabled = true;
  btn.innerHTML = `<span class="spinner"></span> 수집 중…`;
  try {
    const result = await fetchJSON("/api/collect", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ stock_name: name, start, end, threshold }),
    });
    toast(`수집 완료: 이벤트 ${result.events}건, 뉴스 ${result.news}건`);
    await loadEventStocks(name);
  } catch (e) {
    toast("수집 실패: " + e.message);
  } finally {
    btn.disabled = false;
    btn.textContent = original;
  }
}

async function loadLatest() {
  try {
    const res = await fetch("/api/latest", { credentials: "same-origin" });
    if (!res.ok) return;
    const data = await res.json();
    lastOutcome = data.outcome;
    lastMeta = { saved_at: data.saved_at, mode: data.mode };
    renderAll();
  } catch (_) { /* 저장된 결과 없음 */ }
}

async function loadBacktest() {
  const box = $("#backtestBox");
  try {
    const res = await fetch("/api/backtest", { credentials: "same-origin" });
    if (!res.ok) { box.className = "empty"; box.textContent = "백테스트 결과가 없습니다 (백테스트_한달.bat 실행)."; return; }
    const d = await res.json();
    const s = d.summary || {};
    const pct = r => r && r.total ? `${r.correct}/${r.total} (${Math.round((r.correct / r.total) * 100)}%)` : "—";
    const runsTable = (d.runs || []).length > 1 ? `
      <div class="section-label">버전 비교 (같은 기간)</div>
      <table class="stat-table">
        <thead><tr><th>버전</th><th class="num">AI 적중</th><th class="num">최고 단순규칙</th><th class="num">모의수익</th></tr></thead>
        <tbody>${d.runs.map(r => {
          const bs = Object.values(r.summary.baseline_compare || {}).filter(b => b.accuracy != null);
          const best = bs.length ? bs.reduce((a, b) => (b.accuracy > a.accuracy ? b : a)) : null;
          const tr = r.summary.returns?.days ? r.summary.returns.ai.total_return_pct / 100 : r.summary.performance?.total_return;
          return `<tr${r.file === d.current ? ' style="font-weight:600"' : ""}><td>${escapeHtml(r.meta.label || r.file)}</td>
            <td class="num">${pct({ total: r.summary.total, correct: r.summary.correct })}</td>
            <td class="num">${best ? `${escapeHtml(best.label)} ${Math.round(best.accuracy * 100)}%` : "—"}</td>
            <td class="num">${typeof tr === "number" ? (tr * 100).toFixed(2) + "%" : "—"}</td></tr>`;
        }).join("")}</tbody>
      </table>` : "";
    const rows = [["AI 멀티에이전트", { total: s.total, correct: s.correct }, true]]
      .concat(Object.values(s.baseline_compare || {}).map(b => [b.label, b, false]));
    const perf = s.performance || {};
    const fmt = v => (typeof v === "number" ? (Math.abs(v) < 5 ? (v * 100).toFixed(2) + "%" : v.toFixed(2)) : "—");
    box.className = "";
    box.innerHTML = `
      <div class="delta" style="color:var(--text-muted);font-size:12px;margin-bottom:6px">
        ${escapeHtml(d.meta.start || "")} ~ ${escapeHtml(d.meta.end || "")} · ${escapeHtml(d.meta.model || "")} ·
        AI가 과거 흐름을 알고 있을 수 있어 실제보다 낙관적인 참고치입니다.</div>
      ${runsTable}
      <table class="stat-table">
        <thead><tr><th>방식</th><th class="num">적중</th></tr></thead>
        <tbody>${rows.map(([n, r, ai]) => `<tr${ai ? ' style="font-weight:600"' : ""}><td>${escapeHtml(n)}</td><td class="num">${pct(r)}</td></tr>`).join("")}</tbody>
      </table>
      ${Object.keys(s.by_analyst || {}).length ? `<div class="section-label">분석가별 적중 · 기여</div><table class="stat-table"><thead><tr><th>분석가</th><th class="num">적중</th><th class="num">기여</th></tr></thead><tbody>${
        Object.entries(s.by_analyst).map(([n, a]) => `<tr><td>${ANALYST_LABEL[n] || escapeHtml(n)}</td><td class="num">${pct(a)}</td><td class="num">${a.contribution > 0 ? "+" : ""}${a.contribution}</td></tr>`).join("")}</tbody></table>` : ""}
      ${calibrationTable(s.calibration, s.by_outlook, s.by_debate_quality)}
      ${s.returns && s.returns.days ? `<div class="section-label">수익률 (롱/쇼트/현금 포지션 · 비용 차감)</div>
      ${equityCurveSVG([{ name: "AI 포지션", values: s.returns.ai.equity_curve_pct || [], color: "var(--accent, #1f66d1)" },
                        { name: "바이앤홀드", values: s.returns.buy_hold.equity_curve_pct || [], color: "var(--text-muted, #888)" }])}
      <table class="stat-table"><thead><tr><th>방식</th><th class="num">누적</th><th class="num">샤프</th><th class="num">MDD</th><th class="num">승률</th><th class="num">거래일</th></tr></thead>
      <tbody>${[["AI 포지션", s.returns.ai, true], ["KOSPI 바이앤홀드", s.returns.buy_hold, false]].concat(Object.values(s.returns.baselines || {}).map(b => [b.label + " (100%)", b, false])).map(r => perfRow(...r)).join("")}
      ${Object.entries(s.returns.by_agent || {}).map(([n, p]) => perfRow((ANALYST_LABEL[n] || n) + " (방향 100%)", p, false)).join("")}</tbody></table>` : ""}
      <div class="section-label">${escapeHtml(d.meta.label || d.current)} · 모의 투자 성과 (포트폴리오 규칙 적용)</div>
      <table class="stat-table"><tbody>
        ${[["누적 수익률", perf.total_return], ["KOSPI 보유", perf.benchmark_total_return], ["MDD", perf.mdd], ["승률", perf.win_rate]]
          .map(([k, v]) => `<tr><td>${k}</td><td class="num">${fmt(v)}</td></tr>`).join("")}
      </tbody></table>
      <div class="section-label">일별 결과</div>
      <table class="stat-table">
        <thead><tr><th>거래일</th><th class="num">AI</th><th class="num">실제</th><th class="num">결과</th></tr></thead>
        <tbody>${(d.days || []).slice().reverse().map(x => `<tr><td>${escapeHtml(x.target_session)}</td>
          <td class="num">${x.predicted === "up" ? '<span class="up">▲</span>' : '<span class="down">▼</span>'} ${x.up_probability != null ? Math.round(x.up_probability) + "%" : escapeHtml(x.confidence || "")}</td>
          <td class="num">${x.actual === "up" ? '<span class="up">▲</span>' : '<span class="down">▼</span>'} ${Number(x.actual_change_pct).toFixed(2)}%</td>
          <td class="num">${x.correct ? "적중" : "빗나감"}${typeof x.strategy_return_pct === "number" ? ` · <span class="${x.strategy_return_pct >= 0 ? "up" : "down"}">${x.strategy_return_pct.toFixed(2)}%</span>${x.position ? ` (${x.position > 0 ? "롱" : "쇼트"} ${Math.round(Math.abs(x.position) * 100)}%)` : " (현금)"}` : ""}</td></tr>`).join("")}</tbody>
      </table>`;
  } catch (e) {
    box.className = "empty";
    box.textContent = "백테스트 결과를 불러오지 못했습니다.";
  }
}

function init() {
  checkHealth();
  loadLatest();
  loadHistory();
  loadBacktest();
  loadEventStocks().catch(e => toast("이벤트 조회 실패: " + e.message));

  $("#btnDemo").addEventListener("click", () => runPipeline("demo"));
  $("#btnReal").addEventListener("click", () => {
    if (confirm("실제 분석은 설정된 제공자의 API를 호출합니다(과금될 수 있음). 실행할까요?")) runPipeline("real");
  });
  $("#btnCollect").addEventListener("click", submitCollect);
  $("#btnRefreshEvents").addEventListener("click", () => loadEventStocks().catch(e => toast(e.message)));
  $("#eventStockSelect").addEventListener("change", (e) => loadEvents(e.target.value).catch(err => toast(err.message)));
}

// ---- V3.17 탭 내비게이션 (해시 기반, 새로고침해도 유지) ----
function activateTab(name) {
  const tabs = document.querySelectorAll("#tabs a");
  const valid = Array.from(tabs).some(a => a.dataset.tab === name);
  if (!valid) name = "today";
  tabs.forEach(a => a.classList.toggle("active", a.dataset.tab === name));
  document.querySelectorAll(".tab-panel").forEach(p => p.classList.toggle("active", p.id === "tab-" + name));
  try { localStorage.setItem("ta.tab", name); } catch (_) {}
}
function initTabs() {
  const fromHash = (location.hash || "").replace("#", "");
  let saved = "";
  try { saved = localStorage.getItem("ta.tab") || ""; } catch (_) {}
  activateTab(fromHash || saved || "today");
  window.addEventListener("hashchange", () => activateTab((location.hash || "").replace("#", "")));
}

document.addEventListener("DOMContentLoaded", () => { initTheme(); initTabs(); init(); loadKospi(); loadMarket(); loadPaper(); setInterval(loadMarket, 600000);
  tickClock(); setInterval(tickClock, 1000); loadLive(); scheduleLive();
  document.addEventListener("visibilitychange", () => { if (!document.hidden) loadLive(); }); });
