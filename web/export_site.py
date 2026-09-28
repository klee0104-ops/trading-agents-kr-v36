"""
공유용 읽기 전용 스냅샷 페이지 생성기.

- 실행 버튼·수집 폼·API 호출이 전혀 없는 "정적 HTML 한 장"을 만든다.
  → 누가 열어봐도 실제 분석(=API 과금)을 실행할 방법 자체가 없다.
- 데이터는 허용 목록(whitelist)에 있는 필드만 골라 넣고, 마지막에 키 마스킹(redact)을 한 번 더 거친다.
- 출력
    site/index.html              : 어디서나 열 수 있는 완전한 HTML (더블클릭/정적 호스팅)
    site/snapshot_fragment.html  : claude.ai Artifact 게시용 (문서 뼈대 없이 본문만)
"""
from __future__ import annotations

import json
import re
import html
from datetime import datetime
from pathlib import Path

from config import Config
from memory.store import MemoryStore
from memory.outcome_store import load_last_outcome
from security.secret_store import redact, SECRET_PATTERNS, KEY_NAMES, get_api_key
from rules.baselines import baseline_predictions, BASELINES

ROOT = Path(__file__).resolve().parent.parent
SITE_DIR = ROOT / "site"

_STOCK_FIELDS = ("name", "sector", "related_us_indicator", "sensitivity", "directional_signal", "reason",
                 "moderated", "original_signal", "moderation_reason")


_NEWS_PREFIX = re.compile(r"^(\[[^\]]*\]\s*)+")
_REDACTED = "(뉴스 제목 인용 — 공유본에서는 생략)"


def _news_cores(titles) -> list[str]:
    """'[날짜][종목] 제목' 형식에서 제목 본문만. 짧은 것은 오탐 방지를 위해 제외."""
    out = []
    for t in titles or []:
        core = _NEWS_PREFIX.sub("", str(t)).strip()
        if len(core) >= 10:
            out.append(core)
    return out


def _redact_news(text, cores: list[str]):
    """뉴스 제목이 문장째 들어간 텍스트는 통째로 가린다. (공유본에는 뉴스 원문을 싣지 않는다)"""
    if not isinstance(text, str) or not cores:
        return text
    s = re.sub(r"\s+", "", text)
    for c in cores:
        if re.sub(r"\s+", "", c) in s:
            return _REDACTED
    return text


def _redact_deep(obj, cores: list[str]):
    if isinstance(obj, str):
        return _redact_news(obj, cores)
    if isinstance(obj, list):
        return [_redact_deep(x, cores) for x in obj]
    if isinstance(obj, dict):
        return {k: _redact_deep(v, cores) for k, v in obj.items()}
    return obj


_PERF_KEYS = ("days", "total_return_pct", "sharpe_annual", "max_drawdown_pct", "win_rate", "traded_days", "equity_curve_pct")


def _returns_public(rb) -> dict | None:
    """수익률 블록에서 공개 가능한 요약만 (곡선 포함, 날짜별 개별 수익은 제외)."""
    if not rb or not rb.get("days"):
        return None
    out = {"days": rb["days"], "proxy_days": rb.get("proxy_days"), "best_baseline": rb.get("best_baseline"),
           "ai_beats_buy_hold": rb.get("ai_beats_buy_hold"), "ai_beats_best_baseline": rb.get("ai_beats_best_baseline"),
           "ai": _pick(rb.get("ai") or {}, _PERF_KEYS), "buy_hold": _pick(rb.get("buy_hold") or {}, _PERF_KEYS),
           "baselines": {k: {"label": v.get("label"), **_pick(v, _PERF_KEYS[:-1])} for k, v in (rb.get("baselines") or {}).items()},
           "by_agent": {k: _pick(v, _PERF_KEYS[:-1]) for k, v in (rb.get("by_agent") or {}).items()}}
    return out


_PAPER_FIELDS = ("days", "initial_cash", "equity", "pnl", "total_return_pct", "sharpe_annual",
                 "max_drawdown_pct", "win_rate", "trades", "fees", "equity_curve", "recent_days",
                 "cash", "holdings", "recent_trades")


def _paper_public() -> dict | None:
    try:
        from execution.paper import PaperAccount
        sm = PaperAccount(Config.PAPER_ACCOUNT_PATH, Config.PAPER_INITIAL_CASH).summary()
    except Exception:  # noqa: BLE001
        return None
    if not sm.get("days"):
        return None
    return _pick(sm, _PAPER_FIELDS)


def _pick(d: dict, keys) -> dict:
    return {k: d.get(k) for k in keys if d.get(k) is not None}


