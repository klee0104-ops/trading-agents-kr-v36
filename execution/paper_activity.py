"""Explain paper-trading inactivity without changing any order or risk gate."""
from __future__ import annotations

from datetime import datetime, time
import math

from data.sessions import market_status


def summarize(outcome: dict, paper: dict | None, long_report: dict | None, now: datetime) -> dict:
    """Return an allowlisted, deterministic decision summary for both dashboards."""
    market = market_status(now)
    paper = paper if isinstance(paper, dict) else {}
    outcome = outcome if isinstance(outcome, dict) else {}
    long_report = long_report if isinstance(long_report, dict) else {}
    forecast = (outcome.get("final_result") or {}).get("etf_execution") or {}
    forecast = forecast if isinstance(forecast, dict) else {}
    qa = (outcome.get("final_qa") or {}).get("consistent") is True
    verification = (outcome.get("verification") or {}).get("verification_passed") is not False
    independent_short = forecast.get("schema") in (3, 4)
    neutral_probe = forecast.get("selection_basis") == "neutral_probe" and forecast.get("status") == "approved"
    etf_qa = forecast.get("etf_qa") or {}
    short_qa = etf_qa.get("passed") is True
    same_day = outcome.get("target_date") == str(now.date())
    sleeves = {b.get("key"): b for b in paper.get("sleeves", []) if isinstance(b, dict)}
    short_book, long_book = sleeves.get("short", {}), sleeves.get("long", {})
    short_status = str(short_book.get("status") or "상태 기록 없음")[:120]
    long_status = str(long_book.get("status") or "상태 기록 없음")[:120]
    rows = forecast.get("forecasts") or []
    best = max((r.get("up_probability") for r in rows
                if isinstance(r, dict) and type(r.get("up_probability")) in (int, float)
                and math.isfinite(r["up_probability"]) and 0 <= r["up_probability"] <= 100
                and r.get("code") in ("069500", "114800")), default=None)
    alignment = paper.get("execution_alignment") or {}
    active = bool(alignment.get("active"))
    active_basis = (alignment.get("active") or {}).get("selection_basis")
    intraday_active = active_basis in ("intraday_ai", "intraday_ai_exploratory")
    evidence_ids = {s.get("id") for s in outcome.get("source_registry") or [] if isinstance(s, dict)}
    from data.etf_forecast import QA_REASONS, INSTRUMENTS
    reason_code = etf_qa.get("reason_code")
    if same_day and independent_short and not short_qa:
        if reason_code in QA_REASONS:
            etf_qa_reason = QA_REASONS[reason_code]
            if etf_qa.get("instrument") in INSTRUMENTS:
                etf_qa_reason += " · 대상 " + etf_qa["instrument"]
            if etf_qa.get("evidence_reason_code") in {"history_incomplete", "quote_invalid", "history_stale", "session_mismatch", "source_fetch_failed"}:
                etf_qa_reason += " · 자료 상태 " + etf_qa["evidence_reason_code"]
        elif {"M901", "M902", "M903"} <= evidence_ids:
            etf_qa_reason = "당시 세부 탈락 항목 미기록 · ETF 3종 과거시세 근거는 수집됨. 모델 판단 누락·형식·인용·양방향 충돌 중 어느 항목인지 확인 불가"
        else:
            etf_qa_reason = "당시 세부 탈락 항목 미기록 · 자료 수집과 모델 판단 중 어느 단계에서 보류됐는지 확인 불가"
    else:
        etf_qa_reason = None

    if active_basis == "intraday_ai_exploratory":
        short_reason = "트레이더 근거 확인 후 단기자금 5% 가상 탐색 보유 중 · 실제 손익 기록/공식 AI 적중률 제외"
    elif intraday_active:
        short_reason = "장중 AI 재판단 ETF 보유 중 · 전용 QA 통과 · 손익은 별도 회차 기록"
    elif not same_day:
        short_reason = "오늘자 분석 대기 · ETF 신규 매매 보류"
    elif not independent_short and (not qa or not verification):
        short_reason = "최종 QA·근거 검증 미통과 · ETF 신규 매매 보류"
    elif independent_short and not short_qa:
        short_reason = "ETF 전용 근거·자료 검증 미통과 · 신규 매매 보류"
    elif forecast.get("status") != "approved":
        short_reason = "ETF 전용 계획 미승인 · 신규 매매 보류"
    elif not forecast.get("selected_code") or not forecast.get("weight"):
        short_reason = "ETF 계획상 현금 유지 · 신규 매수 없음"
    elif active:
        short_reason = "ETF 보유 중 · 15:20 이후 유효 시세로 청산 점검"
    elif market["code"] == "pre":
        short_reason = ("중립 탐색 5% 계획 대기 · 09:00–09:15 유효 시세로 진입 검사" if neutral_probe else
                        "ETF 전용 QA 승인 계획 대기 · 09:00–09:15 유효 시세로 진입 검사" if independent_short else "승인 계획 대기 · 09:00–09:15 유효 시세로 진입 검사")
    else:
        short_reason = short_status

    positive = [a for a in (long_report.get("allocations") or [])
                if isinstance(a, dict) and type(a.get("weight")) in (int, float) and a["weight"] > 0]
    if long_report.get("status") != "approved":
        long_reason = "장기 보고서 미승인·만료 · 신규 매수 보류"
    elif not positive:
        long_reason = "승인된 장기 매수 배분 0건 · 현금 유지"
    elif market["code"] == "pre":
        long_reason = "장기 매수 후보 있음 · 개장 후 기준가·시세 확인"
    else:
        long_reason = long_status

    if market["code"] == "pre":
        next_check = "다음 정규장 09:00–09:15 단기 진입 조건 확인"
    elif market["code"] == "open" and now.time() <= time(9, 15):
        next_check = "진입 시간 중 · 약 5분마다 조건 재확인"
    elif market["code"] == "open" and active:
        next_check = "15:20 이후 단기 ETF 청산 점검"
    elif market["code"] == "open":
        next_check = "단기 첫 진입 시간 종료 · 승인된 아침 계획의 장중 재진입만 점검 · 장기 조건은 계속 점검"
    else:
        next_check = "다음 정규장에 조건 재확인"

    trades = paper.get("recent_trades") or []
    last_trade_at = str(trades[0].get("at") or "")[:32] if trades and isinstance(trades[0], dict) else None
    return {"market": market["label"], "checked_at": paper.get("last_tick"),
            "quote_status": (paper.get("valuation_audit") or {}).get("status"),
            "short_reason": short_reason, "long_reason": long_reason,
            "short_runtime_status": short_status, "long_runtime_status": long_status,
            "next_check": next_check, "total_trades": paper.get("trades", 0),
            "last_trade_at": last_trade_at, "etf_best_probability": best,
            "etf_neutral_probe": neutral_probe,
            "etf_entry_probability_required": 50 if independent_short else 60,
            "etf_entry_probability_strict": independent_short,
            "etf_entry_probability_rule": ("50% 중립은 검증된 5거래일 추세 일치 시 5% 탐색, 50% 초과 · 60% 미만은 20%, 60% 이상은 40%" if forecast.get("schema") == 4 else
                                           "50% 초과 · 60% 미만은 단기자금 10%, 60% 이상은 25%" if independent_short else "60% 이상"),
            "etf_qa_status": ("ETF 전용 자료 검증 통과" if short_qa else "ETF 전용 QA 미승인") if independent_short else "구버전 · 전체 QA 적용",
            "etf_qa_reason": etf_qa_reason,
            "etf_qa_reason_code": reason_code if etf_qa_reason and reason_code in QA_REASONS else None}
