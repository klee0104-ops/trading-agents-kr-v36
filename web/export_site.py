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
                 "moderated", "original_signal", "moderation_reason", "direction_bias", "bias_confidence", "assessment_status", "assessment_note", "bias_reason")


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
    out = {"days": rb["days"], "dates": rb.get("dates", []), "proxy_days": rb.get("proxy_days"), "best_baseline": rb.get("best_baseline"),
           "ai_beats_buy_hold": rb.get("ai_beats_buy_hold"), "ai_beats_best_baseline": rb.get("ai_beats_best_baseline"),
           "ai": _pick(rb.get("ai") or {}, _PERF_KEYS), "buy_hold": _pick(rb.get("buy_hold") or {}, _PERF_KEYS),
           "baselines": {k: {"label": v.get("label"), **_pick(v, _PERF_KEYS[:-1])} for k, v in (rb.get("baselines") or {}).items()},
           "by_agent": {k: _pick(v, _PERF_KEYS[:-1]) for k, v in (rb.get("by_agent") or {}).items()}}
    return out


_PAPER_FIELDS = ("execution_alignment", "days", "initial_cash", "equity", "pnl", "total_return_pct", "day_pnl", "day_return_pct", "valuation_audit", "sharpe_annual",
                 "max_drawdown_pct", "win_rate", "trades", "fees", "equity_curve", "recent_days",
                 "cash", "holdings", "recent_trades", "portfolio_mode", "sleeves", "last_tick", "created_at", "realized_pnl", "unrealized_pnl", "gross_exposure", "net_direction_exposure", "hedged")


def _long_public():
    from memory.long_report import read_public
    return read_public(Config.LONG_RESEARCH_PATH)


def _paper_public() -> dict | None:
    try:
        from execution.split_portfolio import read_summary
        split = read_summary(Config.PAPER_PORTFOLIO_PATH)
        if split is not None:
            return split
        from execution.paper import PaperAccount
        sm = PaperAccount(Config.PAPER_ACCOUNT_PATH, Config.PAPER_INITIAL_CASH).summary()
    except Exception:  # noqa: BLE001
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
    from memory.review_history import review_history
    audit = review_history(Config.MEMORY_DB_PATH, Config.LESSONS_PATH)
    records = sorted(memory.all_records(), key=lambda r: r.get("target_date", ""), reverse=True)[:30]

    latest = records[0] if records else {}
    today_base = {k: v.get("direction") for k, v in (o.get("baselines") or {}).items()} \
        or baseline_predictions(latest.get("us_indices", []))
    sn = o.get("snapshot") or {}
    kh = o.get("kospi_history")
    # (V3.22) KOSPI 차트·현재 지수는 페이지를 만드는 시점의 최신 시세로 갱신한다(Yahoo 누락분은 네이버로 보충).
    # 분석 당시 값이 누락된 채 저장돼 있으면(예: 9/28 −2.70% 빠짐) 차트가 옛 날짜에서 끝나 '상승'처럼 보이던 문제.
    try:
        from datetime import date as _d
        from data.market_data import MarketDataProvider
        from data.sessions import kst_now
        h = MarketDataProvider().fetch_kospi_history(kst_now().date(), sessions=80)
        if h.get("dates") and (not kh or h["dates"][-1] >= (kh.get("dates") or [""])[-1]):
            kh = {"dates": h["dates"], "closes": [round(c, 2) for c in h["closes"]], "refreshed": True}
    except Exception as e:  # noqa: BLE001 — 네트워크 없으면 저장된 값 사용
        print(f"[공유 페이지] KOSPI 최신 시세 갱신 실패(저장값 사용): {e}")
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
        "long_research": _long_public(),
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
        "review_history": audit,
        "records": [
            _pick(r, ("target_date", "run_date", "run_at", "direction", "confidence", "verified",
                      "actual_direction", "correct", "actual_session_date", "up_probability", "outlook",
                      "actual_change_pct", "position", "strategy_return_pct"))
            for r in records
        ],
    }
    # 뉴스 원문(제목)은 공유본에 싣지 않는다: 토론 근거/분석가 요약/중재 정리에 문장째 인용된 것은 가린다.
    for key in ("debate", "analysts", "moderation", "prediction"):
        data[key] = _redact_deep(data[key], cores)
    from memory.research_reports import sync_latest
    from data.sessions import kst_now
    from tools.company_research import read_public as company_public
    data['company_research']=company_public(Config.COMPANY_RESEARCH_PATH,Config.LONG_RESEARCH_PATH)
    data['research_reports']=sync_latest(Config.REPORT_ARCHIVE_PATH,Config.LAST_OUTCOME_PATH,Config.LONG_RESEARCH_PATH,kst_now())
    return data