def build_snapshot_data() -> dict:
    """공개해도 되는 필드만 모은 스냅샷 데이터."""
    last = load_last_outcome(Config.LAST_OUTCOME_PATH) or {}
    o = last.get("outcome") or {}
    fr = o.get("final_result") or {}
    pred = fr.get("kospi_prediction") or {}
    dq = o.get("debate_qa") or []
    qa_sum = {"bull": [0, 0], "bear": [0, 0]}
    for r in dq:
        if r.get("stance") in qa_sum:
            qa_sum[r["stance"]][0] += int(r.get("verified_count") or 0)
            qa_sum[r["stance"]][1] += int(r.get("total_count") or 0)
    v = o.get("verification") or {}
    f = o.get("final_qa") or {}
    ma = o.get("market_analysis") or {}
    risk = o.get("risk_assessment") or {}

    memory = MemoryStore(Config.MEMORY_DB_PATH)
    stats = memory.compute_stats()
    records = sorted(memory.all_records(), key=lambda r: r.get("target_date", ""), reverse=True)[:30]

    latest = records[0] if records else {}
    today_base = {k: v.get("direction") for k, v in (o.get("baselines") or {}).items()} \
        or baseline_predictions(latest.get("us_indices", []))
    sn = o.get("snapshot") or {}
    kh = o.get("kospi_history")
    # (V3.19) 저장된 분석에 시세/차트가 없으면(구버전 결과) 공유 페이지 생성 시점에 시세만 보충한다. LLM 미사용.
    if not sn.get("us_indices") or not kh:
        try:
            from datetime import date as _d
            from data.market_data import MarketDataProvider
            prov = MarketDataProvider()
            if not kh:
                h = prov.fetch_kospi_history(_d.today(), sessions=80)
                kh = {"dates": h["dates"], "closes": [round(c, 2) for c in h["closes"]]}
            if not sn.get("us_indices"):
                live = prov.fetch_us_snapshot()
                sn = {"as_of": live.as_of,
                      "us_indices": [{"name": q.name, "change_pct": q.change_pct, "value": q.value} for q in live.us_indices],
                      "macro": [{"name": q.name, "change_pct": q.change_pct, "value": q.value} for q in (live.macro or [])],
                      "kr_flow": live.kr_flow}
        except Exception as e:  # noqa: BLE001
            print(f"[공유 페이지] 시세 보충 실패(무시): {e}")
    cores = _news_cores(o.get("news_titles"))
    data = {
        "inputs": {
            "as_of": sn.get("as_of"),
            "us": [_pick(q, ("name", "change_pct", "value")) for q in (sn.get("us_indices") or [])],
            "macro": [_pick(q, ("name", "change_pct", "value")) for q in (sn.get("macro") or [])],
            "kr_flow": _pick(sn.get("kr_flow") or {}, ("date", "foreign_net_krw_bn", "direction", "strong")) or None,
        },
        "target_date": o.get("target_date") or latest.get("target_date"),
        "baselines_today": {k: {"label": BASELINES[k], "direction": d} for k, d in today_base.items() if k in BASELINES},
        "baseline_compare": stats.get("baseline_compare"),
        "returns": _returns_public(stats.get("returns")),
        "paper": _paper_public(),  # User-authorized simulated returns and trades.
        "generated_at": datetime.now().isoformat(timespec="minutes"),
        "analysis_at": last.get("saved_at"),
        "prediction": {
            "direction": pred.get("direction"),
            "confidence": pred.get("confidence"),
            "up_probability": pred.get("up_probability"),
            "outlook_label": pred.get("outlook_label"),
            "reasoning": [str(x) for x in (pred.get("reasoning") or [])][:6],
            "position": pred.get("position"),
            "position_reason": (pred.get("position_info") or {}).get("reason"),
            "calibration_note": pred.get("calibration_note"),
        },
        "lessons_used": [_pick(r, ("target_date", "direction", "actual_direction", "lesson")) for r in (o.get("lessons_used") or [])][:5],
        "realized_vol": (o.get("quant") or {}).get("realized_vol_20"),
        "kospi_history": kh,
        "quant": _pick(o.get("quant") or {}, ("rsi14", "bb_pct_b", "realized_vol_20", "mom_12_1", "flow_z5", "labels", "ma", "macd")),
        "core_stocks": [_pick(s, _STOCK_FIELDS) for s in (fr.get("core_stocks") or [])][:4],
        "watch_stocks": [_pick(s, _STOCK_FIELDS) for s in (fr.get("watch_stocks") or [])][:12],
        "debate": [
            {"round": t.get("round"), "stance": t.get("stance"), "argument": t.get("argument"),
             "confidence": t.get("confidence"), "evidence": (t.get("evidence") or [])[:4]}
            for t in (o.get("debate_transcript") or [])
        ][:10],
        "qa": {
            "bull": qa_sum["bull"], "bear": qa_sum["bear"],
            "risk_stage_passed": v.get("verification_passed"),
            "final_risk_level": v.get("final_risk_level"),
            "final_consistent": f.get("consistent"),
            "llm_triggered": f.get("llm_verification_triggered"),
            "regime": _pick(ma, ("regime_trend", "regime_volatility", "risk_level_hint")),
        },
        "risk": _pick(risk, ("risk_level", "recommended_confidence", "summary")),
        "analysts": {n: _pick(r, ("direction", "confidence", "summary")) for n, r in (o.get("analyst_reports") or {}).items()},
        "news_universe": [str(u.get("name")) for u in (o.get("news_universe") or [])],
        "stock_rank": {str(u.get("name")): u.get("rank") for u in (o.get("news_universe") or []) if u.get("rank")},
        "stock_moderation": fr.get("stock_moderation"),
        "news_count": len(o.get("news_titles") or []),
        "moderation": _pick(o.get("moderation") or {}, ("debate_quality", "stronger_side", "summary",
                                                         "agreed_facts", "key_disputes", "noise")),
        "stats": {
            "total": stats.get("total"), "correct": stats.get("correct"), "accuracy": stats.get("accuracy"),
            "by_confidence": stats.get("by_confidence"),
            "by_agent": {k: _pick(a, ("total", "correct", "accuracy", "evidence_total", "evidence_verified",
                                       "evidence_verify_rate", "contribution"))
                         for k, a in (stats.get("by_agent") or {}).items()},
            "calibration": stats.get("calibration"),
            "by_outlook": stats.get("by_outlook"),
            "by_stock": {k: _pick(b, ("total", "correct", "accuracy"))
                         for k, b in sorted((stats.get("by_stock") or {}).items(),
                                            key=lambda kv: -kv[1].get("total", 0))[:10]},
        },
        "records": [
            _pick(r, ("target_date", "run_date", "direction", "confidence", "verified",
                      "actual_direction", "correct", "actual_session_date", "up_probability", "outlook"))
            for r in records
        ],
    }
    # 뉴스 원문(제목)은 공유본에 싣지 않는다: 토론 근거/분석가 요약/중재 정리에 문장째 인용된 것은 가린다.
    for key in ("debate", "analysts", "moderation", "prediction"):
        data[key] = _redact_deep(data[key], cores)
    return data


_CSS = """
:root{
  color-scheme:dark;
  --bg:#0b0e14; --panel:#12161f; --panel-2:#171c27; --ink:#e8ecf3; --ink-2:#b3bccc; --muted:#7c8799;
  --line:#242c3b; --up:#ff5a5f; --down:#4f8dff; --up-soft:rgba(255,90,95,.14); --down-soft:rgba(79,141,255,.16);
  --good:#3ddc84; --warn:#f5b942; --accent:#4f8dff;
}
:root[data-theme="light"]{
  color-scheme:light;
  --bg:#f4f5f8; --panel:#ffffff; --panel-2:#f1f3f7; --ink:#12161f; --ink-2:#4b5567; --muted:#8a93a3;
  --line:#e1e5ec; --up:#e3323a; --down:#1f6fe0; --up-soft:#fbe9ea; --down-soft:#e6effd;
  --good:#16a34a; --warn:#d69e2e; --accent:#1f6fe0;
}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--ink);
  font-family:"IBM Plex Sans KR",system-ui,-apple-system,"Malgun Gothic",sans-serif;font-size:15px;line-height:1.65}
.wrap{max-width:1200px;margin:0 auto;padding-inline:20px;padding-block:24px 56px;display:grid;gap:20px}
.topbar{position:sticky;top:0;z-index:20;background:var(--panel);border-bottom:1px solid var(--line)}
.topbar-inner{max-width:1200px;margin:0 auto;padding:10px 20px;display:flex;align-items:center;gap:20px;flex-wrap:wrap}
.brand{display:flex;align-items:center;gap:10px}
.brand .logo{width:34px;height:34px;border-radius:9px;background:var(--accent);color:#fff;display:grid;place-items:center;font-weight:800;font-size:13px}
.brand b{display:block;font-size:16px;line-height:1.1}.brand small{display:block;font-size:11.5px;color:var(--muted);margin-top:3px}
.tabs{display:flex;gap:2px;flex:1;flex-wrap:wrap}
.tabs a{text-decoration:none;color:var(--ink-2);font-size:14px;font-weight:500;padding:9px 14px;border-radius:8px;white-space:nowrap}
.tabs a:hover{background:var(--bg);color:var(--ink)} .tabs a.active{background:var(--accent);color:#fff;font-weight:600}
.panel{display:none}.panel.active{display:grid;gap:20px}
.hero{display:grid;grid-template-columns:1.7fr 1fr;gap:20px;align-items:start}
@media (max-width:900px){.hero{grid-template-columns:1fr}.tabs{order:3;width:100%;overflow-x:auto}}
.hero-card{padding:26px}.hero-card .pill{font-size:20px;padding:8px 18px}.hero-card .chip{font-size:14px;padding:5px 12px}
.hero-card ul{font-size:15.5px;line-height:1.7}
.h2-sub{font-weight:400;color:var(--muted);font-size:13px;margin-left:8px}
.mono,.num{font-family:"IBM Plex Mono",ui-monospace,Consolas,monospace;font-variant-numeric:tabular-nums}
header{display:flex;flex-wrap:wrap;justify-content:space-between;align-items:flex-end;gap:12px}
h1{margin:0;font-size:22px;letter-spacing:-.01em;text-wrap:balance}
.sub{color:var(--ink-2);font-size:13px}
.ro{display:inline-flex;align-items:center;gap:6px;font-size:12px;color:var(--ink-2);
  border:1px solid var(--line);background:var(--panel);border-radius:999px;padding:4px 10px}
.ro b{color:var(--ink)}
.kpis{display:grid;grid-template-columns:1fr 1fr;gap:12px}
.kpi{background:var(--panel);border:1px solid var(--line);border-radius:10px;padding:14px 16px;display:grid;gap:4px}
.kpi .l{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.kpi .v{font-size:30px;font-weight:700}
.kpi .d{font-size:13px;color:var(--ink-2)}
.up{color:var(--up)} .down{color:var(--down)}
.grid{display:grid;grid-template-columns:1fr 1fr;gap:20px;align-items:start}
@media (max-width:820px){.grid{grid-template-columns:1fr}}
section{background:var(--panel);border:1px solid var(--line);border-radius:14px;padding:22px 24px;display:grid;gap:12px;min-width:0}
h2{margin:0;font-size:17px;font-weight:700;letter-spacing:-.01em}
.pill{display:inline-flex;align-items:center;gap:6px;border-radius:6px;padding:3px 10px;font-weight:600;font-size:13px}
.pill.up{background:var(--up-soft)} .pill.down{background:var(--down-soft)}
.chip{font-size:12px;border:1px solid var(--line);border-radius:999px;padding:2px 9px;color:var(--ink-2)}
.chip.ok{color:var(--good);border-color:currentColor} .chip.bad{color:var(--up);border-color:currentColor}
ul{margin:0;padding-left:18px;display:grid;gap:4px}
.stocks{display:grid;grid-template-columns:repeat(auto-fill,minmax(220px,1fr));gap:14px}
.stock{border:1px solid var(--line);border-radius:12px;padding:14px 16px;display:grid;gap:4px}
.stock .n{font-weight:700;font-size:16px;display:flex;justify-content:space-between;gap:8px}
.stock .m{font-size:12.5px;color:var(--muted)} .stock .r{font-size:13.5px;color:var(--ink-2);line-height:1.6}
.label{font-size:11px;letter-spacing:.06em;text-transform:uppercase;color:var(--muted)}
.tbl{overflow-x:auto}
table{width:100%;border-collapse:collapse;font-size:13.5px}
th,td{text-align:left;padding:9px 10px;border-bottom:1px solid var(--line);white-space:nowrap}
th{font-weight:500;color:var(--muted);font-size:12px} td.num,th.num{text-align:right}
.turn{display:grid;grid-template-columns:56px 1fr;gap:12px;padding-block:12px;border-bottom:1px solid var(--line);font-size:14.5px;line-height:1.7}
.turn:last-child{border-bottom:0}
.tag{font-size:11px;font-weight:700;letter-spacing:.05em;padding:2px 0;text-align:center;border-radius:4px}
.tag.bull{color:var(--up);background:var(--up-soft)} .tag.bear{color:var(--down);background:var(--down-soft)}
.ev{font-size:13px;color:var(--muted);line-height:1.55}
.empty{color:var(--muted);font-size:14px}
.ticker{display:flex;gap:6px;overflow-x:auto;padding-bottom:4px}
.tk{flex:0 0 auto;display:grid;gap:2px;min-width:128px;padding:8px 12px;border:1px solid var(--line);border-radius:8px;background:var(--panel)}
.tk .nm{font-size:11.5px;color:var(--muted)} .tk .val{font-size:14px} .tk .chg{font-size:13px;font-weight:600}
.chg.up{color:var(--up)} .chg.down{color:var(--down)} .chg.flat{color:var(--muted)}
.chart-head{display:flex;align-items:baseline;gap:14px;flex-wrap:wrap}
.chart-head .px{font-size:30px;font-weight:700} .chart-head .lbl{color:var(--muted);font-size:12.5px}
.kchart{width:100%;height:auto;display:block}
.kchart .g{stroke:var(--line)} .kchart .ax{fill:var(--muted);font-size:11px;font-family:"IBM Plex Mono",monospace}
.kchart .price{fill:none;stroke:var(--ink);stroke-width:1.8} .kchart .ma20{fill:none;stroke:var(--warn);stroke-width:1.1} .kchart .ma60{fill:none;stroke:var(--accent);stroke-width:1.1}
.legend{display:flex;gap:14px;font-size:12px;color:var(--muted);flex-wrap:wrap} .legend i{display:inline-block;width:14px;height:2px;vertical-align:middle;margin-right:5px}
.inds{display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px}
.ind{padding:8px 10px;border:1px solid var(--line);border-radius:8px;background:var(--panel-2)} .ind .k{font-size:11px;color:var(--muted)} .ind .v{font-size:14px;font-weight:600;margin-top:2px} .ind .t{font-size:11.5px;color:var(--ink-2)}
.why-dir{font-size:30px;font-weight:800} .why-dir.up{color:var(--up)} .why-dir.down{color:var(--down)}
.gauge{position:relative;height:14px;border-radius:999px;background:linear-gradient(90deg,var(--down) 0%,var(--panel-2) 50%,var(--up) 100%)}
.gauge .needle{position:absolute;top:-5px;width:4px;height:24px;background:var(--ink);border-radius:2px;transform:translateX(-2px);box-shadow:0 0 0 2px var(--panel)}
.gauge-lbl{display:flex;justify-content:space-between;font-size:11.5px;color:var(--muted);margin-top:4px}
.evidence{display:grid;gap:8px;margin:0;padding:0;list-style:none}
.evidence li{display:grid;grid-template-columns:26px 1fr;gap:10px;padding:10px 12px;border-radius:10px;background:var(--panel-2);border:1px solid var(--line);font-size:14.5px;line-height:1.6}
.evidence .n{color:var(--muted);font-size:12px;padding-top:3px} .evidence .fact{display:block;font-size:12.5px;color:var(--muted);margin-top:3px}
.votes{display:flex;gap:6px;flex-wrap:wrap} .vote{font-size:12.5px;padding:4px 10px;border-radius:999px;border:1px solid var(--line);color:var(--ink-2)}
.vote.up{border-color:var(--up);color:var(--up)} .vote.down{border-color:var(--down);color:var(--down)}
.hero-3{grid-template-columns:1fr 320px}
@media (max-width:1000px){.hero-3{grid-template-columns:1fr}}
.board{padding:18px 18px 12px;align-self:start;position:sticky;top:74px;gap:0}
.board .kq{padding:6px 0 12px;border-bottom:1px solid var(--line);margin-bottom:8px}
.board .kq .nm{font-size:12.5px;color:var(--muted);display:flex;justify-content:space-between}
.board .kq .px{font-size:32px;font-weight:700;line-height:1.1;margin-top:4px} .board .kq .ch{font-size:15px;font-weight:600;margin-top:2px}
.board .kq.up .px,.board .kq.up .ch{color:var(--up)} .board .kq.down .px,.board .kq.down .ch{color:var(--down)}
.board .grp{font-size:11px;color:var(--muted);letter-spacing:.06em;text-transform:uppercase;margin:10px 0 4px}
.board table{font-size:14px} .board td{padding:7px 0;white-space:normal} .board td.chg{font-weight:600;width:74px}
.board .foot{font-size:11.5px;color:var(--muted);margin-top:10px}
.btn{font:inherit;font-size:13px;cursor:pointer;border:1px solid var(--line);background:transparent;color:var(--ink);padding:6px 10px;border-radius:8px}
footer{color:var(--muted);font-size:12px;display:grid;gap:4px}
"""