_CSS = """
.report-intro{line-height:1.8;color:var(--muted);margin:0 0 24px}.research-document{border:1px solid var(--line);border-radius:14px;margin:14px 0;overflow:hidden}.research-document summary{display:flex;align-items:center;flex-wrap:wrap;gap:14px;padding:22px;cursor:pointer;list-style:none;background:var(--panel)}.research-document summary:hover{filter:brightness(1.1)}.research-document[open] summary{border-bottom:1px solid var(--line)}.report-team{font-size:12px;letter-spacing:.05em;background:#173d38;color:#9be9cb;padding:6px 10px;border-radius:6px}.report-date{margin-left:auto;color:var(--muted);font-size:13px}.report-paper{max-width:940px;margin:24px auto;padding:48px 56px;background:#fafbf7;color:#243329;border-radius:4px;line-height:1.9;--line:#d5dfd4;--muted:#526257;--ink:#243329;--ink-2:#47594b;--panel:#f0f4ed;--panel-2:#f0f4ed;--bg:#fafbf7;--accent:#236947}.report-paper h2{font-size:30px;line-height:1.4;color:#173c2b}.report-paper h3{font-size:20px;margin-top:32px;color:#173c2b}.report-paper h4{margin:0 0 10px}.report-paper p,.report-paper li{overflow-wrap:anywhere}.report-paper section{padding:4px 0 20px;border-bottom:1px solid #d5dfd4}.report-paper a{color:#216440}.report-paper table{font-size:13px}.report-heading{border-top:5px solid #236947;padding-top:28px;margin-bottom:30px}.report-kicker{font-size:12px;letter-spacing:.14em;color:#52755e}.report-disclaimer{font-size:13px;color:#647266}.report-conclusion{font-size:23px;font-weight:700}.report-role,.report-debate{padding:18px 22px;margin:14px 0;background:#eef3eb;border-left:3px solid #78a687;border-radius:3px}.report-footer{font-size:12px;color:#65766b;margin-top:32px}.watch-grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(210px,1fr));gap:12px;margin:18px 0}.watch-item{border:1px solid var(--line);border-radius:10px;padding:15px}.watch-item small{display:block;line-height:1.7;color:var(--muted)}
@media(max-width:650px){.report-paper{padding:25px 20px;margin:12px 0}.report-paper h2{font-size:24px}.research-document summary{padding:18px}.report-date{width:100%;margin:0}.report-role,.report-debate{padding:14px}.report-paper h3{font-size:18px}}
@media print{body{background:white!important}.topbar,nav,header:not(.report-heading),.tabs,.sidebar,.report-intro,.research-document:not([open]),.research-document>summary{display:none!important}.panel,.tab-panel{display:none!important}#tab-reports{display:block!important}.report-paper{margin:0;padding:12mm;max-width:none;color:black;background:white;box-shadow:none}.research-document{border:0}.report-role,.report-debate{break-inside:avoid}.report-paper h3{break-after:avoid}}

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
.brand{display:flex;align-items:center;gap:10px;color:var(--ink);text-decoration:none}.brand:hover b{color:var(--accent)}.brand:focus-visible{outline:2px solid var(--accent);outline-offset:5px;border-radius:6px}
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
.aitip{margin-top:6px;font-size:12.5px;color:var(--ink);background:var(--panel-2);border-radius:8px;padding:7px 10px}
.aitag{margin-left:auto;font-size:12.5px;color:var(--ink-2)}.aitag .ax2{color:var(--muted);font-size:11px}
.aihead{display:flex;gap:10px;align-items:baseline;flex-wrap:wrap;margin-top:14px;font-size:12.5px;color:var(--ink-2)}.aihead b{color:var(--ink)}
.kchart .mid{stroke:var(--muted);stroke-dasharray:4 4;stroke-width:1}.kchart .ailine{fill:none;stroke:var(--muted);stroke-width:1.5}
.kchart .m-hit{fill:var(--good);stroke:var(--panel);stroke-width:2}.kchart .m-wait{fill:var(--panel);stroke:var(--ink-2);stroke-width:2}
.kchart .m-miss{stroke:var(--warn);stroke-width:2.5;stroke-linecap:round;fill:none}
.legend i.lg-hit{background:var(--good);border-radius:50%;width:9px;height:9px;border:0}
.legend i.lg-wait{border:2px solid var(--ink-2);border-radius:50%;width:9px;height:9px;background:transparent}
.legend i.lg-miss{font-style:normal;color:var(--warn);font-weight:700;background:none;width:auto;height:auto;border:0}
.clk{font-size:12px;color:var(--ink-2);white-space:nowrap;font-variant-numeric:tabular-nums}
.stale{margin:12px 0;padding:10px 14px;border:1px solid var(--warn);border-radius:10px;color:var(--warn);font-size:13px}
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

/* ---- V3.23 모바일 공유 최적화 ---- */
.tabs a i,.tabs a .s{display:none}
.panel.active{grid-template-columns:minmax(0,1fr)}
.hero-3,.grid{grid-template-columns:minmax(0,1fr) 320px}
.grid{grid-template-columns:repeat(2,minmax(0,1fr))}
svg{max-width:100%;height:auto}
section,.hero-card,.board{min-width:0}
section>*,.hero-card>*,#pred>*,#ret>*,#paper>*,.panel>*{min-width:0}
.tbl{max-width:100%;overflow-x:auto;-webkit-overflow-scrolling:touch}
@media (max-width:1000px){.hero-3{grid-template-columns:minmax(0,1fr)}.board{position:static}}
@media (max-width:820px){.grid{grid-template-columns:minmax(0,1fr)}}
@media (max-width:640px){
  body{font-size:15px;padding-bottom:calc(66px + env(safe-area-inset-bottom))}
  .wrap{padding-inline:12px;padding-block:14px 24px;gap:14px}
  .panel.active{gap:14px}
  .topbar{overflow:hidden}.topbar-inner{padding:8px 12px;gap:8px;flex-wrap:nowrap;max-width:100vw}
  html,body{overflow-x:hidden}
  .brand{min-width:0;flex:1;overflow:hidden}.brand b{font-size:15px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .brand small{font-size:11px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
  .brand .logo{width:30px;height:30px;flex:0 0 auto}
  .ro{display:none}.clk{font-size:10.5px}
  .btn{padding:6px 9px;font-size:12px;flex:0 0 auto}
  /* 하단 고정 탭바 (앱처럼) */
  .tabs{position:fixed;left:0;right:0;bottom:0;z-index:30;display:grid;grid-template-columns:repeat(8,minmax(0,1fr));gap:0;
    background:var(--panel);border-top:1px solid var(--line);padding:4px 2px calc(4px + env(safe-area-inset-bottom));order:0;width:auto}
  .tabs a{display:flex;flex-direction:column;align-items:center;gap:1px;padding:6px 0;border-radius:8px;font-size:11px;color:var(--muted)}
  .tabs a i{display:block;font-style:normal;font-size:17px;line-height:1.1}
  .tabs a .l{display:none}.tabs a .s{display:block}
  .tabs a.active{background:transparent;color:var(--accent);font-weight:700}
  section,.hero-card{padding:16px 14px;border-radius:12px;gap:10px}
  h2{font-size:16px}.h2-sub{display:block;margin:4px 0 0;font-size:12px}
  .chart-head{gap:6px 10px}.chart-head .px{font-size:26px}
  .inds{grid-template-columns:repeat(3,minmax(0,1fr));gap:6px}.ind{padding:7px 8px}.ind .v{font-size:13px}
  .kpis{grid-template-columns:repeat(2,minmax(0,1fr))!important;gap:10px}
  .kpi{padding:12px}.kpi .v{font-size:19px!important;word-break:keep-all;letter-spacing:-.02em}.kpi .l{font-size:10.5px}
  .why-dir{font-size:26px}
  .hero-card .chip,.chip{font-size:12.5px;padding:4px 10px;white-space:nowrap}
  .evidence li{grid-template-columns:22px 1fr;padding:10px;font-size:14px}
  .votes{gap:5px}.vote{font-size:12px;padding:3px 9px}
  .stocks{grid-template-columns:minmax(0,1fr);gap:10px}
  .stock .n{font-size:15px}
  .turn{grid-template-columns:44px 1fr;font-size:14px}
  table{font-size:13px} th,td{padding:8px 6px}
  .tbl{overflow:visible;margin:0}
  .tbl table,.tbl tbody,.tbl tr,.tbl td{display:block;width:100%}
  .tbl thead{display:none}
  .tbl tr{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:2px 14px;border:1px solid var(--line);border-radius:10px;padding:9px 11px;margin-bottom:8px;background:var(--panel-2)}
  .tbl td{border:0;padding:3px 0;white-space:normal!important;text-align:left!important;display:flex;justify-content:space-between;align-items:baseline;gap:8px;font-size:13.5px;min-width:0}
  .tbl td[data-label]::before{content:attr(data-label);color:var(--muted);font-size:11.5px;font-weight:400;font-family:"IBM Plex Sans KR",system-ui,sans-serif;white-space:nowrap}
  .tbl td:first-child{grid-column:1/-1;font-weight:700;font-size:14.5px;border-bottom:1px solid var(--line);padding-bottom:7px;margin-bottom:3px}
  .tbl td:first-child::before{content:none!important}
  .tbl td[colspan]{grid-column:1/-1;border:0;background:none}
  .board .kq .px{font-size:28px}.board td{font-size:14px}
  footer{font-size:11.5px;padding-bottom:8px}
  .label{margin-top:4px}
}

/* Compact overview and progressively disclosed stock research. */
.stocks{grid-template-columns:repeat(auto-fill,minmax(240px,1fr));gap:16px}
.stock-tile{font:inherit;text-align:left;color:var(--ink);background:var(--panel);border:1px solid var(--line);border-radius:22px;padding:22px;cursor:pointer;display:grid;gap:9px;min-width:0;transition:transform .15s,border-color .15s}
.stock-tile:hover{transform:translateY(-3px);border-color:var(--accent)}
.stock-tile:focus-visible,.stock-dialog button:focus-visible{outline:3px solid var(--accent);outline-offset:4px}
.stock-top{display:flex;align-items:center;gap:12px}.stock-top strong{display:block;font-size:18px}.stock-top small,.stock-bottom small{display:block;color:var(--muted);font-size:12px;margin-top:4px}
.stock-avatar{width:42px;height:42px;border-radius:15px;display:grid;place-items:center;background:var(--panel-2);color:var(--accent);font-weight:800}.stock-arrow{margin-left:auto;color:var(--muted);font-size:28px}
.stock-price{font-size:30px;font-weight:750;letter-spacing:-1px;font-variant-numeric:tabular-nums}.stock-tile .stock-line{height:74px;width:100%}.stock-bottom{border-top:1px solid var(--line);padding-top:12px;font-size:13px}
.stock-dialog{box-sizing:border-box;width:min(680px,calc(100% - 32px));max-height:90dvh;overflow-y:auto;border:1px solid var(--line);border-radius:26px;background:var(--panel);color:var(--ink);padding:30px;box-shadow:0 24px 100px #0006}.stock-dialog::backdrop{background:#10182899;backdrop-filter:blur(5px)}
.stock-detail-head{display:flex;justify-content:space-between;gap:12px;align-items:center}.stock-detail-head h2{margin:8px 0 22px;font-size:28px}.stock-detail-head small{color:var(--muted)}
.stock-dialog button{font:inherit;cursor:pointer;border:0;border-radius:12px;background:var(--panel-2);color:var(--ink);padding:10px 16px;min-height:44px}.stock-dialog .detail-close{font-size:26px}.stock-period{display:flex;gap:8px;margin:22px 0 10px}.stock-period button[aria-pressed=true]{background:var(--accent);color:white}.stock-dialog .stock-line{width:100%;display:block}.stock-dates{display:flex;justify-content:space-between;gap:12px;font-size:12px;color:var(--muted)}.stock-prices{margin:18px 0}.stock-prices summary{cursor:pointer;padding:12px 0}.stock-dialog .stock{background:var(--panel-2);border:0;padding:18px}.stock-dialog h3{margin-top:28px}
.panel>section{border-radius:22px}.tabs a{border-radius:12px}.kpi{border-radius:18px}
@media(max-width:600px){.stocks{grid-template-columns:1fr}.stock-tile{padding:20px}.stock-dialog{width:100%;max-height:92dvh;margin:auto 0 0;border-radius:24px 24px 0 0;padding:24px 20px}.stock-price{font-size:28px}}
@media(prefers-reduced-motion:reduce){.stock-tile{transition:none}.stock-tile:hover{transform:none}}


.pnl{font-variant-numeric:tabular-nums;font-weight:750}.pnl-profit{color:var(--up)!important}.pnl-loss{color:var(--down)!important}.pnl-neutral{color:var(--ink-2,var(--text-secondary))!important}
.paper-total{padding:24px!important;border-radius:20px!important}.paper-total .pnl{font-size:21px}.paper-sleeve{padding:22px;line-height:1.75;border:1px solid var(--line,var(--border));border-radius:20px;background:var(--panel,var(--surface-1))}.paper-sleeve-long{border-top:4px solid #39c996;background:linear-gradient(145deg,#23a67612,transparent)}
.long-research{--long-accent:#62dfb1;--long-soft:#1da87512;display:grid;gap:12px}.long-research>.audit-note{border:1px solid #39c99666;border-left:5px solid #39c996;border-radius:20px;padding:24px;background:linear-gradient(125deg,#1da8751c,transparent)}.long-research>.audit-note>b{font-size:20px;color:var(--long-accent)}.long-research h3{color:var(--long-accent);font-size:20px;border-left:3px solid #39c996;padding-left:12px;margin:26px 0 4px}.long-research .watch-item{border-radius:18px;border-color:#39c99640;background:var(--long-soft);padding:20px}.long-research .watch-item>b{font-size:17px}.long-research .watch-item small{line-height:1.9}.long-research .tbl{border-radius:16px;overflow:hidden}.long-research th{background:var(--long-soft)}
:root[data-theme="light"] .long-research{--long-accent:#08764f;--long-soft:#eaf7f1}.long-research p{line-height:1.8}
@media(max-width:600px){.paper-total{padding:18px!important}.paper-total .pnl{font-size:18px}.long-research>.audit-note{padding:18px}.paper-sleeve{padding:18px}}

.company-search-link{border:1px solid var(--line,var(--border));border-radius:12px!important;background:var(--panel-2,var(--surface-2))}.company-search-box{display:flex;gap:12px;align-items:center;border:1px solid var(--line,var(--border));background:var(--panel-2,var(--surface-2));padding:16px 20px;border-radius:18px}.company-search-box input{font:inherit;background:transparent;color:inherit;border:0;outline:0;width:100%;font-size:18px}.company-search-box:focus-within{outline:2px solid var(--accent)}.company-results{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:10px;margin:18px 0}.company-results button,#runCompany{font:inherit;text-align:left;border:1px solid var(--line,var(--border));border-radius:14px;padding:16px;cursor:pointer;background:var(--panel-2,var(--surface-2));color:inherit}.company-results small{display:block;color:var(--muted,var(--text-muted));margin-top:6px}.company-results button:hover{border-color:var(--accent)}#runCompany{background:var(--accent);color:white}#runCompany:disabled{opacity:.5;cursor:wait}.company-report{padding:24px;border:1px solid var(--line,var(--border));border-radius:20px;margin:20px 0;line-height:1.85}.company-metrics{display:grid;grid-template-columns:repeat(auto-fit,minmax(180px,1fr));gap:12px}.company-metrics>div{background:var(--panel-2,var(--surface-2));padding:14px;border-radius:12px}.company-metrics small,.company-metrics b{display:block}.company-report small{font-weight:400;color:var(--muted,var(--text-muted))}@media(max-width:600px){.company-report{padding:16px}.company-results{grid-template-columns:1fr 1fr}}

/* Desktop navigation and readable green chart palette. */
:root{--chart-price:#38e6a0;--chart-ma20:#b9f56b;--chart-ma60:#53d6cb}
:root[data-theme="light"]{--chart-price:#087c50;--chart-ma20:#547d12;--chart-ma60:#087f89}
.kchart .price{stroke:var(--chart-price);stroke-width:2.8}
.kchart .ma20{stroke:var(--chart-ma20);stroke-width:2;stroke-dasharray:7 3}
.kchart .ma60{stroke:var(--chart-ma60);stroke-width:2;stroke-dasharray:2 4}
.kchart .price,.kchart .ma20,.kchart .ma60{vector-effect:non-scaling-stroke;stroke-linecap:round}
.chart-head .px{color:var(--chart-price)}
.legend i{width:22px;height:3px}.legend .sw20{background:none!important;border-top:3px dashed var(--chart-ma20);height:0}
.legend .sw60{background:none!important;border-top:3px dotted var(--chart-ma60);height:0}
.kpi .v{font-size:clamp(21px,2.1vw,30px);line-height:1.3;word-break:keep-all}
.audit-note{padding:15px 18px;border:1px solid var(--line);border-left:3px solid var(--warn);border-radius:10px;background:var(--panel);font-size:13px;line-height:1.7;color:var(--ink-2)}
.audit-note a{color:var(--accent)}.audit-item{padding:14px 0;border-bottom:1px solid var(--line);line-height:1.7;overflow-wrap:anywhere}
@media(min-width:641px){
 .topbar-inner{width:100%;box-sizing:border-box;display:grid;grid-template-columns:minmax(220px,1fr) auto auto;gap:10px 18px;padding:15px 24px 0}
 .brand{grid-column:1}.clk{grid-column:2}.btn{grid-column:3;grid-row:1}.ro{grid-column:1 / -1;justify-self:start;font-size:11px}
 .tabs{grid-column:1 / -1;grid-row:3;display:flex;flex-wrap:nowrap;gap:5px;overflow-x:auto;width:100%;padding:6px 0 12px;order:0}
 .tabs a{flex:1;text-align:center;font-size:13px;padding:11px 12px;border:1px solid var(--line);border-radius:9px}
 .board{position:static}.hero-3{grid-template-columns:minmax(0,1fr) 280px}
}
@media(min-width:641px) and (max-width:1050px){.hero-3{grid-template-columns:minmax(0,1fr)}.kpis{grid-template-columns:repeat(2,minmax(0,1fr))!important}.tabs a{flex:0 0 auto}}

.report-paper .report-heading{display:block;position:static;height:auto;min-height:0;padding:24px 0 22px;margin:0 0 24px;background:transparent;border-bottom:1px solid #d5dfd4}.report-paper .report-kicker{display:block;margin-bottom:10px}.report-paper .report-heading h2{margin:10px 0 18px}.report-paper section{display:block;background:transparent;border:0;border-bottom:1px solid #d5dfd4;border-radius:0;margin:0;padding:12px 0 24px}.report-paper p{margin:12px 0}.report-paper .report-heading p{margin:8px 0}.report-paper li{margin:6px 0}

.report-preview{padding:28px 30px 12px}.report-preview-meta{display:flex;align-items:center;gap:12px}.report-preview h3{font-size:18px;margin:18px 0 12px}.report-abstract-verdict{font-size:25px;font-weight:750;line-height:1.4;margin:12px 0;color:var(--ink)}.report-preview ul{line-height:1.8;padding-left:20px;margin:18px 0}.report-probability{font-size:14px;color:var(--muted);line-height:1.8}.report-caution{font-size:13px;color:var(--muted);padding-top:14px;border-top:1px solid var(--line);line-height:1.8}.report-coverage{font-size:14px;color:var(--muted)}.research-document .report-detail>summary{border-top:1px solid var(--line);padding:18px 30px;justify-content:space-between;color:var(--ink);font-weight:650}.report-open-label,.report-close-label{color:#65cda4}.report-close-label{display:none}.report-detail[open] .report-open-label{display:none}.report-detail[open] .report-close-label{display:inline}.report-reading{font-size:12px;font-weight:400;color:var(--muted)}.report-paper .report-executive{border-top:2px solid #236947;padding:20px 0 24px}.report-paper .report-executive h3{margin-top:0}.report-paper .report-conclusion{font-size:29px;color:#173c2b}.report-preview .report-date{margin-left:auto}
@media(max-width:650px){.report-preview{padding:22px 20px 10px}.report-preview-meta{flex-wrap:wrap}.report-preview .report-date{width:auto;margin-left:0}.report-abstract-verdict{font-size:22px}.research-document .report-detail>summary{padding:17px 20px}.report-reading{width:100%;font-size:11px}.report-paper .report-conclusion{font-size:25px}}
@media print{.research-document:has(.report-detail[open]){display:block!important}.research-document:not(:has(.report-detail[open])){display:none!important}.report-preview,.report-detail>summary{display:none!important}.report-detail[open]{display:block!important}}

/* ===== (V3.29) 토스증권 느낌: 큰 글씨·넓은 여백·핵심만 먼저, 나머지는 '자세히'로 접기 ===== */
body{font-family:"Pretendard","Apple SD Gothic Neo","Malgun Gothic","IBM Plex Sans KR",system-ui,sans-serif;font-size:16px;letter-spacing:-.01em;line-height:1.6}
.mono,.num{font-family:inherit;font-variant-numeric:tabular-nums;letter-spacing:-.01em}
section,.hero-card,.board{border-radius:22px;border-color:transparent;padding:26px 26px}
.hero-card{padding:28px}
h2{font-size:20px;font-weight:700;letter-spacing:-.02em}
.h2-sub,.ev,.legend,.foot,.grp,.gauge-lbl,.label{font-size:13.5px}
.label{font-weight:700;color:var(--ink);letter-spacing:0;text-transform:none;margin-top:6px}
.why-dir{font-size:42px;font-weight:800;letter-spacing:-.03em;line-height:1.15}
.toss-lead{font-size:18px;color:var(--ink-2);font-weight:500;margin-top:-4px}
.hero-card .chip,.chip{font-size:15px;padding:7px 14px;border:0;background:var(--panel-2);color:var(--ink)}
.evidence{gap:10px}
.evidence li{font-size:16px;padding:14px 16px;border:0;border-radius:16px;grid-template-columns:28px 1fr}
.evidence .n{font-size:14px;font-weight:700;color:var(--accent)}
.evidence .fact{font-size:13.5px}
.vote{font-size:14px;padding:6px 12px}
.kpi{border-radius:20px;border-color:transparent;padding:20px 22px}
.kpi .l{font-size:14px;letter-spacing:0;text-transform:none;color:var(--ink-2)}
.kpi .v{font-size:30px;font-weight:800;letter-spacing:-.02em}
.kpi .d{font-size:14px}
.board td{font-size:14.5px;padding:9px 0}.board td:first-child{white-space:nowrap;padding-right:10px}.board td.num{padding-left:12px;white-space:nowrap}
.board .kq .px{font-size:36px;font-weight:800;letter-spacing:-.02em}
.kchart .ax{font-family:inherit;font-size:12px}
details.more{border-top:1px solid var(--line);padding-top:12px;margin-top:4px;display:grid;gap:10px}
details.more>summary{cursor:pointer;list-style:none;font-size:15px;font-weight:600;color:var(--ink-2);padding:4px 0}
details.more>summary::-webkit-details-marker{display:none}
details.more>summary::after{content:"  ▾";color:var(--muted)}
details.more[open]>summary::after{content:"  ▴"}
details.more>summary:hover{color:var(--ink)}
@media (max-width:640px){
  body{font-size:15.5px}
  section,.hero-card,.board{padding:20px 18px;border-radius:18px}
  .why-dir{font-size:34px}.toss-lead{font-size:16.5px}
  .kpi .v{font-size:24px}.kpi{padding:16px}
  .brand small{display:none}
}
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
  // (V3.26) 보는 사람 기준 현재 시각(KST)과 스냅샷이 얼마나 지났는지. 오래되면 경고.
  (function(){
    var F=null; try{F=new Intl.DateTimeFormat('ko-KR',{timeZone:'Asia/Seoul',month:'2-digit',day:'2-digit',weekday:'short',hour:'2-digit',minute:'2-digit',second:'2-digit',hour12:false});}catch(e){}
    var gen=Date.parse(String(D.generated_at).replace(' ','T')+(/[+-]\d\d:?\d\d$|Z$/.test(D.generated_at)?'':'+09:00'));
    function ago(ms){var s=Math.max(0,Math.round(ms/1000));return s<60?s+'초 전':s<3600?Math.floor(s/60)+'분 전':s<86400?Math.floor(s/3600)+'시간 '+Math.floor(s%3600/60)+'분 전':Math.floor(s/86400)+'일 전';}
    function tick(){
      if(F){var o={};F.formatToParts(new Date()).forEach(function(p){o[p.type]=p.value;});
        $('clock').textContent=o.month+'.'+o.day+'('+o.weekday+') '+String(Number(o.hour)%24).padStart(2,'0')+':'+o.minute+':'+o.second+' KST';}
      if(isFinite(gen)){var d=Date.now()-gen;$('genAgo').textContent=ago(d);
        var st=$('stale'); if(d>30*3600*1000){st.hidden=false;st.textContent='이 페이지는 '+ago(d)+'에 만든 스냅샷입니다. 최신 예측이 아닐 수 있어요.';}}
    }
    tick(); if(typeof setInterval==='function') setInterval(tick,1000);
  })();
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
  var liveQuote=D.public_quote, KH0=D.kospi_history, kq='';
  if(KH0&&KH0.closes&&KH0.closes.length>1){var kl=KH0.closes[KH0.closes.length-1],kp=KH0.closes[KH0.closes.length-2],kc=(kl/kp-1)*100;
    kq='<div class="kq '+cc(kc)+'"><div class="nm"><span>KOSPI '+esc(KH0.dates[KH0.dates.length-1])+' 종가</span><span>전일 '+fv(kp)+'</span></div><div class="px mono">'+fv(kl)+'</div><div class="ch mono">'+pct(kc)+' ('+(kl-kp).toFixed(2)+')</div></div>';}
  if(liveQuote){kq='<div class="kq '+cc(liveQuote.change_pct)+'"><div class="nm"><span>KOSPI '+esc(liveQuote.date)+(liveQuote.intraday?' 장중':' 종가')+'</span><span>전일 '+fv(liveQuote.prev)+'</span></div><div class="px mono">'+fv(liveQuote.value)+'</div><div class="ch mono">'+pct(liveQuote.change_pct)+' ('+(liveQuote.value-liveQuote.prev).toFixed(2)+')</div><div class="ev">시세 기준 '+esc(liveQuote.traded_at)+' · '+esc(liveQuote.source)+'</div></div>';}
  $('board').innerHTML='<h2>시장 현황 <span class="h2-sub">'+(liveQuote?'KOSPI 시세 갱신':inp0.as_of?'분석 당시 · '+esc(inp0.as_of):'')+'</span></h2>'+kq+
    '<div class="grp">참고 미국 지수 · 분석 당시 '+esc(inp0.as_of||'')+'</div><table>'+(inp0.us||[]).map(brow).join('')+'</table>'+
    ((inp0.macro||[]).length?'<div class="grp">거시 지표</div><table>'+(inp0.macro||[]).map(brow).join('')+'</table>':'')+
    (function(){var f=D.public_flow||inp0.kr_flow,live=!!D.public_flow;
      if(!f||f.foreign_net_krw_bn==null)return '<div class="grp">외국인 수급</div><div class="ev">데이터 없음 · 다음 갱신에서 재시도</div>';
      var v=Number(f.foreign_net_krw_bn);
      return '<div class="grp">외국인 수급 · '+esc(f.date||'')+(live?' · 시세 갱신':' · 분석 당시')+'</div><table><tr><td>KOSPI 외국인 '+(v<0?'순매도':v>0?'순매수':'순매수·매도 균형')+'</td><td class="num chg '+cc(v)+'" style="white-space:nowrap">'+Math.abs(v).toLocaleString()+'억원</td></tr></table><div class="ev">'+(live?'수집 '+esc(f.checked_at||'')+' · '+esc(f.source||''):'분석 당시 저장값 · 현재 수급 아님')+(D.public_flow_status==='unavailable'?' · 최신 조회 실패, 이전 값 표시':'')+'</div>';
    })()+
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

  if(p.direction)$('pred').innerHTML=predictionComparison(p.direction,D.public_quote,D.target_date,'pt')+$('pred').innerHTML;

  function aiTrack(recs,W,pl,pr){
    // 최근 기록(최대 20건)을 날짜순으로 같은 간격에 놓는다(지수 차트와 별도 축). y = AI 상승확률.
    // 적중 ●(초록) / 빗나감 ×(주황) / 채점 대기 ○
    var H2=128,t=16,b=26;
    function Y2(v){return t+(1-v/100)*(H2-t-b);}
    var recent=recs.slice().sort(function(a,c){return String(a.target_date||'').localeCompare(String(c.target_date||''));}).slice(-20);
    var rs=recent.filter(function(r){var v=r.up_probability;return typeof v==='number'&&isFinite(v)&&v>=0&&v<=100;})
      .map(function(r){return {r:r,v:r.up_probability,d:String(r.target_date||'').slice(0,10)};});
    var head='<div class="aihead"><b>AI 예측 기록</b><span>세로축 = AI가 매긴 <b>상승 확률</b>(50% 아래 = 하락 예측). KOSPI 등락률이 아닙니다.</span></div>';
    var note='<div class="ev">최근 '+recent.length+'건 중 상승확률이 저장된 '+rs.length+'건 표시'+(recent.length>rs.length?' · 확률 없음/오류 '+(recent.length-rs.length)+'건 제외':'')+' · 가로축: 예측 대상일</div>';
    if(!rs.length)return head+note+'<div class="empty">표시할 상승확률 기록이 없습니다. 새 예측이 저장되면 표시됩니다.</div>';
    var L=pl+34,R=W-pr-10,m=rs.length;
    var pts=rs.map(function(q,k){q.x=m===1?(L+R)/2:L+k*(R-L)/(m-1);return q;});
    var step=Math.max(1,Math.ceil(m/(W<500?4:8)));
    var xl=pts.map(function(q,k){return (k%step===0||k===m-1)?'<text class="ax" x="'+q.x.toFixed(1)+'" y="'+(H2-8)+'" text-anchor="middle">'+esc(q.d.slice(5))+'</text>':'';}).join('');
    var g='';[0,50,100].forEach(function(v){g+='<line class="'+(v===50?'mid':'g')+'" x1="'+pl+'" x2="'+(W-pr)+'" y1="'+Y2(v).toFixed(1)+'" y2="'+Y2(v).toFixed(1)+'"/><text class="ax" x="'+(W-pr+6)+'" y="'+(Y2(v)+4).toFixed(1)+'">'+v+'%'+(v===50?' 중립':'')+'</text>';});
    var ln=pts.length>1?'<path class="ailine" d="'+pts.map(function(q,i){return (i?'L':'M')+q.x.toFixed(1)+','+Y2(q.v).toFixed(1);}).join(' ')+'"/>':'';
    var mk=pts.map(function(q){var r=q.r,x=q.x.toFixed(1),y=Y2(q.v).toFixed(1),scored=r.verified===true&&typeof r.correct==='boolean',st=scored?(r.correct?'hit':'miss'):'wait';
      var tip=q.d+' AI 상승확률 '+Math.round(q.v)+'% ('+(q.v===50?'중립':q.v>50?'상승':'하락')+' 예측)'+
        (scored?(' → 실제 '+(r.actual_direction==='up'?'상승':r.actual_direction==='down'?'하락':r.actual_direction==='flat'?'보합':'방향 미기록')+(typeof r.actual_change_pct==='number'&&isFinite(r.actual_change_pct)?' '+pct(r.actual_change_pct):'')+' · '+(r.correct?'적중':'빗나감')+
          (typeof r.strategy_return_pct==='number'&&isFinite(r.strategy_return_pct)?' · 전략 '+pct(r.strategy_return_pct):'')):' → 채점 대기');
      var shape=st==='miss'?'<path class="m-miss" d="M'+(q.x-5).toFixed(1)+','+(Y2(q.v)-5).toFixed(1)+'l10,10m0,-10l-10,10"/>':'<circle class="m-'+st+'" cx="'+x+'" cy="'+y+'" r="5"/>';
      q.tip=tip;
      return '<g tabindex="0" role="button" aria-label="'+esc(tip)+'" data-tip="'+esc(tip)+'"><title>'+esc(tip)+'</title><circle cx="'+x+'" cy="'+y+'" r="11" fill="transparent"/>'+shape+'</g>';}).join('');
    var lastp=pts[pts.length-1];
    var lab='<text class="ax" x="'+(lastp.x-9).toFixed(1)+'" y="'+(Y2(lastp.v)+(lastp.v>=50?-9:16)).toFixed(1)+'" text-anchor="end" style="fill:var(--ink);font-weight:600">'+Math.round(lastp.v)+'%</text>';
    return head+note+
      '<svg class="kchart" viewBox="0 0 '+W+' '+H2+'" role="group" aria-label="AI 상승확률 기록 '+pts.length+'건">'+g+
      ln+mk+lab+xl+'</svg>'+
      '<div class="legend"><span><i class="lg-hit"></i>적중</span><span><i class="lg-miss">×</i>빗나감</span><span><i class="lg-wait"></i>채점 대기</span><span>점을 누르거나 올리면 실제 결과</span></div>'+
      '<div class="aitip" id="aiTip" aria-live="polite">'+esc(lastp.tip)+'</div>';
  }
  var KH=D.kospi_history;
  if(KH&&KH.closes&&KH.closes.length>5){
    var cl=KH.closes,dt=KH.dates,n=cl.length,W=Math.round(Math.min(720,Math.max(300,((typeof window!=='undefined'&&window.innerWidth)||1100)-(((typeof window!=='undefined'&&window.innerWidth)||1100)<700?64:96)))),H=W<500?210:260,pl=8,pr=W<500?50:62,pt=12,pb=26;
    function sma(a,k){return a.map(function(_,i){return i+1<k?null:a.slice(i+1-k,i+1).reduce(function(x,y){return x+y;},0)/k;});}
    var m20=sma(cl,20),m60=sma(cl,60),lo=Math.min.apply(null,cl)*0.995,hi=Math.max.apply(null,cl)*1.005;
    function X(i){return pl+(i/(n-1))*(W-pl-pr);} function Y(v){return pt+(1-(v-lo)/(hi-lo))*(H-pt-pb);}
    function path(a){return a.map(function(v,i){return v==null?null:((i===0||a[i-1]==null)?'M':'L')+X(i).toFixed(1)+','+Y(v).toFixed(1);}).filter(Boolean).join(' ');}
    var g='';for(var k=0;k<=4;k++){var v=lo+(hi-lo)*k/4;g+='<line class="g" x1="'+pl+'" x2="'+(W-pr)+'" y1="'+Y(v).toFixed(1)+'" y2="'+Y(v).toFixed(1)+'"/><text class="ax" x="'+(W-pr+6)+'" y="'+(Y(v)+4).toFixed(1)+'">'+Math.round(v).toLocaleString()+'</text>';}
    var xl=[0,Math.floor(n/2),n-1].map(function(i){return '<text class="ax" x="'+X(i).toFixed(1)+'" y="'+(H-8)+'" text-anchor="'+(i===0?'start':i===n-1?'end':'middle')+'">'+esc((dt[i]||'').slice(5))+'</text>';}).join('');
    var last=cl[n-1],prev=liveQuote?liveQuote.prev:(cl[n-2]||last),chg=(last/prev-1)*100,pr2='';
    if(p.direction){var up=p.direction==='up',x=X(n-1)-12,y=Math.max(pt+16,Math.min(H-pb-8,Y(last)+(up?-26:26))),c=up?'var(--up)':'var(--down)';
      pr2='<text class="ax ai-direction" x="'+x.toFixed(1)+'" y="'+y.toFixed(1)+'" text-anchor="end" style="fill:'+c+';font-weight:700;paint-order:stroke;stroke:var(--panel);stroke-width:4;stroke-linejoin:round">AI '+(up?'▲':'▼')+'</text>';}
    $('kChart').innerHTML='<svg class="kchart" viewBox="0 0 '+W+' '+H+'" role="img" aria-label="KOSPI 최근 '+n+'거래일"><defs><linearGradient id="kg" x1="0" y1="0" x2="0" y2="1"><stop offset="0%" stop-color="var(--chart-price)" stop-opacity="0.22"/><stop offset="100%" stop-color="var(--chart-price)" stop-opacity="0"/></linearGradient></defs>'+g+
      '<path fill="url(#kg)" d="'+path(cl)+' L'+X(n-1).toFixed(1)+','+(H-pb)+' L'+X(0).toFixed(1)+','+(H-pb)+' Z"/><path class="ma60" d="'+path(m60)+'"/><path class="ma20" d="'+path(m20)+'"/><path class="price" d="'+path(cl)+'"/>'+pr2+xl+'</svg>';
    $('kPx').textContent=last.toLocaleString(undefined,{minimumFractionDigits:2,maximumFractionDigits:2});
    $('kChg').textContent=pct(chg)+' ('+(last-prev).toFixed(2)+')';$('kChg').className='mono chg '+(chg>=0?'up':'down');
    $('kDate').textContent=liveQuote?liveQuote.traded_at+(liveQuote.intraday?' 장중 (미확정)':' 종가')+' · 최근 '+n+'거래일':dt[n-1]+' 종가 · 최근 '+n+'거래일';
  }
  // AI 확률 기록은 시장 차트 데이터 유무와 무관하게 표시한다.
  if(p.direction){$('kAi').innerHTML='AI 예측 <b class="'+(p.direction==='up'?'up':'down')+'">'+(p.direction==='up'?'▲ 상승':'▼ 하락')+'</b>'+(prob!=null?' · 상승확률 '+prob+'% <span class="ax2">(지수 등락률 아님)</span>':'');}
  var trackWidth=Math.round(Math.min(720,Math.max(300,((typeof window!=='undefined'&&window.innerWidth)||1100)-(((typeof window!=='undefined'&&window.innerWidth)||1100)<700?64:96))));
  $('aiTrack').innerHTML=aiTrack(D.records||[],trackWidth,8,trackWidth<500?62:76);
  if($('aiTrack').addEventListener){['click','mouseover','focusin','keydown'].forEach(function(ev){$('aiTrack').addEventListener(ev,function(e){
    if(ev==='keydown'&&e.key!=='Enter'&&e.key!==' ')return;
    var g=e.target&&e.target.closest?e.target.closest('g[data-tip]'):null;
    if(g&&$('aiTip')){if(ev==='keydown')e.preventDefault();$('aiTip').textContent=g.getAttribute('data-tip');}
  });});}
  if(liveQuote){$('priceSeriesLabel').textContent=liveQuote.intraday?'종가 / 오늘 장중값':'종가';$('quoteNotice').textContent='PC에서 5분마다 KOSPI 시세 확인 · 게시·새로고침 지연 가능 · 시세 수집 '+liveQuote.checked_at+' · AI 예측/정량지표/모의계좌는 별도 갱신';}
  var IN=[];
  if(Q.rsi14!=null)IN.push(['RSI(14)',Q.rsi14,QL.rsi]); if(Q.macd)IN.push(['MACD 히스토그램',Q.macd.hist,QL.macd]); if(Q.bb_pct_b!=null)IN.push(['볼린저 %B',Q.bb_pct_b,QL.bb]);
  if(Q.ma)IN.push(['이평 배열',Q.ma.above_ma20?'20일선 위':'20일선 아래',Q.ma.state]); if(Q.realized_vol_20!=null)IN.push(['실현변동성(연율)',Q.realized_vol_20+'%',QL.vol]);
  if(Q.mom_12_1!=null)IN.push(['12-1 모멘텀',pct(Q.mom_12_1),QL.momentum]); if(Q.flow_z5!=null)IN.push(['외국인 5일 z',Q.flow_z5,QL.flow]);
  $('inds').innerHTML=IN.map(function(t){return '<div class="ind"><div class="k">'+esc(t[0])+'</div><div class="v mono">'+esc(String(t[1]))+'</div>'+(t[2]?'<div class="t">'+esc(String(t[2]))+'</div>':'')+'</div>';}).join('');

  var RK=D.stock_rank||{};
  function sigt(g){if(g==='held')return '<span class="ev">판단 보류</span>';return g==='up'?'<span class="up">▲ 상승</span>':g==='down'?'<span class="down">▼ 하락</span>':'<span class="ev">— 보합</span>';}
  function predictionComparison(sig,q,target,unit){
    var pred=sig==='up'?'▲ 상승':sig==='down'?'▼ 하락':sig==='flat'?'— 보합':'판단 없음';
    var lead='<div class="ev" style="padding:10px;border:1px solid var(--line);border-radius:9px"><b>예측 '+esc(pred)+'</b> · 대상 '+esc(target||'미기록');
    if(!q||typeof q.change_pct!=='number'||!isFinite(q.change_pct))return lead+'<br>현재 시세 조회 대기 · 비교 불가</div>';
    var v=q.change_pct,actual=v>0?'▲ 상승':v<0?'▼ 하락':'— 보합';
    var ready=q.status!=='unavailable'&&q.date===target;
    var result=!ready?(q.status==='unavailable'?'최신 조회 실패 · 이전 시세, 비교 보류':'예측 대상일과 시세 날짜가 달라 비교 보류'):
      (sig==='flat'?(typeof D.stock_flat_threshold_pct==='number'?(Math.abs(v)<D.stock_flat_threshold_pct?'현재 보합 범위 내':'현재 보합 범위 이탈')+' (|등락률| < '+D.stock_flat_threshold_pct+'% 기준)':'보합 기준 미기록 · 비교 보류'):sig!=='up'&&sig!=='down'?'예측 방향 없음 · 비교 보류':v===0?'현재 보합 · 방향 비교 대기':
      ((sig==='up'&&v>0)||(sig==='down'&&v<0)?'현재 방향 일치':'현재 방향 불일치'));
    return lead+'<br>시세 <b class="'+cc(v)+'">'+actual+' '+pct(v)+'</b> · '+fv(q.value)+(unit||'원')+
      '<br>'+esc(result)+' · '+(q.intraday?'장중 참고':'종가 기준 참고')+' (확정 채점 아님)'+
      '<br>시세 날짜 '+esc(q.date||'')+' · 수집 '+esc(q.checked_at||q.traded_at||'')+' · '+esc(q.source||'네이버 증권')+'</div>';
  }
  function stockForecastLabel(s){
    if(s.assessment_status==='held')return '판단 보류 · '+(s.assessment_note||'근거 부족');
    if(!s.assessment_status&&s.directional_signal==='flat'&&!['up','down','neutral'].includes(s.direction_bias))return '기존 보합 기록 · 세부 판단 미검증';
    if(s.directional_signal==='up')return '상승 예상';
    if(s.directional_signal==='down')return '하락 예상';
    if(s.directional_signal!=='flat')return '판단 미기록';
    var bias=s.direction_bias==='up'?'상승 쪽 우세':s.direction_bias==='down'?'하락 쪽 우세':s.direction_bias==='neutral'?'방향 우세 없음':'세부 방향 미기록';
    return '보합 예상 ('+bias+')';
  }
  function card(s){var sig=s.assessment_status==='held'?'held':s.directional_signal;
    var bias=s.direction_bias==='up'?'상승 쪽 우세':s.direction_bias==='down'?'하락 쪽 우세':s.direction_bias==='neutral'?'방향 우세 없음':'세부 방향 미기록';
    return '<div class="stock"'+(s.moderated?' style="border-style:dashed"':'')+'><div class="n"><span>'+esc(s.name)+(RK[s.name]?' <span class="ev">시총 '+RK[s.name]+'위</span>':'')+'</span>'+sigt(sig)+'</div>'+
      '<div class="m">'+esc(s.related_us_indicator||s.sector||'')+' · 민감도 '+esc(s.sensitivity||'-')+'</div>'+
      (s.moderated?'<div class="chip" style="color:var(--warn);border-color:var(--warn)">중재자 조정 · 원래 '+(s.original_signal==='up'?'상승':'하락')+'</div>':'')+
      (sig==='flat'?'<div class="ev">보합권 · '+bias+' · 확신도 '+esc(({low:'낮음',mid:'보통',high:'높음'})[s.bias_confidence]||'미기록')+'</div>':'')+predictionComparison(sig,(D.stock_quotes||{})[s.name],D.target_date)+
      '<div class="r">'+(s.assessment_status==='held'?esc(s.assessment_note||'근거 부족')+' · ':'')+esc(s.bias_reason||'')+'<br>'+esc(s.moderated&&s.moderation_reason?s.moderation_reason:(s.reason||''))+'</div></div>';}
  $('core').innerHTML=(D.core_stocks||[]).map(card).join('')||'<div class="empty">없음</div>';
  $('watch').innerHTML=((D.watch_stocks||[]).map(card).join('')||'<div class="empty">없음</div>')+
    (D.stock_moderation?'<div class="ev" style="grid-column:1/-1">추천 분석 대상은 KOSPI 시총 100에서 선정한 종목이며 매수 추천이 아닙니다.  종목 중재자가 '+D.stock_moderation.reviewed+'종목을 검토해 '+D.stock_moderation.adjusted_to_flat+'종목을 보합으로 조정했습니다.</div>':'');

  (function(){
  if(typeof document.createElement!=='function')return;
  var stockItems=(D.core_stocks||[]).concat(D.watch_stocks||[]);
  function stockHistory(q){return (q&&q.history||[]).filter(function(r){return /^\d{4}-\d{2}-\d{2}$/.test(r.date)&&typeof r.close==='number'&&isFinite(r.close)&&r.close>0;});}
  function stockLine(rows,small){
    if(rows.length<2)return '<p class="ev">가격 흐름 자료를 기다리고 있어요</p>';
    var values=rows.map(function(r){return r.close;}),lo=Math.min.apply(null,values),hi=Math.max.apply(null,values),pad=Math.max((hi-lo)*.15,hi*.001),bottom=lo-pad,range=hi-lo+2*pad;
    var pts=rows.map(function(r,i){return [20+i*560/(rows.length-1),160-(r.close-bottom)/range*140];});
    var path=pts.map(function(p){return p.join(',');}).join(' ');
    return '<svg class="stock-line" viewBox="0 0 600 180" role="img" aria-label="'+esc(rows[0].date+'부터 '+rows[rows.length-1].date+'까지 가격 흐름')+'"><path d="M20 160H580" stroke="currentColor" opacity=".15"/><polyline points="'+path+'" fill="none" stroke="var(--chart-price)" stroke-width="'+(small?5:3)+'" stroke-linecap="round" stroke-linejoin="round"/>'+(small?'':pts.map(function(p,i){return '<circle cx="'+p[0]+'" cy="'+p[1]+'" r="4" fill="var(--chart-price)"><title>'+esc(rows[i].date+' · '+fv(rows[i].close)+'원')+'</title></circle>';}).join(''))+'</svg>';
  }
  function simpleStock(s){var q=(D.stock_quotes||{})[s.name]||{},idx=stockItems.indexOf(s);
    return '<button type="button" class="stock-tile" data-stock-open="'+idx+'" aria-label="'+esc(s.name)+' 주가와 분석 상세 보기"><span class="stock-top"><span class="stock-avatar">'+esc(s.name.slice(0,1))+'</span><span><strong>'+esc(s.name)+'</strong><small>'+esc(q.code||'종목코드 확인 중')+'</small></span><span class="stock-arrow">›</span></span><span class="stock-price">'+(typeof q.value==='number'?fv(q.value)+'원':'시세 대기')+'</span><span class="'+cc(q.change_pct)+'">'+(typeof q.change_pct==='number'?pct(q.change_pct):'—')+' <small>전일 대비</small></span>'+stockLine(stockHistory(q),true)+'<span class="stock-bottom">AI '+esc(stockForecastLabel(s))+'<small>'+(q.status==='unavailable'?'갱신 실패 · 이전 자료':esc(q.date||'자료 대기'))+'</small></span></button>';
  }
  $('core').innerHTML=(D.core_stocks||[]).map(simpleStock).join('')||'<p class="empty">추천 종목을 기다리고 있어요</p>';
  $('watch').innerHTML=(D.watch_stocks||[]).map(simpleStock).join('')||'<p class="empty">추천 분석 종목을 기다리고 있어요</p>';
  var detail=document.createElement('dialog');detail.className='stock-dialog';detail.setAttribute('aria-labelledby','stockDetailTitle');document.body.appendChild(detail);
  var stockTrigger=null;
  function selectedCompanyReport(name){var list=((D.company_research||{}).reports||[]).filter(function(r){return r.name===name;});return '<details class="stock-research-detail"><summary>기업 심층분석 보고서 보기</summary>'+companyReportView(list.length?list[list.length-1]:null,esc)+'</details>';}
  function showStock(index){var s=stockItems[index];if(!s)return;var q=(D.stock_quotes||{})[s.name]||{},rows=stockHistory(q);
    detail.innerHTML='<div class="stock-detail-head"><div><small>종목 살펴보기 · '+esc(q.code||'코드 미확인')+'</small><h2 id="stockDetailTitle">'+esc(s.name)+'</h2></div><button type="button" class="detail-close" aria-label="종목 상세 닫기">×</button></div><div class="stock-price">'+(typeof q.value==='number'?fv(q.value)+'원':'시세 대기')+'</div><p class="'+cc(q.change_pct)+'">'+(typeof q.change_pct==='number'?pct(q.change_pct)+' · 전일 대비':'현재 시세 없음')+'</p><div class="stock-period"><button type="button" data-period="5">최근 5일</button><button type="button" data-period="10" aria-pressed="true">최근 10일</button></div><div id="stockChartArea"></div><p class="ev">일별 종가 흐름 · 오늘 장중에는 마지막 값이 변동됩니다. '+esc(q.source||'출처 대기')+' · 수집 '+esc(q.checked_at||'미확인')+'</p><details class="stock-prices"><summary>날짜별 가격 보기</summary><div id="stockPriceRows"></div></details><h3>AI는 이렇게 판단했어요</h3>'+card(s)+selectedCompanyReport(s.name)+'<p class="ev">예측은 매수 지시가 아닙니다. 이 화면에서는 주문이나 AI 분석을 실행하지 않습니다.</p>';
    function draw(count){var r=rows.slice(-count);detail.querySelector('#stockChartArea').innerHTML=stockLine(r,false)+(r.length?'<p class="stock-dates"><span>'+esc(r[0].date)+'</span><span>'+esc(r[r.length-1].date)+' · '+r.length+'개 관측값</span></p>':'');detail.querySelector('#stockPriceRows').innerHTML=r.map(function(x){return '<p class="stock-dates"><span>'+esc(x.date)+'</span><b>'+fv(x.close)+'원</b></p>';}).join('')||'자료 없음';detail.querySelectorAll('[data-period]').forEach(function(b){b.setAttribute('aria-pressed',String(Number(b.dataset.period)===count));});}
    draw(10);detail.querySelectorAll('[data-period]').forEach(function(b){b.addEventListener('click',function(){draw(Number(b.dataset.period));});});detail.querySelector('.detail-close').addEventListener('click',function(){detail.close();});detail.showModal();
  }
  document.querySelectorAll('[data-stock-open]').forEach(function(b){b.addEventListener('click',function(){stockTrigger=b;showStock(Number(b.dataset.stockOpen));});});
  detail.addEventListener('close',function(){if(stockTrigger)stockTrigger.focus();});

  })();
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
  function curve(series,w,h){w=w||Math.round(Math.min(720,Math.max(300,((typeof window!=='undefined'&&window.innerWidth)||1100)-(((typeof window!=='undefined'&&window.innerWidth)||1100)<700?64:96))));h=h||(w<500?150:140);var all=[];series.forEach(function(x){all=all.concat(x.v);});if(!all.length)return '';
    var lo=Math.min.apply(null,[0].concat(all)),hi=Math.max.apply(null,[0].concat(all)),pad=6,span=(hi-lo)||1,n=Math.max.apply(null,series.map(function(x){return x.v.length;}));
    function X(i){return pad+(n>1?i/(n-1):0)*(w-2*pad);}var top=12+series.length*16;function Y(v){return h-pad-((v-lo)/span)*(h-pad-top);}
    return '<svg viewBox="0 0 '+w+' '+h+'" width="100%" height="'+h+'"><line x1="0" y1="'+Y(0)+'" x2="'+w+'" y2="'+Y(0)+'" stroke="var(--line)"/>'+
      series.map(function(x){return '<polyline fill="none" stroke="'+x.c+'" stroke-width="2" points="'+x.v.map(function(v,i){return X(i).toFixed(1)+','+Y(v).toFixed(1);}).join(' ')+'"/>';}).join('')+
      series.map(function(x,k){return '<text x="'+pad+'" y="'+(14+k*14)+'" font-size="11" fill="'+x.c+'">'+esc(x.n)+' '+x.v[x.v.length-1].toFixed(2)+'%</text>';}).join('')+'</svg>';}
  function prow(l,q,b){function f(v){return typeof v==='number'?v.toFixed(2):'—';}
    return '<tr'+(b?' style="font-weight:600"':'')+'><td>'+esc(l)+'</td><td class="num '+(q.total_return_pct>=0?'up':'down')+'">'+f(q.total_return_pct)+'%</td><td class="num">'+(q.sharpe_annual==null||(q.days||0)<20?'<span class="ev">표본 부족</span>':f(q.sharpe_annual))+'</td><td class="num">'+f(q.max_drawdown_pct)+'%</td><td class="num">'+(q.win_rate==null?'—':Math.round(q.win_rate*100)+'%')+'</td><td class="num">'+(q.traded_days==null?'—':q.traded_days)+'/'+q.days+'</td></tr>';}
  var returnDates=rb&&rb.dates||[];
  var returnNote=rb?'<div class="audit-note"><b>과거 채점 기록의 누적 전략 성과 · 실제 실현손익 아님</b><br>집계 '+(returnDates.length?esc(returnDates[0])+' ~ '+esc(returnDates[returnDates.length-1]):'기간 미기록')+' · '+rb.days+'거래일'+(rb.proxy_days?' · '+rb.proxy_days+'일은 ETF 대신 지수로 근사':'')+'<br>오늘 장중 평가손익과 별개입니다. 미채점 예측은 포함하지 않으며, 모의계좌의 체결·손익은 <a href="#paper">모의투자 수익률</a>에서 확인하세요.</div>':'';
  $('ret').innerHTML = rb ? returnNote+curve([{n:'AI 포지션',v:rb.ai.equity_curve_pct||[],c:'var(--accent)'},{n:'바이앤홀드',v:rb.buy_hold.equity_curve_pct||[],c:'var(--muted)'}])+
    '<div class="tbl"><table><thead><tr><th>방식</th><th class="num">누적</th><th class="num">샤프</th><th class="num">MDD</th><th class="num">승률</th><th class="num">거래일</th></tr></thead><tbody>'+
    prow('기존 신호 가정성과 (실제 체결 아님)',rb.ai,true)+prow('KOSPI 바이앤홀드',rb.buy_hold,false)+Object.keys(rb.baselines||{}).map(function(k){return prow(rb.baselines[k].label+' (100%)',rb.baselines[k],false);}).join('')+
    Object.keys(rb.by_agent||{}).map(function(k){return prow((AL[k]||k)+' (방향 100%)',rb.by_agent[k],false);}).join('')+'</tbody></table></div>'+
    '<div class="ev">'+(function(){var best=rb.baselines&&rb.best_baseline?rb.baselines[rb.best_baseline]:null,bt=best?best.total_return_pct:null,a=rb.ai.total_return_pct,bh=rb.buy_hold.total_return_pct;
      if(a>bh&&(bt==null||a>bt))return 'AI 포지션이 바이앤홀드·최고 단순규칙보다 수익이 높습니다.';
      if(a>bh&&bt!=null&&Math.abs(a-bt)<0.005)return 'AI 포지션이 바이앤홀드보다 높고, 최고 단순규칙('+esc(best.label)+')과 같습니다.';
      if(a>bh)return 'AI 포지션이 바이앤홀드보다는 높지만 단순규칙('+esc(best.label)+')보다 낮습니다.';
      return '아직 바이앤홀드가 AI 포지션보다 수익이 높습니다.';})()+
    ' 포지션 = (2p−1)×½켈리×목표변동성15%/실현변동성, |2p−1|<0.1이면 현금. 왕복 비용 0.1% 차감. 표본 '+rb.days+'일.</div>'
    : '<div class="empty">검증된 기록이 쌓이면 수익률(롱=KODEX200 · 쇼트=KODEX인버스 · 현금)이 표시됩니다.</div>';

function companyReportView(r,esc){
 if(!r)return '<p class="empty">아직 저장된 심층분석이 없습니다. 내 PC 대시보드에서 분석을 실행하면 이곳에서 볼 수 있습니다.</p>';
 var labels={reviewed:'검토 완료',held:'판단 보류',failed:'분석 실패'};
 var metrics={price:'가격',ttm_income:'최근 12개월 순이익',ttm_fcf:'최근 12개월 잉여현금흐름',revenue_yoy_pct:'매출 전년 대비 (%)',debt_equity:'부채/자본',pb:'PBR',ttm_eps:'최근 12개월 EPS'};
 return '<article class="company-report"><p class="ev">기업 조사 · '+esc(r.generated_at)+' · '+esc(labels[r.status]||'확인 필요')+'</p><h2>'+esc(r.name)+' <small>'+esc(r.code)+'</small></h2><p>'+esc(r.note)+'</p><p class="ev">모의계좌와 독립된 조사 보고서 · 자동 매매 지시가 아닙니다.</p><h3>재무·가치평가 자료</h3>'+(r.candidates||[]).map(function(c){return '<p>결산기 '+esc(c.period_end||'미확인')+' · '+esc(c.source||'자료 없음')+'</p><div class="company-metrics">'+Object.keys(metrics).map(function(k){var v=(c.metrics||{})[k];return '<div><small>'+metrics[k]+'</small><b>'+(typeof v==='number'&&isFinite(v)?v.toLocaleString(undefined,{maximumFractionDigits:2}):'자료 없음')+'</b></div>';}).join('')+'</div><p class="ev">부족한 자료: '+esc((c.missing||[]).join(', ')||'보고된 누락 없음')+'</p>';}).join('')+'<h3>분석가와 반대 검토</h3>'+((r.roles||[]).map(function(role){return '<section><h4>'+esc(role.name)+'</h4>'+(role.stocks||[]).map(function(s){return '<p>'+esc(({consider:'검토 가능',watch:'관찰',reject:'반대'})[s.stance]||s.stance)+'</p><ul>'+(s.points||[]).map(function(p){return '<li>'+esc(p)+'</li>';}).join('')+'</ul><b>판단을 다시 검토할 조건</b><ul>'+(s.invalidation||[]).map(function(p){return '<li>'+esc(p)+'</li>';}).join('')+'</ul>';}).join('')+'<p>'+esc((role.summary||[]).join(' '))+'</p></section>';}).join('')||'<p>자료·검증 요건을 충족하지 못해 AI 검토를 완료하지 못했습니다.</p>')+'<h3>근거 출처</h3><ul>'+(r.sources||[]).map(function(s){return '<li>'+esc(s.id)+' · '+esc(s.source)+' · '+esc(s.date)+'</li>';}).join('')+'</ul><p class="ev">뉴스 '+Number(r.news_count||0)+'건 · '+esc(r.news_status||'확인 불가')+' · 집계 재무자료와 뉴스 제목 기반 조사이며 공시 원문 전체 검증이 아닙니다. 재무 금액 단위는 원자료 기준입니다.</p></article>';
}
function mountCompanySearch(data,esc,run){
 var input=document.getElementById('companyQuery'),list=document.getElementById('companyResults'),body=document.getElementById('companyBody');
 if(!input||!list||!body)return;
 var selected=null,busy=false;
 function render(){var q=input.value.trim().toLowerCase();var rows=(data.companies||[]).filter(function(c){return !q||c.name.toLowerCase().includes(q)||c.code.includes(q);}).slice(0,12);
 list.innerHTML=rows.map(function(c){var saved=(data.reports||[]).some(function(r){return r.code===c.code;});return '<button type="button" data-company-code="'+esc(c.code)+'"><b>'+esc(c.name)+'</b><small>'+esc(c.code)+' · '+(saved?'보고서 있음':'미분석')+'</small></button>';}).join('')||'<p>지원 목록에서 찾지 못했습니다. 현재 코스피 시총 상위 100개 관리 기업을 지원합니다.</p>';
 list.querySelectorAll('[data-company-code]').forEach(function(b){b.addEventListener('click',function(){selected=b.getAttribute('data-company-code');show();});});}
 function show(){var records=(data.reports||[]).filter(function(r){return r.code===selected;}).reverse();var c=(data.companies||[]).find(function(c){return c.code===selected;});
 body.innerHTML='<h3>'+esc(c?c.name:selected)+'</h3>'+(run?'<p class="ev">내 PC 전용 · 하루 1건, 최대 3회 모델 호출 · 실패해도 당일 실행 한도는 유지됩니다. 실행 시 API 비용이 발생할 수 있습니다.</p><button type="button" id="runCompany" '+(busy?'disabled':'')+'>'+(busy?'분석 중…':'이 기업 새 심층분석 실행')+'</button>':'<p class="ev">공개 페이지에서는 저장된 보고서만 볼 수 있습니다.</p>')+companyReportView(records[0],esc)+(records.length>1?'<details><summary>이전 조사 '+(records.length-1)+'건</summary>'+records.slice(1).map(function(r){return companyReportView(r,esc);}).join('')+'</details>':'');
 if(run)body.querySelector('#runCompany').addEventListener('click',async function(){if(busy)return;busy=true;var code=selected;show();try{var r=await run(code);data.reports=(data.reports||[]).concat([r]);}catch(e){body.insertAdjacentHTML('beforeend','<p role="alert">'+esc(e.message||'분석 실패')+'</p>');busy=false;body.querySelector('#runCompany').disabled=false;body.querySelector('#runCompany').textContent='이 기업 새 심층분석 실행';return;}busy=false;show();});
 }
 input.addEventListener('input',render);render();
}

function paperProfit(v,percent){
 if(typeof v!=='number'||!isFinite(v))return '<span class="pnl-neutral">미확인</span>';
 var rounded=Number(v.toFixed(percent?3:0));
 var cls=rounded>0?'pnl-profit':rounded<0?'pnl-loss':'pnl-neutral';
 return '<strong class="pnl '+cls+'">'+(rounded>0?'+':'')+(percent?rounded.toFixed(3)+'%':rounded.toLocaleString()+'원')+'</strong>';
}
function executionAlignmentView(a,esc){
 var futuresNotice='<p class="ev"><b>선물 데이터 연결 · 추후 오픈 예정</b><br>현재 선물 시세·F-KOSPI200 직접 지수는 연결되지 않았습니다. 기존 ETF 모의운용은 계속되며, 선물 직접 매매 기능은 없습니다.</p>';
 if(!a)return futuresNotice+'<p>기존 KOSPI 신호 기반 운용 기록 · 새 ETF 운용 성과와 분리합니다.</p>';
 var pct=function(n){return typeof n==='number'?n.toFixed(3)+'%':'채점 대기';};
 var out=futuresNotice+'<div class="audit-note"><h3>단기 ETF 운용 · 별도 성과 장부</h3><p>'+esc(a.note)+'</p><p>기존 방식 체결 '+esc(a.legacy_trade_count)+'건 보존 · 새 방식 완료 '+esc(a.closed_cycles)+'회</p>';
 out+=a.closed_cycles?'<p>비용 차감 손익 '+paperProfit(a.net_pnl,false)+' · 회차 누적 '+paperProfit(a.net_return_pct,true)+' · 운용 중 관측 최대 낙폭 '+pct(a.observed_cycle_max_drawdown_pct)+'</p>':'<p>새 방식의 완료된 거래가 없어 수익률·적중률 평가를 시작하지 않았습니다.</p>';
 if(a.active)out+='<p>진입 '+esc(a.active.entry_at)+' · 보유 중 · 확정 성과 아님</p>';
 out+='<p>예측: 대상일 아침 실제 진입 → 15:20. 신규 진입은 09:00–09:15만 허용합니다. 손실 한도 조기 청산이나 지연 청산은 원래 시간대 예측의 적중률에 넣지 않습니다.</p></div>';
 (a.recent_cycles||[]).forEach(function(c){out+='<details><summary>'+esc(c.entry_at.slice(0,10))+' 운용 결과 · '+paperProfit(c.net_return_pct,true)+'</summary><p>실제 진입 '+esc(c.entry_at)+' → 실제 청산 '+esc(c.exit_at)+' · 수수료/세금 '+Number(c.costs).toLocaleString()+'원 (슬리피지는 손익에 반영)</p>'+(c.scores||[]).map(function(s){return '<p>'+esc(s.code)+' · 같은 보유 구간 가격 변화 '+pct(s.return_pct)+' · '+(s.correct===true?'방향 적중':s.correct===false?'방향 빗나감':'시간대 불일치/자료 부족 · 채점 제외')+'</p>';}).join('')+'</details>';});
 return out;
}
function etfPlanView(plan,esc){
 if(!plan||!plan.status)return '<p class="ev">이 보고서는 기존 KOSPI 시장 전망입니다. ETF 전용 판단은 기록되지 않았으며 신규 ETF 매매 근거로 재사용하지 않습니다.</p>';
 return '<section><h3>별도 단기 ETF 운용 판단</h3><p>'+esc(plan.note)+'</p><p>'+esc(plan.futures_note)+'</p><p>대상일 '+esc(plan.target_date)+' · 진입 '+esc(plan.entry_window)+' · 예정 청산 '+esc(plan.exit_time)+'</p>'+(plan.forecasts||[]).map(function(f){return '<p><b>'+esc(f.name)+'</b> · 실제 진입 이후 상승 확률 '+esc(f.up_probability)+'%</p><ul>'+(f.reasoning||[]).map(function(p){return '<li>'+esc(p)+'</li>';}).join('')+'</ul>';}).join('')+'<p>계획 상품 '+esc(plan.selected_code||'현금')+' · 단기 자금 비중 '+Number((plan.weight||0)*100)+'% · 체결 완료를 뜻하지 않습니다.</p></section>';
}

function reportVerdict(p){
  var d=p.direction,prob=p.up_probability,valid=typeof prob==='number'&&isFinite(prob)&&prob>=0&&prob<=100;
  if((d==='up'&&valid&&prob<50)||(d==='down'&&valid&&prob>50))return '판단 불일치 · 확인 필요';
  if(d==='down'||d==='하락')return '하락 예상';
  if(d==='up'||d==='상승')return '상승 예상';
  if(d==='flat'||d==='보합')return valid&&prob!==50?'보합권 · '+(prob>50?'상승':'하락')+' 쪽 우세':'보합 예상 · 뚜렷한 우세 없음';
  return '방향 판단 미기록';
}
function reportLibraryView(library,esc){
  var list=(library||{}).reports||[];
  var lines=function(rows){return '<ul>'+(rows||[]).map(function(x){return '<li>'+esc(x)+'</li>';}).join('')+'</ul>';};
  var roles={MarketAnalyst:'시장 분석가',MacroAnalyst:'거시경제 분석가',NewsSentimentAnalyst:'뉴스 분석가',TechnicalAnalyst:'기술 분석가',QuantAnalyst:'정량 분석가'};
  if(!list.length)return '<p class="report-empty">저장된 팀 보고서가 없습니다. 다음 분석·정기 갱신 후 자동으로 추가됩니다.</p>';
  return '<p class="report-intro">요약으로 결론을 먼저 확인하고, 본문 보기에서 판단 근거와 반대 의견을 읽어보세요. 최근 '+list.length+'건 표시 / 보관 '+esc(library.total)+'건 · 인쇄 메뉴에서 PDF로 저장할 수 있습니다.</p>'+list.map(function(r){
    var p=r.prediction||{},q=r.qa||{},long=r.team==='long',research=r.research||{};
    var dateText=String(r.at||'').replace('T',' ').slice(0,16);
    var verdict=long?(research.status!=='approved'?'신규 매수 보류':(research.allocations||[]).some(function(a){return a.weight>0;})?'조건부 매수 계획 승인':'신규 매수 없음 · 현금/기존 보유 유지'):reportVerdict(p);
    var reasons=long?[research.note||'검토 내용 미기록']:p.reasoning||[];
    var abstract='<p class="report-abstract-verdict">'+esc(verdict)+'</p>';
    if(!long)abstract+='<p class="report-probability">보조 지표 · AI 상승 확률 '+(typeof p.up_probability==='number'&&isFinite(p.up_probability)&&p.up_probability>=0&&p.up_probability<=100?esc(p.up_probability)+'%':'미기록')+' <span>(지수 등락률이 아닙니다)</span></p>';
    if(long)abstract+='<p class="report-coverage">조사 기업: '+esc((research.candidates||[]).map(function(c){return c.name;}).join(' · ')||'조사 대기')+'</p>';
    abstract+=lines(reasons.slice(0,2));
    var caution=long?'발행 당시 검토 결과입니다. 실제 가상 체결 여부는 모의투자 화면에서 확인하세요.':('위험 수준: '+((r.risk||{}).risk_level||'미기록')+' · 발행 당시 예측이며 실제 결과는 장 마감 후 검증합니다.');

    var body='<header class="report-heading"><span class="report-kicker">SIGNAL LAB / '+(long?'LONG HORIZON':'DAILY RESEARCH')+'</span><h2>'+esc(r.title)+'</h2><p>발행 '+esc(dateText)+' · 분석 대상일 '+esc(r.target_date)+'</p><p class="report-disclaimer">가상투자 연구 기록 · 작성 당시 판단이며 실제 주문 또는 수익 보장이 아닙니다.</p></header>';
    if(long){body+='<section class="report-executive"><h3>핵심 판단 요약</h3>'+abstract+'</section>'+longResearchView(Object.assign({},r.research,{archived:true}),esc);}
    else{
      body+='<section class="report-executive"><h3>01 · KOSPI 시장 전망 (매매 판단과 별도)</h3><p class="report-conclusion">'+esc(verdict)+'</p><p class="report-probability">AI 상승 확률 '+(typeof p.up_probability==='number'&&isFinite(p.up_probability)&&p.up_probability>=0&&p.up_probability<=100?esc(p.up_probability)+'%':'미기록')+' · 방향 판단을 보조하는 수치이며 지수 등락률이 아닙니다.</p><p>확신도 '+esc(p.confidence||'미기록')+'</p>'+lines(p.reasoning)+'</section>';
      body+='<section><h3>02 · 분석가별 근거</h3>'+(r.roles||[]).map(function(a){return '<div class="report-role"><h4>'+esc(roles[a.name]||a.name)+'</h4><p>'+esc(a.summary)+'</p>'+lines(a.points)+'<small>출처 검사: '+(a.citation_ok===true?'통과':a.citation_ok===false?'미통과 · 해석 주의':'별도 상태 미기록')+'</small></div>';}).join('')+'</section>';
      body+='<section><h3>03 · 상승·하락 토론과 중재</h3>'+(r.debate||[]).map(function(t){return '<div class="report-debate"><b>'+esc(t.round||'')+'차 · '+(t.stance==='bull'?'상승 측':t.stance==='bear'?'하락 측':esc(t.stance))+'</b><p>'+esc(t.argument)+'</p>'+lines(t.evidence)+'</div>';}).join('')+'<p><b>중재 판단</b> '+esc((r.moderation||{}).summary||'기록 없음')+'</p></section>';
      body+='<section><h3>04 · 위험과 검증</h3><p>'+esc((r.risk||{}).risk_level||'미기록')+' · '+esc((r.risk||{}).summary||'위험 요약 미기록')+'</p><p>최종 정합성 '+(q.consistent===true?'통과':q.consistent===false?'미통과':'미기록')+' · 위험 단계 '+(q.risk_passed===true?'통과':q.risk_passed===false?'미통과':'미기록')+'</p><p>검증 통과는 기록의 정합성 확인이며 미래 예측의 정확성을 보증하지 않습니다. 이후 뉴스·시세가 달라지면 판단을 다시 확인해야 합니다.</p></section>';
      body+='<section><h3>05 · 종목별 판단</h3>'+(r.stocks||[]).map(function(s){return '<p><b>'+esc(s.name)+' · '+esc(stockForecastLabel(s))+'</b><br>'+esc(s.reason)+'</p>';}).join('')+'</section>';
    }
    if(!long)body+=etfPlanView(r.execution,esc);
    var sources=long?(r.research||{}).sources:r.sources;
    body+='<section><h3>자료 출처</h3><p>기사 원문·제목은 재배포하지 않습니다. ID는 보고서 내 근거 표시에 대응합니다.</p><ul>'+(sources||[]).map(function(s){return '<li>['+esc(s.id)+'] '+esc(s.source)+' · '+esc(s.date||'시점 미기록')+'</li>';}).join('')+'</ul></section><footer class="report-footer">SIGNAL LAB · 보고서 '+esc(r.id)+'<br>생성 당시 기록 보관 · 현재 시세 및 사후 성과는 별도 화면에서 확인</footer>';
    return '<div class="research-document"><div class="report-preview"><div class="report-preview-meta"><span class="report-team">'+(long?'장기팀':'단기팀')+'</span><span class="report-date">'+esc(dateText)+'</span></div><h3>'+esc(r.title)+'</h3>'+abstract+'<p class="report-caution">'+esc(caution)+'</p></div><details class="report-detail"><summary><span class="report-open-label">본문 보기 <span aria-hidden="true">↗</span></span><span class="report-close-label">본문 접기 −</span><span class="report-reading">판단 근거 · 반대 의견 · 위험 · 출처</span></summary><article class="report-paper">'+body+'</article></details></div>';

  }).join('');
}

function longResearchView(r, esc) {
  if(!r)return '<p>장기팀 조사 대기</p>';
  var fmt=function(v){return typeof v==='number'&&isFinite(v)?v.toLocaleString(undefined,{maximumFractionDigits:2}):'자료 없음';};
  var status={ok:'수집 완료',insufficient:'자료 부족',unavailable:'수집 실패',partial:'일부 수집 실패',waiting:'대기',approved:'보고서 검토 완료',held:'매수 보류'};
  var out='<div class="audit-note"><b>장기팀 · 배정 800만원 · '+esc(status[r.status]||r.status)+'</b><p>'+esc(r.note)+'</p><p>보고서 '+esc(r.generated_at||'미생성')+' · 유효기한 '+esc(r.valid_until||'없음')+'</p><p>기업가치 분석 → 독립 반대 검토 → 장기 운용 담당. 승인 배분이 없으면 하루 최대 3회 모델 호출, 승인 배분이 있으면 7일 간격으로 재검토합니다. 뉴스는 매시간 수집합니다.</p></div>';
  if(r.review_issue)out+='<p class="ev">이번 조사 보류 사유: '+esc(r.review_issue)+' · 같은 날 모델 재호출은 하지 않습니다.</p>';
  if(!r.archived && r.watchlist){var w=r.watchlist;out+='<h3>관심 목록 · 실제 조사 대상과 구분</h3><p>'+esc(w.label)+' · 확보 '+esc(w.count)+'개 · '+esc(status[w.status]||w.status)+' · '+esc(w.checked_at||'갱신 대기')+'</p><p class="ev">'+esc(w.note)+' 단기팀 추천은 후보 선정의 참고이며 장기 매수 승인이 아닙니다. 업종당 최대 3개, 관심 목록 최대 15개를 유지합니다.</p><div class="watch-grid">'+(w.rows||[]).map(function(c){return '<div class="watch-item"><b>'+esc(c.name)+'</b><small>'+esc(({Technology:'정보기술',Industrials:'산업재','Consumer Cyclical':'경기소비재','Consumer Defensive':'필수소비재','Financial Services':'금융',Healthcare:'헬스케어','Basic Materials':'소재',Utilities:'유틸리티',Energy:'에너지','Communication Services':'통신·미디어','Real Estate':'부동산',Unknown:'업종 미확인'})[c.sector]||c.sector)+' · '+esc(({research:'심층 조사 후보',watch:'관찰',needs_data:'추가 자료 필요'})[c.status]||c.status)+'</small><small>'+esc((c.reasons||[]).join(' '))+'</small><small>PER '+fmt((c.metrics||{}).trailingPE)+' / PBR '+fmt((c.metrics||{}).priceToBook)+' · 결산기 '+esc(c.period_end||'미확인')+'</small><small>'+esc(c.source||'자료 대기')+'</small></div>';}).join('')+'</div><p>최근 주간 조사 묶음: '+esc((w.batch||[]).join(', ')||'기존 보고서 유지 · 다음 주간 조사 대기')+'</p>';}
  out+='<h3>하락해도 가치가 유지되는지 확인할 조사 대상</h3><p class="ev">아래 종목은 해당 보고서에서 실제로 조사한 대상입니다. 관심 목록 전체가 심층 분석된 것은 아닙니다. 가격 하락만으로 매수하지 않습니다. 최근 5개 분기의 실적·현금흐름, 부채, PER·PBR을 검사하며 자료가 빠지면 보류합니다. Yahoo 집계자료이며 공시 원문 대조를 완료했다는 뜻은 아닙니다.</p>';
  out+='<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px">'+(r.candidates||[]).map(function(c,i){var m=c.metrics||{};return '<div style="border:1px solid var(--line);border-radius:12px;padding:16px"><h3>'+esc(c.name)+' [M'+(i+1)+']</h3><p><b>'+esc(({data_wait:'자료 확인 대기',sector_review:'업종별 검증 대기',fundamental_fail:'장기 투자 근거 미충족',valuation_wait:'가치평가 기준 대기',report_wait:'보고서 승인 대기',thesis_wait:'종목별 매수 배분 없음',news_wait:'관련 뉴스 재검토 대기',price_wait:'매수가격 대기',eligible:'조건 충족'})[(c.gate||{}).phase]||'검토 대기')+'</b> · '+esc((c.gate||{}).reason||'상태 확인 중')+'</p><p>결산기 '+esc(c.period_end||'확인 불가')+' · '+esc(c.source)+'</p><p>PER '+fmt(m.pe)+' / PBR '+fmt(m.pb)+' / 부채·자본 '+fmt(m.debt_equity)+'</p><p>매출 전년동기 대비 '+fmt(m.revenue_yoy_pct)+'% · 최근 4분기 순이익 '+fmt(m.ttm_income)+'원 · 잉여현금흐름 '+fmt(m.ttm_fcf)+'원</p><p class="ev">'+esc((c.missing||[]).join(', ')||'자료 누락 기록 없음')+'</p><details><summary>매수 기준과 현재 값</summary><ul>'+((c.gate||{}).criteria||[]).map(function(x){return '<li>'+esc(x.label)+' · '+fmt(x.value)+' / 기준 '+esc(x.required)+' · '+esc(({pass:'통과',fail:'미충족',unknown:'자료 없음'})[x.result]||'확인 대기')+'</li>';}).join('')+'</ul><p>매수 기준가 '+fmt((c.gate||{}).buy_below)+'원 · '+esc((c.gate||{}).next_review||'재검토 일정 확인 중')+'</p></details></div>';}).join('')+'</div>';
  if((r.deferred||[]).length)out+='<p>자료 부족으로 이번 심층 판단에서 보류: '+r.deferred.map(function(f){return esc(f.name)+' ('+esc((f.missing||[]).join(', '))+')';}).join(' / ')+'</p>';
  (r.roles||[]).forEach(function(role){out+='<h3>'+esc(role.name)+'</h3><p class="ev">모델 해석과 검토 의견입니다. 뉴스 원문의 사실 확인을 완료했다는 뜻은 아닙니다.</p>';(role.stocks||[]).forEach(function(s){out+='<p><b>'+esc(s.name)+' · '+esc(({consider:'검토 가능',watch:'관찰',reject:'반대'})[s.stance]||s.stance)+'</b></p><ul>'+s.points.map(function(p){return '<li>'+esc(p)+'</li>';}).join('')+'</ul><p>투자 근거 재검토 조건</p><ul>'+s.invalidation.map(function(p){return '<li>'+esc(p)+'</li>';}).join('')+'</ul>';});out+=(role.summary||[]).map(function(s){return '<p>'+esc(s)+'</p>';}).join('');});
  out+='<h3>장기 운용 계획</h3>'+(r.allocations||[]).map(function(a){return '<p>'+esc(a.code)+' · 목표 '+fmt(a.weight*100)+'% · 매수 기준 '+fmt(a.buy_below)+'원 이하</p>';}).join('');
  if(!(r.allocations||[]).length)out+='<p>승인된 종목 배분 없음 · 기존 현금/보유 유지. 자료 부족은 기업가치 부적격 판정이 아닙니다.</p>';
  out+='<p class="ev">종목당 장기 자금 25%, 같은 업종 30%, 전체 주식 50% 상한. 금융업은 추가 건전성 검증 전 자동 매수에서 제외합니다. 하락 때 자동 물타기하지 않으며 매월 1회 가격 조건을 확인합니다. 계획 만료·뉴스 장애 시 전체 신규 매수를 막고, 종목별 중요 사건은 관련 종목만 보류합니다. 승인 종목의 가격 조건은 종목별로 확인합니다.</p>';
  if(r.archived)return out;
  out+='<h3>미국 뉴스 · 장 마감 후에도 감시</h3><p>수집 상태 '+esc(status[r.news_status]||r.news_status)+' · 마지막 시도 '+esc(r.news_checked_at||'없음')+' · 관련 보도 '+fmt(r.news_count)+'건</p><p>'+esc((r.block_reasons||[]).join(' / ')||'수집된 뉴스 기준 추가 차단 없음')+'</p><p class="ev">미국판 영문 뉴스이며 모든 발행사가 미국 소재인 것은 아닙니다. 직접 관련·고객사/경쟁사 간접 관련·거시 영향을 구분합니다. 제목 기반 위험 표시는 확인 요청이며 사실 확정이나 매매 지시가 아닙니다. 기사 원문은 재배포하지 않습니다.</p>';
  out+='<div class="tbl" style="overflow-x:auto"><table><thead><tr><th>발행시각</th><th>출처</th><th>연결 종목</th><th>검토 상태</th></tr></thead><tbody>'+(r.events||[]).map(function(n){var url=/^https:\/\//.test(n.url||'')?n.url:'';return '<tr><td>'+esc(n.evidence_id||'신규')+' · '+esc(n.published_at)+'</td><td>'+(url?'<a href="'+esc(url)+'" target="_blank" rel="noopener noreferrer">'+esc(n.source)+'</a>':esc(n.source))+'</td><td>'+(n.relations||[]).map(function(x){return esc(x.name)+' ('+esc(({direct:'직접',indirect:'간접',macro:'거시'})[x.kind]||x.kind)+(x.held?'·보유':'·조사')+')';}).join('<br>')+'</td><td>'+(n.risk_flag?'위험 단어 감지 · ':'')+(n.reviewed?'팀 검토 자료에 포함':'팀 재검토 대기')+'</td></tr>';}).join('')+'</tbody></table></div>';
  return '<div class="long-research">'+out+'</div>';
}

function splitPaperView(s, esc) {
  var won=function(v){return typeof v==='number'&&isFinite(v)?v.toLocaleString(undefined,{maximumFractionDigits:0})+'원':'미확인';};
  var audit=s.valuation_audit||{};
  var now=new Date(),stamp=s.last_tick?new Date(s.last_tick):null,parts={};
  new Intl.DateTimeFormat('en-US',{timeZone:'Asia/Seoul',weekday:'short',hour:'2-digit',minute:'2-digit',hourCycle:'h23'}).formatToParts(now).forEach(function(x){parts[x.type]=x.value;});
  var minute=Number(parts.hour)*60+Number(parts.minute);
  if(audit.status==='ok'&&stamp&&!isNaN(stamp.getTime())&&['Mon','Tue','Wed','Thu','Fri'].includes(parts.weekday)&&minute>=540&&minute<930&&(now-stamp)>600000){
    audit={status:'stale',note:'장중 시세 평가가 10분 이상 지연 · 수익률 표시 보류'};
  }
  var metric=function(v){return audit.status==='stale'||audit.status==='mismatch'?null:v;};
  var realized=function(v){return audit.status==='mismatch'?null:v;};
  var total='<div class="audit-note paper-total"><b>가상자본금 '+won(s.initial_cash)+' · 실제 자금 거래 없음</b><br>평가금액 '+(audit.status==='mismatch'?'미확인':won(s.equity))+' · 오늘 손익 '+paperProfit(metric(s.day_pnl),false)+' ('+paperProfit(metric(s.day_return_pct),true)+')<br>누적 손익 '+paperProfit(metric(s.pnl),false)+' ('+paperProfit(metric(s.total_return_pct),true)+') · 현금 '+won(s.cash)+'<br>실현손익 '+paperProfit(realized(s.realized_pnl),false)+' / 평가손익 '+paperProfit(metric(s.unrealized_pnl),false)+' · 거래비용 '+won(s.fees)+'<br>평가 시각 '+esc(s.last_tick||'시세 수집 대기')+' · '+esc(audit.note||'원장 검증 대기')+'</div>';
  var books='<div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(240px,1fr));gap:14px">'+(s.sleeves||[]).map(function(b){return '<div class="paper-sleeve '+(b.key==='long'?'paper-sleeve-long':'')+'"><h3 style="margin:0 0 12px">'+esc(b.label)+' · 배정 '+won(b.initial_cash)+'</h3><p>평가 '+won(b.equity)+' / 현금 '+won(b.cash)+'</p><p>실현 '+paperProfit(realized(b.realized_pnl),false)+' · 평가 '+paperProfit(metric(b.unrealized_pnl),false)+'</p><p>'+esc(b.status)+'</p><p>'+((b.holdings||[]).map(function(h){return esc(h.name)+' '+h.qty+'주 · 평균 매입원가 '+won(h.average_cost);}).join('<br>')||'보유 종목 없음')+'</p></div>';}).join('')+'</div>';
  var trades='<h3>최근 가상 체결</h3><div class="tbl" style="overflow-x:auto"><table class="stat-table"><thead><tr><th>시각</th><th>계좌</th><th>종목</th><th>매매</th><th>수량</th><th>체결가</th><th>거래비용</th></tr></thead><tbody>'+(s.recent_trades||[]).map(function(t){return '<tr><td>'+esc(t.at)+'</td><td>'+(t.sleeve==='long'?'장기':'단기')+'</td><td>'+esc(t.name)+'</td><td>'+(t.side==='buy'?'매수':'매도')+'</td><td>'+t.qty+'</td><td>'+Number(t.price).toLocaleString(undefined,{maximumFractionDigits:3})+'원'+'</td><td>'+won(t.fee)+'</td></tr>';}).join('')+'</tbody></table></div>';
  return executionAlignmentView(s.execution_alignment,esc)+total+'<p class="ev">전체 투자 노출 '+won(s.gross_exposure)+' · 상승/하락 상쇄 후 방향 노출 '+won(s.net_direction_exposure)+(s.hedged?' · 장기 상승 자산과 단기 인버스가 일부 상쇄됩니다.':'')+'</p>'+books+((s.recent_trades||[]).length?trades:'<p>아직 체결 없음 · 유효한 판단과 당일 시세를 기다립니다.</p>')+'<p class="ev">장기: 독립 장기팀의 종목 계획으로 월 1회 조정, 미승인·만료·뉴스 재검토 시 추가 매수 보류. 단기: KOSPI200·ETF 별도 판단, 종목별 근거 검증 후 최대 25%. 아침 진입/15:20 청산, 자료 장애 시 실제 체결은 지연될 수 있습니다. 단기 일손실 2% 현금화, 최고 평가액 대비 장기 15%/단기 10% 손실 시 중지. 장중 5분 점검이므로 손실 한도를 넘겨 체결될 수 있습니다. 관측 시세에 편도 거래비용 0.015%·슬리피지 0.05%를 가정하며 실제 호가·체결을 재현하지 않습니다. 개별주식 매도에는 세금 0.2%를 추가로 가정하며 실제 적용 세율과 다를 수 있습니다. 현금 이자·배당·ETF 분배금은 미반영입니다.</p>';
}

  if(typeof document.createElement==='function')mountCompanySearch(D.company_research||{},esc,null);
  if($('reportLibrary')) $('reportLibrary').innerHTML=reportLibraryView(D.research_reports,esc);
  if($('longResearch')) $('longResearch').innerHTML=longResearchView(D.long_research,esc);
  var PA=D.paper||null;
  function won(v){return Number(v).toLocaleString(undefined,{maximumFractionDigits:0})+'원';}
  if(PA&&PA.portfolio_mode){$('paper').innerHTML=splitPaperView(PA,esc);} else $('paper').innerHTML = PA ? (PA.days===0?'<div class="ev">운용 시작 전 · 수익률 0%는 초기 상태입니다. 아직 모의 체결·평가 기록이 없습니다.</div>':'')+'<div class="kpis" style="grid-template-columns:repeat(auto-fit,minmax(150px,1fr))">'+
    [['평가금액',won(PA.equity),pct(PA.total_return_pct)+' ('+(PA.pnl>=0?'+':'')+won(PA.pnl)+')',PA.total_return_pct>0?'up':PA.total_return_pct<0?'down':''],['시작 자금',won(PA.initial_cash),PA.days+'일 운용',''],['샤프 / MDD',(PA.sharpe_annual==null?'—':PA.sharpe_annual)+' / '+(PA.max_drawdown_pct==null?'—':PA.max_drawdown_pct+'%'),'승률 '+(PA.win_rate==null?'—':Math.round(PA.win_rate*100)+'%'),''],['거래',PA.trades+'건','비용 '+won(PA.fees),'']]
    .map(function(t){return '<div class="kpi"><div class="l">'+t[0]+'</div><div class="v mono '+t[3]+'" style="font-size:22px">'+t[1]+'</div><div class="d">'+t[2]+'</div></div>';}).join('')+'</div>'+
    curve([{n:'가상 계좌',v:(PA.equity_curve||[]).map(function(x){return (x.equity/PA.initial_cash-1)*100;}),c:'var(--accent)'}])+
    '<div class="tbl"><table><thead><tr><th>거래일</th><th class="num">목표</th><th class="num">평가금액</th><th class="num">일수익</th><th class="num">누적</th></tr></thead><tbody>'+
    (PA.recent_days||[]).slice(0,20).map(function(d){return '<tr><td>'+esc(d.date)+'</td><td class="num">'+(d.target_position>0?'롱':d.target_position<0?'쇼트':'현금')+' '+Math.round(Math.abs(d.target_position)*100)+'%</td><td class="num">'+won(d.equity)+'</td><td class="num '+(d.day_return_pct>=0?'up':'down')+'">'+pct(d.day_return_pct)+'</td><td class="num">'+pct(d.total_return_pct)+'</td></tr>';}).join('')+'</tbody></table></div>'+
    '<div class="ev">현금 '+won(PA.cash||0)+' · 보유 '+(Object.keys(PA.holdings||{}).length?Object.keys(PA.holdings).map(function(t){return (t.indexOf('069500')===0?'KODEX 200':'KODEX 인버스')+' '+PA.holdings[t]+'주';}).join(', '):'없음')+'</div>'+
    ((PA.recent_trades||[]).length?'<div class="label">최근 체결</div><div class="tbl"><table><thead><tr><th>일자</th><th>종목</th><th class="num">매매</th><th class="num">수량</th><th class="num">체결가</th><th class="num">수수료</th></tr></thead><tbody>'+
      PA.recent_trades.map(function(t){return '<tr><td>'+esc(t.date)+'</td><td>'+(t.ticker.indexOf('069500')===0?'KODEX 200':'KODEX 인버스')+'</td><td class="num '+(t.side==='buy'?'up':'down')+'">'+(t.side==='buy'?'매수':'매도')+'</td><td class="num">'+t.qty+'</td><td class="num">'+Number(t.price).toLocaleString()+'</td><td class="num">'+won(t.fee)+'</td></tr>';}).join('')+'</tbody></table></div>':'')+
    '<div class="ev">가상 체결입니다. 아침 분석의 포지션 비중대로 ETF를 사고팔아(분석이 08:40 전에 끝나면 장전 시간외 전일 종가, 아니면 시가 체결) 종가로 평가하며, 다음 날 다시 조정합니다. 실제 돈은 들어가지 않습니다.</div>'
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
  var audit=D.review_history||{}, obs=audit.observations||[], archived=audit.archived||[];
  var failed=obs.filter(function(r){return r.correct===false;}).length;
  if(obs.length||archived.length||audit.unavailable){
    $('reviewNotice').hidden=false;
    $('reviewNotice').innerHTML='정식 적중률은 채점 완료 기록만 집계합니다. 관찰 복기 '+obs.length+'건 (빗나감 '+failed+'건), 별도 보관 예측 '+archived.length+'건은 별도 표시합니다. <a href="#records">기록과 제외 사유 보기 →</a>'+(audit.unavailable?' · 일부 보관 기록을 읽지 못했습니다.':'');
  }
  $('reviewHistory').innerHTML=obs.map(function(r){return '<div class="audit-item"><b>'+esc(r.target_date)+' 관찰 결과 · '+(r.correct===false?'빗나감':r.correct===true?'일치':'미확인')+'</b><br/>예상 '+dir(r.direction)+' → 실제 '+dir(r.actual_direction)+'<br/><span class="ev">원래 예측 대상 '+esc(r.reviewed_record||'미기록')+' · 정식 채점 제외<br/>'+esc(r.note||'')+'</span></div>';}).join('')+
    archived.map(function(r){return '<div class="audit-item"><b>별도 보관 · '+esc(r.run_at||r.run_date)+'</b><br/>대상 '+esc(r.target_date)+' · 예상 '+dir(r.direction)+'<br/><span class="ev">대체 기록: '+esc(r.superseded_by||'미기록')+' · 정식 통계 제외</span></div>';}).join('')+(audit.unavailable?'<div class="empty">일부 보관 기록을 읽지 못했습니다.</div>':(!obs.length&&!archived.length?'<div class="empty">별도 기록 없음</div>':''));
  var recs=D.records||[];
  $('records').innerHTML=recs.length?'<div class="tbl"><table><thead><tr><th>대상 거래일</th><th>예측</th><th>신뢰도</th><th>실제</th><th>결과</th></tr></thead><tbody>'+
    recs.map(function(x){return '<tr><td class="mono">'+esc(x.actual_session_date||x.target_date)+'</td><td>'+dir(x.direction)+(x.up_probability!=null?' <span class="ev">'+Math.round(x.up_probability)+'%</span>':'')+'</td><td>'+esc(x.confidence||'-')+'</td><td>'+(x.verified?dir(x.actual_direction):'<span class="empty">대기</span>')+'</td><td>'+(x.verified?(x.correct?'<span class="chip ok">적중</span>':'<span class="chip bad">빗나감</span>'):'—')+'</td></tr>';}).join('')+
    '</tbody></table></div>':'<div class="empty">기록 없음</div>';
})();


/* (V3.29) 토스 스타일 정리: 이미 그려진 화면에서 핵심만 남기고 부가 정보는 '자세히'로 접는다.
   렌더 코드를 건드리지 않는 후처리라 다른 기능과 충돌이 적다. DOM 이 없는 검사 환경에서는 아무것도 안 한다. */
(function(){
  if(typeof document==='undefined'||typeof document.querySelector!=='function'||typeof document.createElement!=='function')return;
  function q(sel,root){return (root||document).querySelector(sel);}
  function qa(sel,root){return Array.prototype.slice.call((root||document).querySelectorAll(sel));}
  function fold(nodes,label,host,before){
    nodes=nodes.filter(function(n){return n&&n.parentNode;});
    if(!nodes.length)return null;
    var d=document.createElement('details');d.className='more';
    var s=document.createElement('summary');s.textContent=label;d.appendChild(s);
    if(host){if(before&&before.parentNode===host)host.insertBefore(d,before);else host.appendChild(d);}
    else nodes[0].parentNode.insertBefore(d,nodes[0]);
    nodes.forEach(function(n){d.appendChild(n);});
    return d;
  }
  var LEAD={'강한 상승':'AI는 오늘 코스피가 크게 오를 것 같다고 봤어요','상승':'AI는 오늘 코스피가 오를 것 같다고 봤어요',
    '약한 상승':'AI는 오늘 코스피가 조금 오를 것 같다고 봤어요','보합':'AI는 오늘 코스피가 크게 움직이지 않을 것 같다고 봤어요',
    '약한 하락':'AI는 오늘 코스피가 조금 내릴 것 같다고 봤어요','하락':'AI는 오늘 코스피가 내릴 것 같다고 봤어요',
    '강한 하락':'AI는 오늘 코스피가 크게 내릴 것 같다고 봤어요'};
  try{
    // 1) 예측 카드
    var pred=q('#pred');
    if(pred&&q('.why-dir',pred)){
      var hd=q('.why-dir',pred),lab=(hd.textContent||'').replace(/[▲▼]/g,'').trim();
      var lead=document.createElement('div');lead.className='toss-lead';
      lead.textContent=LEAD[lab]||('AI 판단: '+lab);
      hd.parentNode.insertBefore(lead,hd.nextSibling);
      var kids=Array.prototype.slice.call(pred.children);
      var extra=kids.filter(function(el){
        if(el===hd||el===lead)return false;
        if(el.classList&&el.classList.contains('evidence'))return false;
        if(el.classList&&el.classList.contains('votes'))return false;
        if(el.classList&&el.classList.contains('label'))return false;
        if(q('.gauge',el))return false;
        if(el.querySelector&&el.querySelector('.chip'))return false;
        if(el.querySelector&&el.querySelector('a[href="#process"]'))return false;
        return true;
      });
      var link=q('a[href="#process"]',pred);
      fold(extra,'자세히 보기 · 투자 비중, 확률 보정, 시세 비교',pred,link&&link.parentNode&&link.parentNode.parentNode===pred?link.parentNode:null);
    }
    // 2) 차트 카드: 시세 안내·AI 기록 설명·기술 지표는 접기
    var notice=q('#quoteNotice');
    var ai=q('#aiTrack'),inds=q('#inds');
    if(ai)qa('.ev',ai).forEach(function(n){n.classList.add('sub-note');});
    fold([notice],'시세 기준 안내',notice&&notice.parentNode);
    if(inds&&inds.children.length)fold([inds],'기술 지표 보기 · RSI, MACD, 이동평균');
    // 3) 검증·QA 섹션 전체를 접기
    var qaBox=q('#qa');
    if(qaBox&&qaBox.parentNode){var h=qaBox.parentNode.querySelector('h2');if(h)h.style.display='none';fold([qaBox],'검증 · QA 과정 보기');}
    // 4) 시장 현황판: 수집 시각 같은 메타 문구는 접기
    var board=q('#board');
    if(board){var metas=qa('.ev',board);if(metas.length)fold(metas,'데이터 기준 시각',board);}
    // 5) ISO 시각(2026-09-29T15:46:13+09:00)을 읽기 쉬운 형태(9월 29일 15:46)로
    var re=/(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})(?::\d{2}(?:\.\d+)?)?(?:[+-]\d{2}:?\d{2}|Z)?/g;
    var walker=document.createTreeWalker?document.createTreeWalker(document.body,4,null):null;
    var nodes=[];if(walker){while(walker.nextNode())nodes.push(walker.currentNode);}
    nodes.forEach(function(t){if(t.parentNode&&t.parentNode.tagName!=='SCRIPT'&&re.test(t.nodeValue)){re.lastIndex=0;
      t.nodeValue=t.nodeValue.replace(re,function(_,y,m,d,hh,mm){return Number(m)+'월 '+Number(d)+'일 '+hh+':'+mm;});}re.lastIndex=0;});
  }catch(e){/* 정리 실패 시 원래 화면 그대로 둔다 */}
})();
"""