_JS = r"""
(function(){
  var D = JSON.parse(document.getElementById('ta-data').textContent);
  function esc(s){return String(s==null?'':s).replace(/[&<>"']/g,function(c){return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c];});}
  function dir(d){return d==='up'?'<span class="up">▲ 상승</span>':d==='down'?'<span class="down">▼ 하락</span>':'—';}
  function frac(a,b){return b?(a+'/'+b+' ('+Math.round(a/b*100)+'%)'):'—';}
  function $(id){return document.getElementById(id);}
  var p=D.prediction||{}, st=D.stats||{};
  var AL={TechnicalAnalyst:'기술적',MacroFlowAnalyst:'거시·수급',SemiconductorAnalyst:'반도체',NewsSentimentAnalyst:'뉴스·심리',MarketAnalyst:'통합(다수결)'};

  $('asof').textContent = D.analysis_at ? D.analysis_at.replace('T',' ') : '없음';
  $('gen').textContent = D.generated_at.replace('T',' ');
  $('tgt').textContent = D.target_date || '-';

  var k=[];
  k.push(['오늘 KOSPI 예측', p.direction?(p.outlook_label?'<span class="'+(p.direction==='up'?'up':'down')+'">'+esc(p.outlook_label)+'</span>':dir(p.direction)):'—',
    p.up_probability!=null?('상승 확률 '+Math.round(p.up_probability)+'%'):(p.confidence?('신뢰도 '+esc(p.confidence)):'분석 기록 없음')]);
  k.push(['누적 적중률', st.total?(Math.round(st.accuracy*100)+'%'):'—', (st.correct||0)+'/'+(st.total||0)+'건 검증']);
  var q=D.qa||{};
  k.push(['Bull 근거 검증', q.bull&&q.bull[1]?Math.round(q.bull[0]/q.bull[1]*100)+'%':'—', q.bull?frac(q.bull[0],q.bull[1]):'']);
  k.push(['Bear 근거 검증', q.bear&&q.bear[1]?Math.round(q.bear[0]/q.bear[1]*100)+'%':'—', q.bear?frac(q.bear[0],q.bear[1]):'']);
  $('kpis').innerHTML = k.map(function(t){return '<div class="kpi"><div class="l">'+t[0]+'</div><div class="v">'+t[1]+'</div><div class="d">'+t[2]+'</div></div>';}).join('');

  function pct(n){return (n>=0?'+':'')+Number(n).toFixed(2)+'%';}
  var inp0=D.inputs||{};
  function fv(v){return v==null?'—':Number(v).toLocaleString(undefined,{maximumFractionDigits:2});}
  function cc(c){return typeof c==='number'?(c>0?'up':c<0?'down':'flat'):'flat';}
  function brow(x){return '<tr><td>'+esc(x.name)+'</td><td class="num">'+fv(x.value)+'</td><td class="num chg '+cc(x.change_pct)+'">'+(typeof x.change_pct==='number'?pct(x.change_pct):'—')+'</td></tr>';}
  var KH0=D.kospi_history, kq='';
  if(KH0&&KH0.closes&&KH0.closes.length>1){var kl=KH0.closes[KH0.closes.length-1],kp=KH0.closes[KH0.closes.length-2],kc=(kl/kp-1)*100;
    kq='<div class="kq '+cc(kc)+'"><div class="nm"><span>KOSPI '+esc(KH0.dates[KH0.dates.length-1])+' 종가</span><span>전일 '+fv(kp)+'</span></div><div class="px mono">'+fv(kl)+'</div><div class="ch mono">'+pct(kc)+' ('+(kl-kp).toFixed(2)+')</div></div>';}
  $('board').innerHTML='<h2>시장 현황 <span class="h2-sub">'+(inp0.as_of?'분석 당시 · '+esc(inp0.as_of):'')+'</span></h2>'+kq+
    '<div class="grp">참고 미국 지수</div><table>'+(inp0.us||[]).map(brow).join('')+'</table>'+
    ((inp0.macro||[]).length?'<div class="grp">거시 지표</div><table>'+(inp0.macro||[]).map(brow).join('')+'</table>':'')+
    (inp0.kr_flow&&inp0.kr_flow.foreign_net_krw_bn!=null?'<div class="grp">외국인 수급'+(inp0.kr_flow.date?' · '+esc(inp0.kr_flow.date):'')+'</div><table><tr><td>KOSPI 외국인 순매수</td><td></td><td class="num chg '+(inp0.kr_flow.direction==='buy'?'up':'down')+'">'+Number(inp0.kr_flow.foreign_net_krw_bn).toLocaleString()+'억</td></tr></table>':'')+
    '<div class="foot">SOX는 KOSPI와 방향 일치율이 가장 높은 지수입니다.</div>';

  var Q=D.quant||{}, QL=Q.labels||{};
  var facts=[];
  (inp0.us||[]).forEach(function(x){facts.push({k:[x.name,x.name==='SOX'?'반도체 지수':x.name==='VIX'?'공포':x.name==='S&P500'?'S&P':x.name],t:x.name+' '+pct(x.change_pct)});});
  (inp0.macro||[]).forEach(function(x){facts.push({k:[x.name,x.name.indexOf('달러')>=0?'환율':x.name.indexOf('금리')>=0?'금리':x.name.indexOf('WTI')>=0?'유가':x.name],t:x.name+' '+pct(x.change_pct)});});
  if(inp0.kr_flow&&inp0.kr_flow.foreign_net_krw_bn!=null)facts.push({k:['외국인'],t:'외국인 순매수 '+Number(inp0.kr_flow.foreign_net_krw_bn).toLocaleString()+'억'});
  if(Q.rsi14!=null)facts.push({k:['RSI','과매수','과매도'],t:'RSI '+Q.rsi14});
  if(Q.ma)facts.push({k:['이평','이동평균','정배열','역배열','20일선','60일선'],t:'20/60일선 '+Q.ma.ma20+'/'+Q.ma.ma60});
  if(Q.realized_vol_20!=null)facts.push({k:['변동성'],t:'실현변동성 '+Q.realized_vol_20+'%'});
  function mf(text){var o=[];facts.forEach(function(f){if(f.k.some(function(k){return k&&text.indexOf(k)>=0;})&&o.indexOf(f.t)<0)o.push(f.t);});return o.slice(0,3);}
  var AN=D.analysts||{}, votes=[];
  Object.keys(AN).forEach(function(n){var d=AN[n].direction;votes.push('<span class="vote '+(d==='up'?'up':d==='down'?'down':'')+'">'+(AL[n]||esc(n))+' '+(d==='up'?'▲':d==='down'?'▼':'—')+'</span>');});
  var RS=D.risk||{}; if(RS.risk_level)votes.push('<span class="vote">리스크 '+esc(RS.risk_level)+'</span>');
  var prob=p.up_probability!=null?Math.round(p.up_probability):null;
  $('pred').innerHTML = p.direction ?
    '<div class="why-dir '+(p.direction==='up'?'up':'down')+'">'+(p.outlook_label?esc(p.outlook_label):dir(p.direction))+'</div>'+
    '<div>'+(prob!=null?'<span class="chip">상승 확률 <b class="mono">'+prob+'%</b></span> ':'')+'<span class="chip">신뢰도 '+esc(p.confidence||'-')+'</span> <span class="chip">대상 거래일 '+esc(D.target_date||'-')+'</span></div>'+
    (prob!=null?'<div><div class="gauge"><div class="needle" style="left:'+prob+'%"></div></div><div class="gauge-lbl mono"><span>하락 확신</span><span>50</span><span>상승 확신</span></div></div>':'')+
    (p.position_reason?'<div class="ev"><b>포지션</b> '+esc(p.position_reason)+(D.realized_vol!=null?' · 실현변동성 '+D.realized_vol+'%':'')+'</div>':'')+
    (p.calibration_note?'<div class="ev">확률 보정: '+esc(p.calibration_note)+'</div>':'')+
    '<div class="label">왜 이렇게 판단했나</div><ol class="evidence">'+(p.reasoning||[]).map(function(r,i){var f=mf(r);return '<li><span class="n mono">'+String(i+1).padStart(2,'0')+'</span><span>'+esc(r)+(f.length?'<span class="fact mono">데이터: '+f.map(esc).join(' · ')+'</span>':'')+'</span></li>';}).join('')+'</ol>'+
    (votes.length?'<div class="label">에이전트 판단</div><div class="votes">'+votes.join('')+'</div>':'')+
    ((D.lessons_used||[]).length?'<div class="ev"><b>복기 교훈 참고</b> '+D.lessons_used.map(function(r){return esc(r.target_date)+': '+esc(r.lesson||'');}).join(' · ')+'</div>':'')+
    '<div class="ev"><a href="#process" style="color:var(--accent)">분석 과정 전체 보기 →</a></div>'
    : '<div class="empty">아직 분석 결과가 없습니다.</div>';

  var KH=D.kospi_history;
  if(KH&&KH.closes&&KH.closes.length>5){
    var cl=KH.closes,dt=KH.dates,n=cl.length,W=720,H=260,pl=8,pr=62,pt=12,pb=26;
    function sma(a,k){return a.map(function(_,i){return i+1<k?null:a.slice(i+1-k,i+1).reduce(function(x,y){return x+y;},0)/k;});}
    var m20=sma(cl,20),m60=sma(cl,60),lo=Math.min.apply(null,cl)*0.995,hi=Math.max.apply(null,cl)*1.005;
    function X(i){return pl+(i/(n-1))*(W-pl-pr);} function Y(v){return pt+(1-(v-lo)/(hi-lo))*(H-pt-pb);}
    function path(a){return a.map(function(v,i){return v==null?null:((i===0||a[i-1]==null)?'M':'L')+X(i).toFixed(1)+','+Y(v).toFixed(1);}).filter(Boolean).join(' ');}
    var g='';for(var k=0;k<=4;k++){var v=lo+(hi-lo)*k/4;g+='<line class="g" x1="'+pl+'" x2="'+(W-pr)+'" y1="'+Y(v).toFixed(1)+'" y2="'+Y(v).toFixed(1)+'"/><text class="ax" x="'+(W-pr+6)+'" y="'+(Y(v)+4).toFixed(1)+'">'+Math.round(v).toLocaleString()+'</text>';}
    var xl=[0,Math.floor(n/2),n-1].map(function(i){return '<text class="ax" x="'+X(i).toFixed(1)+'" y="'+(H-8)+'" text-anchor="'+(i===0?'start':i===n-1?'end':'middle')+'">'+esc((dt[i]||'').slice(5))+'</text>';}).join('');
    var last=cl[n-1],prev=cl[n-2]||last,chg=(last/prev-1)*100,pr2='';
    if(p.direction){var up=p.direction==='up',x=X(n-1)+22,y=Y(last)+(up?-22:22),c=up?'var(--up)':'var(--down)';
      pr2='<line x1="'+X(n-1).toFixed(1)+'" y1="'+Y(last).toFixed(1)+'" x2="'+x+'" y2="'+y+'" stroke="'+c+'" stroke-width="2" stroke-dasharray="3 3"/><circle cx="'+x+'" cy="'+y+'" r="5" fill="'+c+'"/><text class="ax" x="'+(x+8)+'" y="'+(y+4)+'" style="fill:'+c+'">'+(up?'▲':'▼')+(prob!=null?' '+prob+'%':'')+'</text>';}
    $('kChart').innerHTML='<svg class="kchart" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="KOSPI 최근 '+n+'거래일"><defs><linearGradient id="kg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--accent)" stop-opacity="0.22"/><stop offset="100%" stop-color="var(--accent)" stop-opacity="0"/></linearGradient></defs>'+g+
      '<path fill="url(#kg)" d="'+path(cl)+' L'+X(n-1).toFixed(1)+','+(H-pb)+' L'+X(0).toFixed(1)+','+(H-pb)+' Z"/><path class="ma60" d="'+path(m60)+'"/><path class="ma20" d="'+path(m20)+'"/><path class="price" d="'+path(cl)+'"/>'+pr2+xl+'</svg>';
    $('kPx').textContent=last.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
    $('kChg').textContent=pct(chg)+' ('+(last-prev).toFixed(2)+')';$('kChg').className='mono chg '+(chg>=0?'up':'down');
    $('kDate').textContent=dt[n-1]+' 종가 · 최근 '+n+'거래일';
  }
  var IN=[];
  if(Q.rsi14!=null)IN.push(['RSI(14)',Q.rsi14,QL.rsi]); if(Q.macd)IN.push(['MACD 히스토그램',Q.macd.hist,QL.macd]); if(Q.bb_pct_b!=null)IN.push(['볼린저 %B',Q.bb_pct_b,QL.bb]);
  if(Q.ma)IN.push(['이평 배열',Q.ma.above_ma20?'20일선 위':'20일선 아래',Q.ma.state]); if(Q.realized_vol_20!=null)IN.push(['실현변동성(연율)',Q.realized_vol_20+'%',QL.vol]);
  if(Q.mom_12_1!=null)IN.push(['12-1 모멘텀',pct(Q.mom_12_1),QL.momentum]); if(Q.flow_z5!=null)IN.push(['외국인 5일 z',Q.flow_z5,QL.flow]);
  $('inds').innerHTML=IN.map(function(t){return '<div class="ind"><div class="k">'+esc(t[0])+'</div><div class="v mono">'+esc(String(t[1]))+'</div>'+(t[2]?'<div class="t">'+esc(String(t[2]))+'</div>':'')+'</div>';}).join('');

  var RK=D.stock_rank||{};
  function sigt(g){return g==='up'?'<span class="up">▲ 상승</span>':g==='down'?'<span class="down">▼ 하락</span>':'<span class="ev">— 보합</span>';}
  function card(s){var sig=s.directional_signal;
    return '<div class="stock"'+(s.moderated?' style="border-style:dashed"':'')+'><div class="n"><span>'+esc(s.name)+(RK[s.name]?' <span class="ev">시총 '+RK[s.name]+'위</span>':'')+'</span>'+sigt(sig)+'</div>'+
      '<div class="m">'+esc(s.related_us_indicator||s.sector||'')+' · 민감도 '+esc(s.sensitivity||'-')+'</div>'+
      (s.moderated?'<div class="chip" style="color:var(--warn);border-color:var(--warn)">중재자 조정 · 원래 '+(s.original_signal==='up'?'상승':'하락')+'</div>':'')+
      '<div class="r">'+esc(s.moderated&&s.moderation_reason?s.moderation_reason:(s.reason||''))+'</div></div>';}
  $('core').innerHTML=(D.core_stocks||[]).map(card).join('')||'<div class="empty">없음</div>';
  $('watch').innerHTML=((D.watch_stocks||[]).map(card).join('')||'<div class="empty">없음</div>')+
    (D.stock_moderation?'<div class="ev" style="grid-column:1/-1">관심 종목은 KOSPI 시총 100 중 무작위 추첨. 종목 중재자가 '+D.stock_moderation.reviewed+'종목을 검토해 '+D.stock_moderation.adjusted_to_flat+'종목을 보합으로 조정했습니다.</div>':'');

  var inp=D.inputs||{};
  function irow(x){return '<tr><td>'+esc(x.name)+'</td><td class="num">'+(x.value!=null?esc(x.value):'—')+'</td><td class="num '+(x.change_pct>=0?'up':'down')+'">'+(x.change_pct>=0?'+':'')+Number(x.change_pct).toFixed(2)+'%</td></tr>';}
  var fl=inp.kr_flow;
  $('inputs').innerHTML=((inp.us||[]).length||(inp.macro||[]).length)?'<div class="tbl"><table><thead><tr><th>미국 지수'+(inp.as_of?' ('+esc(inp.as_of)+')':'')+'</th><th class="num">종가</th><th class="num">등락</th></tr></thead><tbody>'+(inp.us||[]).map(irow).join('')+'</tbody>'+
    ((inp.macro||[]).length?'<thead><tr><th>거시 지표</th><th class="num">값</th><th class="num">등락</th></tr></thead><tbody>'+inp.macro.map(irow).join('')+'</tbody>':'')+'</table></div>'+
    '<div class="ev">'+(fl?('외국인 '+(fl.direction==='buy'?'순매수':'순매도')+' '+Math.abs(Number(fl.foreign_net_krw_bn)).toLocaleString()+'억원 ('+esc(fl.date)+')'):'외국인 수급: 데이터 없음')+'</div>'
    :'<div class="empty">이 스냅샷에는 입력 지표가 포함되지 않았습니다.</div>';

  var rg=q.regime||{};
  $('qa').innerHTML='<div class="tbl"><table><tbody>'+
    '<tr><td>장세 (규칙엔진)</td><td class="num">'+(rg.regime_trend?esc('추세 '+rg.regime_trend+' · 변동성 '+rg.regime_volatility):'—')+'</td></tr>'+
    '<tr><td>검증 ① 리스크 단계</td><td class="num"><span class="chip '+(q.risk_stage_passed===false?'bad':'ok')+'">'+(q.risk_stage_passed===false?'재판단':'통과')+'</span> 리스크 '+esc(q.final_risk_level||'-')+'</td></tr>'+
    '<tr><td>검증 ② 최종 단계</td><td class="num"><span class="chip '+(q.final_consistent===false?'bad':'ok')+'">'+(q.final_consistent===false?'불일치':'정합')+'</span></td></tr>'+
    '</tbody></table></div>';

  var an=D.analysts||{};
  $('analysts').innerHTML=Object.keys(an).length?('<div class="tbl"><table><thead><tr><th>분석가</th><th class="num">판단</th><th class="num">신뢰도</th></tr></thead><tbody>'+
    Object.keys(an).map(function(n){var r=an[n];return '<tr><td>'+(AL[n]||esc(n))+'</td><td class="num">'+(r.direction?dir(r.direction):'<span class="ev">보류</span>')+'</td><td class="num">'+esc(r.confidence||'-')+'</td></tr>';}).join('')+'</tbody></table></div>'+
    Object.keys(an).map(function(n){var r=an[n];return r.summary?'<div class="ev"><b>'+(AL[n]||esc(n))+'</b> · '+esc(r.summary)+'</div>':'';}).join('')+
    ((D.news_universe||[]).length?'<div class="ev">뉴스 '+(D.news_count||0)+'건 · 대상 '+D.news_universe.length+'종목: '+D.news_universe.map(esc).join(', ')+'</div>':''))
    :'<div class="empty">이 스냅샷에는 분석가별 판단이 없습니다.</div>';

  var md=D.moderation||{};
  var qm={resolved:'한쪽 우세',contested:'팽팽함',unresolved:'미해결 → 중립 권고'};
  $('mod').innerHTML=md.debate_quality?('<div><span class="chip">토론 품질 '+esc(qm[md.debate_quality]||md.debate_quality)+'</span> <span class="chip">근거상 우세 '+esc(md.stronger_side==='none'?'없음':md.stronger_side||'-')+'</span></div>'+
    (md.summary?'<div class="r">'+esc(md.summary)+'</div>':'')+
    ((md.agreed_facts||[]).length?'<div class="label">합의된 사실</div><ul>'+md.agreed_facts.map(function(x){return '<li>'+esc(x)+'</li>';}).join('')+'</ul>':'')+
    ((md.key_disputes||[]).length?'<div class="label">쟁점</div><ul>'+md.key_disputes.map(function(d){return '<li><b>'+esc(d.topic||'')+'</b> — Bull: '+esc(d.bull||'')+' / Bear: '+esc(d.bear||'')+'</li>';}).join('')+'</ul>':''))
    :'<div class="empty">이 스냅샷에는 중재자 정리가 없습니다.</div>';

  var r=D.risk||{};
  $('risk').innerHTML=(r.risk_level?'<div><span class="chip">리스크 '+esc(r.risk_level)+'</span> <span class="chip">권장 신뢰도 '+esc(r.recommended_confidence||'-')+'</span></div>':'')+
    (r.summary?'<div class="r">'+esc(r.summary)+'</div>':'<div class="empty">요약 없음</div>');

  var deb=D.debate||[];
  $('debate').innerHTML=deb.length?deb.map(function(t){
    return '<div class="turn"><div class="tag '+(t.stance==='bull'?'bull':'bear')+'">'+(t.stance==='bull'?'BULL':'BEAR')+'</div><div>'+esc(t.argument)+
      ((t.evidence||[]).length?'<div class="ev">근거: '+t.evidence.map(esc).join(' · ')+'</div>':'')+'</div></div>';}).join('')
    :'<div class="empty">이 스냅샷에는 토론 기록이 포함되지 않았습니다.</div>';

  var bc=D.baseline_compare||null, bt=D.baselines_today||{};
  function acc(r){return r&&r.total?frac(r.correct,r.total):'—';}
  var brows=[['AI 멀티에이전트',p.direction,bc&&bc.ai,true]];
  Object.keys((bc&&bc.baselines)||bt).forEach(function(k){brows.push([(bc&&bc.baselines[k]&&bc.baselines[k].label)||(bt[k]&&bt[k].label)||k,bt[k]&&bt[k].direction,bc&&bc.baselines[k],false]);});
  $('base').innerHTML='<div class="tbl"><table><thead><tr><th>방식</th><th class="num">오늘</th><th class="num">누적 적중</th></tr></thead><tbody>'+
    brows.map(function(b){return '<tr'+(b[3]?' style="font-weight:600"':'')+'><td>'+esc(b[0])+'</td><td class="num">'+(b[1]==='up'?'<span class="up">▲</span>':b[1]==='down'?'<span class="down">▼</span>':'—')+'</td><td class="num">'+acc(b[2])+'</td></tr>';}).join('')+
    '</tbody></table></div><div class="ev">AI가 단순 규칙보다 꾸준히 높아야 의미가 있습니다. 같은 날짜 표본으로 비교합니다.</div>';

  var rb=D.returns||null;
  function curve(series,w,h){w=w||520;h=h||110;var all=[];series.forEach(function(x){all=all.concat(x.v);});if(!all.length)return '';
    var lo=Math.min.apply(null,[0].concat(all)),hi=Math.max.apply(null,[0].concat(all)),pad=6,span=(hi-lo)||1,n=Math.max.apply(null,series.map(function(x){return x.v.length;}));
    function X(i){return pad+(n>1?i/(n-1):0)*(w-2*pad);}function Y(v){return h-pad-((v-lo)/span)*(h-2*pad);}
    return '<svg viewBox="0 0 '+w+' '+h+'" width="100%" height="'+h+'"><line x1="0" y1="'+Y(0)+'" x2="'+w+'" y2="'+Y(0)+'" stroke="var(--line)"/>'+
      series.map(function(x){return '<polyline fill="none" stroke="'+x.c+'" stroke-width="2" points="'+x.v.map(function(v,i){return X(i).toFixed(1)+','+Y(v).toFixed(1);}).join(' ')+'"/>';}).join('')+
      series.map(function(x,k){return '<text x="'+pad+'" y="'+(14+k*14)+'" font-size="11" fill="'+x.c+'">'+esc(x.n)+' '+x.v[x.v.length-1].toFixed(2)+'%</text>';}).join('')+'</svg>';}
  function prow(l,q,b){function f(v){return typeof v==='number'?v.toFixed(2):'—';}
    return '<tr'+(b?' style="font-weight:600"':'')+'><td>'+esc(l)+'</td><td class="num '+(q.total_return_pct>=0?'up':'down')+'">'+f(q.total_return_pct)+'%</td><td class="num">'+(q.sharpe_annual==null?'—':f(q.sharpe_annual))+'</td><td class="num">'+f(q.max_drawdown_pct)+'%</td><td class="num">'+(q.win_rate==null?'—':Math.round(q.win_rate*100)+'%')+'</td><td class="num">'+(q.traded_days==null?'—':q.traded_days)+'/'+q.days+'</td></tr>';}
  $('ret').innerHTML = rb ? curve([{n:'AI 포지션',v:rb.ai.equity_curve_pct||[],c:'var(--accent)'},{n:'바이앤홀드',v:rb.buy_hold.equity_curve_pct||[],c:'var(--muted)'}])+
    '<div class="tbl"><table><thead><tr><th>방식</th><th class="num">누적</th><th class="num">샤프</th><th class="num">MDD</th><th class="num">승률</th><th class="num">거래일</th></tr></thead><tbody>'+
    prow('AI 포지션',rb.ai,true)+prow('KOSPI 바이앤홀드',rb.buy_hold,false)+Object.keys(rb.baselines||{}).map(function(k){return prow(rb.baselines[k].label+' (100%)',rb.baselines[k],false);}).join('')+
    Object.keys(rb.by_agent||{}).map(function(k){return prow((AL[k]||k)+' (방향 100%)',rb.by_agent[k],false);}).join('')+'</tbody></table></div>'+
    '<div class="ev">'+(rb.ai_beats_buy_hold&&rb.ai_beats_best_baseline?'AI 포지션이 바이앤홀드·최고 단순규칙보다 수익이 높습니다.':'아직 바이앤홀드 또는 단순규칙이 AI보다 수익이 높습니다.')+
    ' 포지션 = (2p−1)×½켈리×목표변동성15%/실현변동성, |2p−1|<0.1이면 현금. 왕복 비용 0.1% 차감. 표본 '+rb.days+'일.</div>'
    : '<div class="empty">검증된 기록이 쌓이면 수익률(롱=KODEX200 · 쇼트=KODEX인버스 · 현금)이 표시됩니다.</div>';

  var PA=D.paper||null;
  function won(v){return Number(v).toLocaleString(undefined,{maximumFractionDigits:0})+'원';}
  $('paper').innerHTML = PA ? '<div class="kpis" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">'+
    [['평가금액',won(PA.equity),pct(PA.total_return_pct)+' ('+(PA.pnl>=0?'+':'')+won(PA.pnl)+')',PA.total_return_pct>=0?'up':'down'],['시작 자금',won(PA.initial_cash),PA.days+'일 운용',''],['샤프 / MDD',(PA.sharpe_annual==null?'—':PA.sharpe_annual)+' / '+PA.max_drawdown_pct+'%','승률 '+(PA.win_rate==null?'—':Math.round(PA.win_rate*100)+'%'),''],['거래',PA.trades+'건','비용 '+won(PA.fees),'']]
    .map(function(t){return '<div class="kpi"><div class="l">'+t[0]+'</div><div class="v mono '+t[3]+'" style="font-size:22px">'+t[1]+'</div><div class="d">'+t[2]+'</div></div>';}).join('')+'</div>'+
    curve([{n:'가상 계좌',v:(PA.equity_curve||[]).map(function(x){return (x.equity/PA.initial_cash-1)*100;}),c:'var(--accent)'}])+
    '<div class="tbl"><table><thead><tr><th>거래일</th><th class="num">목표</th><th class="num">평가금액</th><th class="num">일수익</th><th class="num">누적</th></tr></thead><tbody>'+
    (PA.recent_days||[]).slice(0,20).map(function(d){return '<tr><td>'+esc(d.date)+'</td><td class="num">'+(d.target_position>0?'롱':d.target_position<0?'쇼트':'현금')+' '+Math.round(Math.abs(d.target_position)*100)+'%</td><td class="num">'+won(d.equity)+'</td><td class="num '+(d.day_return_pct>=0?'up':'down')+'">'+pct(d.day_return_pct)+'</td><td class="num">'+pct(d.total_return_pct)+'</td></tr>';}).join('')+'</tbody></table></div>'+
    '<div class="ev">현금 '+won(PA.cash||0)+' · 보유 '+(Object.keys(PA.holdings||{}).length?Object.keys(PA.holdings).map(function(t){return (t.indexOf('069500')===0?'KODEX 200':'KODEX 인버스')+' '+PA.holdings[t]+'주';}).join(', '):'없음')+'</div>'+
    ((PA.recent_trades||[]).length?'<div class="label">최근 체결</div><div class="tbl"><table><thead><tr><th>일자</th><th>종목</th><th class="num">매매</th><th class="num">수량</th><th class="num">체결가</th><th class="num">수수료</th></tr></thead><tbody>'+
      PA.recent_trades.map(function(t){return '<tr><td>'+esc(t.date)+'</td><td>'+(t.ticker.indexOf('069500')===0?'KODEX 200':'KODEX 인버스')+'</td><td class="num '+(t.side==='buy'?'up':'down')+'">'+(t.side==='buy'?'매수':'매도')+'</td><td class="num">'+t.qty+'</td><td class="num">'+Number(t.price).toLocaleString()+'</td><td class="num">'+won(t.fee)+'</td></tr>';}).join('')+'</tbody></table></div>':'')+
    '<div class="ev">가상 체결입니다. 아침 분석의 포지션 비중을 그날 ETF 시가에 맞추고 종가로 평가하며, 다음 날 다시 조정합니다. 실제 돈은 들어가지 않습니다.</div>'
    : '<div class="empty">아직 체결 기록이 없습니다. 마감 채점(15:45)부터 그날 포지션대로 ETF를 가상 체결하고 여기에 평가금액·손익·체결 내역이 쌓입니다. 시작 자금 1,000만 원.</div>';

  var ag=st.by_agent||{}, order=['TechnicalAnalyst','MacroFlowAnalyst','SemiconductorAnalyst','NewsSentimentAnalyst','MarketAnalyst','Bull','Bear','RiskManager','Trader'];
  var names=order.filter(function(n){return ag[n];});
  $('agents').innerHTML=names.length?'<div class="tbl"><table><thead><tr><th>에이전트</th><th class="num">방향 적중</th><th class="num">근거 검증률</th></tr></thead><tbody>'+
    names.map(function(n){var a=ag[n];return '<tr><td>'+(AL[n]||esc(n))+'</td><td class="num">'+(a.total?frac(a.correct,a.total):'—')+'</td><td class="num">'+(a.evidence_total?frac(a.evidence_verified,a.evidence_total):'—')+'</td></tr>';}).join('')+
    '</tbody></table></div>':'<div class="empty">검증된 기록이 쌓이면 표시됩니다.</div>';

  var cal=st.calibration;
  if(cal&&cal.n){
    $('agents').innerHTML+='<div class="label" style="margin-top:8px">확신도 보정 (Brier '+cal.brier+', 낮을수록 좋음)</div><div class="tbl"><table><thead><tr><th>말한 확률</th><th class="num">건수</th><th class="num">실제 상승률</th></tr></thead><tbody>'+
      cal.bins.map(function(b){return '<tr><td>'+b.range+'</td><td class="num">'+b.n+'</td><td class="num">'+(b.actual_up_rate!=null?b.actual_up_rate+'%':'—')+'</td></tr>';}).join('')+'</tbody></table></div>';
  }
  var recs=D.records||[];
  $('records').innerHTML=recs.length?'<div class="tbl"><table><thead><tr><th>대상 거래일</th><th>예측</th><th>신뢰도</th><th>실제</th><th>결과</th></tr></thead><tbody>'+
    recs.map(function(x){return '<tr><td class="mono">'+esc(x.actual_session_date||x.target_date)+'</td><td>'+dir(x.direction)+(x.up_probability!=null?' <span class="ev">'+Math.round(x.up_probability)+'%</span>':'')+'</td><td>'+esc(x.confidence||'-')+'</td><td>'+(x.verified?dir(x.actual_direction):'<span class="empty">대기</span>')+'</td><td>'+(x.verified?(x.correct?'<span class="chip ok">적중</span>':'<span class="chip bad">빗나감</span>'):'—')+'</td></tr>';}).join('')+
    '</tbody></table></div>':'<div class="empty">기록 없음</div>';
})();
"""

_BODY = """<title>KOSPI 멀티에이전트 리포트</title>
<style>{css}</style>
<header class="topbar">
  <div class="topbar-inner">
    <div class="brand"><span class="logo">TA</span><span><b>KOSPI 멀티에이전트 리포트</b><small>대상 거래일 <span class="mono" id="tgt"></span> · 분석 <span class="mono" id="asof"></span></small></span></div>
    <nav class="tabs" id="tabs">
      <a href="#today" data-tab="today" class="active">오늘의 예측</a>
      <a href="#stocks" data-tab="stocks">관심 종목</a>
      <a href="#process" data-tab="process">분석 과정</a>
      <a href="#performance" data-tab="performance">성과·수익률</a>
      <a href="#paper" data-tab="paper">모의투자 수익률</a>
      <a href="#records" data-tab="records">예측 기록</a>
    </nav>
    <span class="ro"><b>읽기 전용 · 모의투자</b> 실제 자금 거래 없음</span>
    <button class="btn" id="btnTheme">☀ 밝게</button>
  </div>
</header>
<div class="wrap">
  <div class="panel active" id="tab-today">
    <div class="hero hero-3">
      <div style="display:grid;gap:20px;min-width:0">
      <section class="hero-card" style="gap:10px"><div class="chart-head"><span class="lbl">KOSPI</span><span class="px mono" id="kPx">—</span><span class="mono" id="kChg"></span><span class="lbl" id="kDate"></span></div>
        <div id="kChart"><div class="empty">이 스냅샷에는 KOSPI 시세가 없습니다 (다음 분석부터 포함).</div></div>
        <div class="legend"><span><i style="background:var(--ink)"></i>종가</span><span><i style="background:var(--warn)"></i>20일선</span><span><i style="background:var(--accent)"></i>60일선</span></div>
        <div class="inds" id="inds"></div></section>
      <section class="hero-card" style="gap:12px"><h2>오늘의 KOSPI 예측</h2><div id="pred" style="display:grid;gap:12px"></div></section>
      </div>
      <section class="board" id="board"></section>
    </div>
    <div class="kpis" id="kpis" style="grid-template-columns:repeat(4,1fr)"></div>
    <section><h2>검증 · QA</h2><div id="qa"></div></section>
  </div>
  <div class="panel" id="tab-stocks">
    <section><h2>핵심 종목 <span class="h2-sub">삼성전자 · SK하이닉스</span></h2><div class="stocks" id="core"></div>
      <h2 style="margin-top:20px">관심 종목 <span class="h2-sub">KOSPI 시총 100에서 오늘 추첨한 10개</span></h2><div class="stocks" id="watch"></div></section>
  </div>
  <div class="panel" id="tab-process">
    <div class="grid">
      <div style="display:grid;gap:20px;min-width:0">
        <section><h2>분석가 4명 판단</h2><div id="analysts"></div></section>
        <section><h2>Bull / Bear 토론</h2><div id="debate"></div></section>
      </div>
      <div style="display:grid;gap:20px;min-width:0">
        <section><h2>중재자 정리</h2><div id="mod"></div></section>
        <section><h2>리스크 매니저 판단</h2><div id="risk"></div></section>
        <section><h2>입력 지표</h2><div id="inputs"></div></section>
      </div>
    </div>
  </div>
  <div class="panel" id="tab-performance">
    <section><h2>수익률 <span class="h2-sub">포지션 기준 · 비용 차감</span></h2><div id="ret"></div></section>
    <div class="grid">
      <section><h2>AI vs 단순 규칙</h2><div id="base"></div></section>
      <section><h2>에이전트별 성과</h2><div id="agents"></div></section>
    </div>
  </div>
  <div class="panel" id="tab-paper">
    <section><h2>모의투자 수익률 <span class="h2-sub">가상계좌 · 실제 자금 거래 없음 · 매일 시가 리밸런싱 → 종가 평가 · 수수료 0.015% + 슬리피지 0.05%</span></h2><div id="paper" style="display:grid;gap:14px"></div></section>
  </div>
  <div class="panel" id="tab-records">
    <section><h2>예측 기록</h2><div id="records"></div></section>
  </div>
  <footer>
    <div>투자 자문이 아닙니다. 모든 내용은 참고용 정보이며, 방향성 신호는 매수·매도 추천이 아닙니다.</div>
    <div>뉴스 원문은 이 공유본에 포함되지 않습니다(건수·AI 요약만). 시장 데이터 출처: Yahoo Finance, Google 뉴스 RSS, 네이버 증권(집계 수치만).</div>
    <div>스냅샷 생성 <span class="mono" id="gen"></span> · TradingAgents-KR</div>
  </footer>
</div>
<script type="application/json" id="ta-data">{data}</script>
<script>{js}</script>
<script>
(function(){
  var tabs=document.querySelectorAll('#tabs a');
  function go(n){var ok=false;tabs.forEach(function(a){if(a.dataset.tab===n)ok=true;});if(!ok)n='today';
    tabs.forEach(function(a){a.classList.toggle('active',a.dataset.tab===n);});
    document.querySelectorAll('.panel').forEach(function(p){p.classList.toggle('active',p.id==='tab-'+n);});}
  go((location.hash||'').replace('#',''));
  var tb=document.getElementById('btnTheme');
  function paint(){tb.textContent=document.documentElement.getAttribute('data-theme')==='light'?'☾ 어둡게':'☀ 밝게';}
  try{var t=localStorage.getItem('ta.theme');if(t)document.documentElement.setAttribute('data-theme',t);}catch(e){}
  paint();
  tb.addEventListener('click',function(){var cur=document.documentElement.getAttribute('data-theme')==='light'?'':'light';
    if(cur)document.documentElement.setAttribute('data-theme',cur);else document.documentElement.removeAttribute('data-theme');
    try{localStorage.setItem('ta.theme',cur);}catch(e){}paint();});
  window.addEventListener('hashchange',function(){go((location.hash||'').replace('#',''));});
})();
</script>
"""