_BODY = """<title>시그널랩 | Signal Lab</title>
<style>{css}</style>
<header class="topbar">
  <div class="topbar-inner">
    <a class="brand" id="brandHome" href="#today" aria-label="시그널랩 — 첫 화면으로"><span class="logo">SL</span><span><b>시그널랩 <span style="font-size:12px;font-weight:500;color:var(--muted)">Signal Lab</span></b><small>대상 거래일 <span class="mono" id="tgt"></span> · 분석 <span class="mono" id="asof"></span></small></span></a>
    <nav class="tabs" id="tabs">
      <a href="#today" data-tab="today" class="active"><i>◎</i><span class="l">오늘의 예측</span><span class="s">예측</span></a>
      <a href="#stocks" data-tab="stocks"><i>▦</i><span class="l">추천 분석 종목</span><span class="s">종목</span></a>
      <a href="#process" data-tab="process"><i>⋯</i><span class="l">분석 과정</span><span class="s">과정</span></a>
      <a href="#performance" data-tab="performance"><i>↗</i><span class="l">성과·수익률</span><span class="s">성과</span></a>
      <a href="#reports" data-tab="reports"><i>▤</i><span class="l">팀 보고서</span><span class="s">보고서</span></a>
      <a href="#long" data-tab="long"><i>長</i><span class="l">장기 투자</span><span class="s">장기</span></a>
      <a href="#paper" data-tab="paper"><i>₩</i><span class="l">모의투자 수익률</span><span class="s">모의투자</span></a>
      <a href="#company" data-tab="company" class="company-search-link"><i>⌕</i><span class="l">기업 심층분석 검색</span><span class="s">검색</span></a>
      <a href="#records" data-tab="records"><i>≡</i><span class="l">예측 기록</span><span class="s">기록</span></a>
    </nav>
    <span class="mono clk" id="clock">--:--:-- KST</span>
    <span class="ro"><b>읽기 전용 · 모의투자</b> 실제 자금 거래 없음</span>
    <button class="btn" id="btnTheme">☀ 밝게</button>
  </div>
</header>
<div class="wrap">
  <div class="stale" id="stale" hidden></div>
  <div class="panel active" id="tab-today">
    <div class="hero hero-3">
      <div style="display:grid;gap:20px;min-width:0">
      <section class="hero-card" style="gap:12px"><h2>오늘의 KOSPI 예측</h2><div id="pred" style="display:grid;gap:12px"></div></section>
      <section class="hero-card" style="gap:10px"><div class="chart-head"><span class="lbl">KOSPI</span><span class="px mono" id="kPx">—</span><span class="mono" id="kChg"></span><span class="lbl" id="kDate"></span><span class="aitag" id="kAi"></span></div>
        <div class="ev" id="quoteNotice">게시 시점의 종가 스냅샷입니다. 공유 주소는 5분마다 게시본을 확인하며, 실시간 시세 조회는 하지 않습니다.</div>
        <div id="kChart"><div class="empty">이 스냅샷에는 KOSPI 시세가 없습니다 (다음 분석부터 포함).</div></div>
        <div class="legend"><span><i style="background:var(--chart-price)"></i><span id="priceSeriesLabel">종가</span></span><span><i class="sw20"></i>20일선</span><span><i class="sw60"></i>60일선</span><span style="color:var(--ink-2)">AI ▲/▼ = AI 예측 방향</span></div>
        <div id="aiTrack"></div>
        <div class="inds" id="inds"></div></section>
      </div>
      <section class="board" id="board"></section>
    </div>
    <div class="audit-note" id="reviewNotice" hidden></div>
    <div class="kpis" id="kpis" style="grid-template-columns:repeat(4,1fr)"></div>
    <section><h2>검증 · QA</h2><div id="qa"></div></section>
  </div>
  <div class="panel" id="tab-stocks">
    <section><h2>추천 종목 <span class="h2-sub">삼성전자 · SK하이닉스</span></h2><p class="ev">AI 분석 대상으로 선정된 종목입니다. 상승·하락·보합 예측을 모두 포함하며, 매수 지시가 아닙니다. 현재 등락은 전일 종가 대비이며 장중 비교는 확정 적중률에 반영하지 않습니다.</p><div class="stocks" id="core"></div>
      <h2 style="margin-top:20px">추천 분석 종목 <span class="h2-sub">종목을 눌러 주가 흐름과 판단 근거 보기</span></h2><div class="stocks" id="watch"></div></section>
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
  <div class="panel" id="tab-performance"><section><h2>시장 전망 평가 · KOSPI 기준</h2><p>이 화면의 기존 신호 가정성과는 ETF 하루 수익률 또는 지수 근사치로 계산한 사후 값이며, 실제 ETF 모의계좌 수익이 아닙니다. 실제 체결 이후 비용 차감 손익과 ETF 운용 성과는 모의투자 수익률에서 별도로 확인하세요. 과거 KOSPI 채점 기록은 그대로 보존합니다.</p></section>
    <section><h2>과거 전략 성과 <span class="h2-sub">채점 완료 기록 · 누적 · 비용 차감</span></h2><div id="ret"></div></section>
    <div class="grid">
      <section><h2>AI vs 단순 규칙</h2><div id="base"></div></section>
      <section><h2>에이전트별 성과</h2><div id="agents"></div></section>
    </div>
  </div>
  <div class="panel" id="tab-reports"><section><h2>투자팀 리서치 라이브러리</h2><div id="reportLibrary"></div></section></div>
  <div class="panel" id="tab-long"><section><h2>장기 투자팀 보고서</h2><div id="longResearch" style="display:grid;gap:12px"></div></section></div>
  <div class="panel" id="tab-paper">
    <section><h2>모의투자 수익률 <span class="h2-sub">가상자본금 2,000만원 · 장기 800만원 / 단기·현금 1,200만원 · 조회 시점 가격으로 모의 체결</span></h2><div id="paper" style="display:grid;gap:14px"></div></section>
  </div>
  <div class="panel" id="tab-company"><section><h2>기업 심층분석</h2><p class="ev">기업명 또는 종목코드로 검색하세요. 현재 관리 중인 코스피 시가총액 상위 100개 기업을 지원합니다.</p><label class="company-search-box"><span aria-hidden="true">⌕</span><input id="companyQuery" type="search" maxlength="80" placeholder="기업명 또는 종목코드 검색" aria-label="기업명 또는 종목코드 검색"></label><div id="companyResults" class="company-results"></div><div id="companyBody"><p>기업을 선택하면 저장된 심층분석을 볼 수 있습니다.</p></div></section></div>
  <div class="panel" id="tab-records">
    <section><h2>정식 예측 기록</h2><div id="records"></div></section>
    <section><h2>관찰 복기 · 별도 보관 예측</h2><p class="ev">관찰일과 원래 예측 대상일이 다른 기록은 정식 적중률·가상계좌 수익에 합산하지 않습니다.</p><div id="reviewHistory"></div></section>
  </div>
  <footer>
    <div>투자 자문이 아닙니다. 모든 내용은 참고용 정보이며, 방향성 신호는 매수·매도 추천이 아닙니다.</div>
    <div>뉴스 원문은 이 공유본에 포함되지 않습니다(건수·AI 요약만). 시장 데이터 출처: Yahoo Finance, Google 뉴스 RSS, 네이버 증권(집계 수치만).</div>
    <div>스냅샷 생성 <span class="mono" id="gen"></span> (<span id="genAgo"></span>) · 시그널랩 | Signal Lab</div>
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
  document.getElementById('brandHome').addEventListener('click',function(e){e.preventDefault();location.hash='today';go('today');window.scrollTo({top:0,left:0,behavior:'instant'});});
  var tbs=document.querySelectorAll('.tbl table');
  for(var ti=0;ti<tbs.length;ti++){var hs=[],ths=tbs[ti].querySelectorAll('thead th');for(var hi=0;hi<ths.length;hi++)hs.push(ths[hi].textContent.trim());
    var trs=tbs[ti].querySelectorAll('tbody tr');for(var ri=0;ri<trs.length;ri++){var tds=trs[ri].children;for(var ci=0;ci<tds.length;ci++){if(hs[ci]&&!tds[ci].hasAttribute('colspan'))tds[ci].setAttribute('data-label',hs[ci]);}}}
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