def render_fragment(data: dict) -> str:
    # Only the approved simulated-account fields may enter a shared report.
    data = {**data, "paper": _pick(data.get("paper") or {}, _PAPER_FIELDS) or None}
    # </script> 탈출 방지: JSON 안의 '<'를 유니코드 이스케이프
    data_json = json.dumps(data, ensure_ascii=False).replace("<", "\\u003c")
    out = _BODY.replace("{css}", _CSS).replace("{js}", _JS).replace("{data}", data_json)
    return redact(out)


def export_site(out_dir: Path = SITE_DIR) -> dict:
    # Register configured secrets for exact-value redaction without printing them.
    # This only reads local credentials; it does not call a paid API.
    for provider in KEY_NAMES:
        get_api_key(provider)
    out_dir.mkdir(parents=True, exist_ok=True)
    frag = render_fragment(build_snapshot_data())
    for pat in SECRET_PATTERNS:  # 최종 이중 확인
        if pat.search(frag):
            raise RuntimeError("스냅샷에 키로 보이는 값이 있어 생성을 중단했습니다.")
    full = ('<!doctype html>\n<html lang="ko">\n<head>\n<meta charset="utf-8">\n'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">\n'
            '</head>\n<body>\n' + frag + '\n</body>\n</html>\n')
    (out_dir / "snapshot_fragment.html").write_text(frag, encoding="utf-8")
    (out_dir / "index.html").write_text(full, encoding="utf-8")
    return {"index": str(out_dir / "index.html"), "fragment": str(out_dir / "snapshot_fragment.html")}
