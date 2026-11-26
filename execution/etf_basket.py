"""Isolated AI ETF basket paper ledger. No broker, network, or model calls.

The model proposes weights; deterministic QA owns approval and execution.
The 30m primary account is never read or mutated by this module.
"""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime, time
from hashlib import sha256
import json
import math
import re

from data.krx_calendar import is_trading_day, next_trading_day_after
from data.sessions import market_status

CAPITAL = 10_000_000
FEE = 0.00015
SLIPPAGE = 0.0005
UNIVERSE = {
    "069500": {"name": "KODEX 200", "role": "국내 대표주", "cap": 40, "source_id": "M701"},
    "379800": {"name": "KODEX 미국S&P500", "role": "미국 대표주·환노출", "cap": 35, "source_id": "M702"},
    "132030": {"name": "KODEX 골드선물(H)", "role": "금선물·환헤지", "cap": 20, "source_id": "M703"},
    "488770": {"name": "KODEX 머니마켓액티브", "role": "초단기 금융상품", "cap": 40, "source_id": "M704"},
}
BENCHMARK = "069500"
MIN_CASH_PCT = 10


def summarize_evidence(evidence, basis, retrieved_at):
    """Keep a bounded, numeric summary of the ten closes used for a plan.

    A later re-fetch is explicitly labelled; it is never presented as the
    original model input or as an additional AI conclusion.
    """
    if basis not in ("generation_input", "rechecked_after_creation"):
        raise ValueError("Invalid evidence basis")
    result = {}
    for code in UNIVERSE:
        row = evidence.get(code) if isinstance(evidence, dict) else None
        closes = row.get("recent_closes") if isinstance(row, dict) else None
        if (not isinstance(closes, list) or len(closes) != 10
                or any(not _positive(item) for item in closes)
                or not isinstance(row.get("date"), str)):
            raise ValueError("Ten valid daily closes are required")
        first, last = closes[0], closes[-1]
        result[code] = {"source_id": UNIVERSE[code]["source_id"],
                        "date": row["date"], "basis": basis,
                        "retrieved_at": retrieved_at,
                        "first_close": round(first, 2), "last_close": round(last, 2),
                        "change_pct": round((last / first - 1) * 100, 2),
                        "range_pct": round((max(closes) / min(closes) - 1) * 100, 2),
                        "up_sessions": sum(b > a for a, b in zip(closes, closes[1:])),
                        "down_sessions": sum(b < a for a, b in zip(closes, closes[1:]))}
    return result


def _positive(value):
    return type(value) in (int, float) and math.isfinite(value) and value > 0


def previous_session(day):
    from datetime import timedelta
    current = day - timedelta(days=1)
    while not is_trading_day(current):
        current -= timedelta(days=1)
    return current


def evidence_session(now):
    """Last fully closed session; intraday candles are never planning evidence."""
    return now.date() if is_trading_day(now.date()) and now.time() >= time(15, 40) else previous_session(now.date())


def validate_plan(raw, evidence, now):
    """Reject unsupported instruments, stale evidence, fabricated IDs and risky weights."""
    reason = None
    rows = raw.get("positions") if isinstance(raw, dict) else None
    if not isinstance(evidence, dict) or set(evidence) != set(UNIVERSE):
        reason = "ETF 네 종목 자료가 모두 필요합니다"
    elif any(not isinstance(evidence[c], dict) or evidence[c].get("date") != str(evidence_session(now))
             or not _positive(evidence[c].get("close")) for c in UNIVERSE):
        reason = "직전 거래일 종가 자료가 누락됐습니다"
    elif not isinstance(rows, list) or not 2 <= len(rows) <= 4:
        reason = "ETF 2~4종을 골라야 합니다"
    selected = []
    seen = set()
    total = 0
    if reason is None:
        for row in rows:
            if not isinstance(row, dict):
                reason = "ETF 선택 형식 오류"; break
            code, weight = row.get("code"), row.get("weight_pct")
            why, ids = row.get("reason"), row.get("source_ids")
            if code not in UNIVERSE or code in seen:
                reason = "허용되지 않거나 중복된 ETF"; break
            if type(weight) is not int or not 1 <= weight <= UNIVERSE[code]["cap"]:
                reason = "ETF별 한도 또는 정수 비중 위반"; break
            if (not isinstance(why, str) or not 12 <= len(why) <= 240 or re.search(r"<|>|https?://", why)
                    or "[" + UNIVERSE[code]["source_id"] + "]" not in why
                    or re.search(r"\d", re.sub(r"\[M70[1-4]\]", "", why))):
                reason = "판단 근거 누락 또는 위험한 문자열"; break
            if not isinstance(ids, list) or UNIVERSE[code]["source_id"] not in ids or set(ids) - {x["source_id"] for x in UNIVERSE.values()}:
                reason = "종목별 시세 근거 ID 누락"; break
            seen.add(code); total += weight
            analysis = row.get("analysis")
            detailed = {}
            if isinstance(analysis, dict):
                expected_id = UNIVERSE[code]["source_id"]
                for key in ("price_reading", "allocation_thesis", "countercase"):
                    sentence = analysis.get(key)
                    if (isinstance(sentence, str) and 12 <= len(sentence) <= 240
                            and re.findall(r"\[M70[1-4]\]", sentence) == ["[" + expected_id + "]"]
                            and not re.search(r"<|>|https?://", sentence)
                            and not re.search(r"\d", re.sub(r"\[M70[1-4]\]", "", sentence))):
                        detailed[key] = sentence.strip()
            selected.append({"code": code, "name": UNIVERSE[code]["name"],
                             "role": UNIVERSE[code]["role"], "weight_pct": weight,
                             "reason": why.strip(), "source_ids": sorted(set(ids)),
                             **({"analysis": detailed} if len(detailed) == 3 else {})})
        if reason is None and not 40 <= total <= 100 - MIN_CASH_PCT:
            reason = "ETF 합계 40~90%와 현금 최소 10% 규칙 위반"
    target = now.date() if is_trading_day(now.date()) and now.time() < time(9) else next_trading_day_after(now.date())
    digest = sha256(json.dumps({"date": str(target), "rows": selected, "at": now.isoformat()},
                               sort_keys=True, ensure_ascii=False).encode()).hexdigest()[:20]
    try:
        evidence_summary = summarize_evidence(evidence, "generation_input", now.isoformat())
    except ValueError:
        evidence_summary = {}
    return {"schema": 1, "mode": "paper", "strategy": "ai_etf_basket_comparison",
            "id": digest, "created_at": now.isoformat(), "target_date": str(target),
            "evidence_date": str(evidence_session(now)),
            "status": "approved" if reason is None else "held", "qa_reason": reason or "자료·근거·비중 검증 통과",
            "positions": selected if reason is None else [], "cash_weight_pct": 100 - total if reason is None else 100,
            "evidence_summary": evidence_summary if reason is None else {},
            "universe": [{"code": c, **spec} for c, spec in UNIVERSE.items()]}


def fresh(now, capital=CAPITAL):
    if not _positive(capital):
        raise ValueError("Invalid basket capital")
    return {"schema": 1, "mode": "paper", "strategy": "ai_etf_basket_comparison",
            "created_at": now.isoformat(), "initial_cash": capital, "cash": float(capital),
            "holdings": {}, "benchmark": {"cash": float(capital), "qty": 0},
            "trades": [], "equity_history": [], "last_tick": None,
            "executed_plan_id": None, "equity": float(capital), "benchmark_equity": float(capital),
            "valuation_status": "not_started", "status": "AI 바스켓 계획 대기"}


def valid_quote(quote, now):
    try:
        observed = datetime.fromisoformat(quote["observed_at"])
        traded = datetime.fromisoformat(quote["traded_at"])
        if observed.tzinfo is None or traded.tzinfo is None:
            return False
        return (quote.get("date") == str(now.date()) and quote.get("market_status") == "OPEN"
                and 0 <= (now - observed.astimezone(now.tzinfo)).total_seconds() <= 60
                and 0 <= (now - traded.astimezone(now.tzinfo)).total_seconds() <= 900
                and _positive(quote.get("value")))
    except (KeyError, TypeError, ValueError):
        return False


def _approved_for_tick(plan, now):
    if not isinstance(plan, dict) or plan.get("status") != "approved" or plan.get("mode") != "paper" or plan.get("strategy") != "ai_etf_basket_comparison" or plan.get("target_date") != str(now.date()):
        return False
    rows = plan.get("positions")
    if not isinstance(rows, list) or not 2 <= len(rows) <= 4 or not isinstance(plan.get("id"), str):
        return False
    seen, total = set(), 0
    for row in rows:
        if not isinstance(row, dict):
            return False
        code, weight = row.get("code"), row.get("weight_pct")
        if code not in UNIVERSE or code in seen or type(weight) is not int or not 1 <= weight <= UNIVERSE[code]["cap"]:
            return False
        seen.add(code); total += weight
    return 40 <= total <= 90 and plan.get("cash_weight_pct") == 100 - total


def _trade(state, code, side, qty, price, now, plan_id):
    if qty <= 0:
        return
    fill = price * (1 + SLIPPAGE if side == "buy" else 1 - SLIPPAGE)
    gross = qty * fill
    fee = gross * FEE
    held = state["holdings"].get(code, 0)
    if side == "buy":
        if gross + fee > state["cash"] + 1e-6:
            raise ValueError("Insufficient basket cash")
        state["cash"] -= gross + fee
        state["holdings"][code] = held + qty
    else:
        if qty > held:
            raise ValueError("Insufficient basket holding")
        state["cash"] += gross - fee
        remaining = held - qty
        if remaining:
            state["holdings"][code] = remaining
        else:
            del state["holdings"][code]
    state["trades"].append({"at": now.isoformat(), "code": code, "side": side, "qty": qty,
                            "price": round(fill, 4), "fee": round(fee, 4), "plan_id": plan_id})


def _holding_costs(state):
    """Rebuild remaining cost from fills, including fees, without changing the ledger."""
    positions = {}
    for trade in state.get("trades", []):
        code, side, qty = trade.get("code"), trade.get("side"), trade.get("qty")
        price, fee = trade.get("price"), trade.get("fee")
        if (code not in UNIVERSE or side not in ("buy", "sell") or type(qty) is not int
                or qty <= 0 or not _positive(price) or not isinstance(fee, (int, float))
                or not math.isfinite(fee) or fee < 0):
            return None
        row = positions.setdefault(code, {"qty": 0, "cost": 0.0})
        if side == "buy":
            row["qty"] += qty
            row["cost"] += qty * price + fee
        else:
            if qty > row["qty"]:
                return None
            row["cost"] *= (row["qty"] - qty) / row["qty"]
            row["qty"] -= qty
    holdings = state.get("holdings", {})
    if not isinstance(holdings, dict) or any(
            code not in positions or positions[code]["qty"] != qty
            for code, qty in holdings.items()):
        return None
    if any(row["qty"] and code not in holdings for code, row in positions.items()):
        return None
    return positions


def tick(state, plan, quotes, now):
    """One market-data-bounded transition; no quote means no fill or invented return."""
    if state.get("schema") != 1 or state.get("strategy") != "ai_etf_basket_comparison":
        raise ValueError("Invalid basket ledger")
    result = deepcopy(state)
    last = result.get("last_tick")
    if last and datetime.fromisoformat(last) >= now:
        return result
    if market_status(now)["code"] != "open":
        result["status"] = "휴장·장외 · 다음 정규장 점검 대기"
        return result
    approved = _approved_for_tick(plan, now)
    intended = {r["code"] for r in plan["positions"]} if approved else set()
    required = set(result["holdings"]) | intended
    if result["benchmark"]["qty"] or approved:
        required.add(BENCHMARK)
    if any(code not in UNIVERSE or not valid_quote(quotes.get(code) or {}, now) for code in required):
        result["valuation_status"] = "partial"
        result["position_valuation"] = []
        result["status"] = "보유·계획 ETF 시세 누락·지연 · 체결/수익률 보류"
        result["last_tick"] = now.isoformat()
        return result
    if approved and result.get("executed_plan_id") != plan["id"]:
        if result.get("executed_plan_id") is not None:
            result["status"] = "기존 바스켓 평가 중 · 새 계획은 다음 별도 리밸런싱에서 검토"
        else:
            baseline = result["benchmark"]
            bprice = quotes[BENCHMARK]["value"] * (1 + SLIPPAGE)
            bqty = int(baseline["cash"] / (bprice * (1 + FEE)))
            baseline["qty"] = bqty
            baseline["cash"] -= bqty * bprice * (1 + FEE)
            for row in plan["positions"]:
                code = row["code"]
                price = quotes[code]["value"] * (1 + SLIPPAGE)
                budget = result["initial_cash"] * row["weight_pct"] / 100
                qty = min(int(budget / (price * (1 + FEE))), int(result["cash"] / (price * (1 + FEE))))
                _trade(result, code, "buy", qty, quotes[code]["value"], now, plan["id"])
            result["executed_plan_id"] = plan["id"]
            result["executed_at"] = now.isoformat()
            result["active_plan"] = {"id": plan["id"], "positions": deepcopy(plan["positions"]),
                                     "cash_weight_pct": plan["cash_weight_pct"], "target_date": plan["target_date"]}
            result["status"] = "AI 바스켓 모의 진입 · KODEX 200 동일 시점 비교 시작"
    elif not approved and result.get("executed_plan_id") is None:
        result["status"] = "AI 계획 승인 대기 · 가상 현금 유지"
    if result["holdings"] or result["benchmark"]["qty"]:
        costs = _holding_costs(result)
        if costs is None:
            raise ValueError("Basket fills and holdings disagree")
        result["equity"] = result["cash"] + sum(qty * quotes[c]["value"] for c, qty in result["holdings"].items())
        result["benchmark_equity"] = result["benchmark"]["cash"] + result["benchmark"]["qty"] * quotes[BENCHMARK]["value"]
        result["valuation_status"] = "ok"
        result["position_valuation"] = [
            {"code": code, "qty": qty, "at": now.isoformat(),
             "market_price": quotes[code]["value"],
             "market_value": round(qty * quotes[code]["value"], 2)}
            for code, qty in result["holdings"].items()]
        result["equity_history"].append({"at": now.isoformat(), "equity": round(result["equity"], 2),
                                         "benchmark_equity": round(result["benchmark_equity"], 2)})
        result["equity_history"] = result["equity_history"][-2500:]
    result["last_tick"] = now.isoformat()
    return result


def public_summary(state, plan=None):
    """Explicit safe projection; never serialize internal quote or model payloads."""
    if not isinstance(state, dict) or state.get("strategy") != "ai_etf_basket_comparison":
        return None
    active = state.get("active_plan") or {}
    candidate = plan if isinstance(plan, dict) and plan.get("strategy") == "ai_etf_basket_comparison" else {}
    equity = state.get("equity") if state.get("valuation_status") == "ok" else None
    benchmark = state.get("benchmark_equity") if state.get("valuation_status") == "ok" else None
    capital = state["initial_cash"]
    costs = _holding_costs(state)
    valuations = {row["code"]: row for row in state.get("position_valuation", [])
                  if isinstance(row, dict) and row.get("code") in UNIVERSE}
    holdings = []
    for code, qty in state["holdings"].items():
        if code not in UNIVERSE:
            continue
        row = {"code": code, "name": UNIVERSE[code]["name"], "qty": qty}
        if costs is not None:
            cost = costs[code]["cost"]
            row["invested_cost"] = round(cost, 2)
            current = valuations.get(code)
            if (state.get("valuation_status") == "ok" and current
                    and current.get("at") == state.get("last_tick")
                    and current.get("qty") == qty and _positive(current.get("market_price"))
                    and _positive(current.get("market_value"))):
                current_value = current["market_value"]
                row.update({"market_price": current["market_price"], "market_value": current_value,
                            "unrealized_pnl": round(current_value - cost, 2),
                            "return_pct": round((current_value / cost - 1) * 100, 4) if cost > 0 else None,
                            "valued_at": current["at"]})
        holdings.append(row)
    status = (state.get("status") if state.get("executed_at") or
              (state.get("last_tick") and state.get("valuation_status") == "partial") else
              "AI 계획 승인 · 정규장 유효 시세 대기" if candidate.get("status") == "approved" else
              "AI 계획 대기 · 가상 현금 유지")
    return {"label": "AI ETF 바스켓 비교 가상계좌", "initial_cash": capital,
            "cash": round(state["cash"], 2), "status": status,
            "valuation_status": state.get("valuation_status"), "last_tick": state.get("last_tick"),
            "equity": round(equity, 2) if equity is not None else None,
            "return_pct": round((equity / capital - 1) * 100, 4) if equity is not None else None,
            "benchmark_equity": round(benchmark, 2) if benchmark is not None else None,
            "benchmark_return_pct": round((benchmark / capital - 1) * 100, 4) if benchmark is not None else None,
            "holdings": holdings,
            "plan": {"status": candidate.get("status", "not_run"), "qa_reason": candidate.get("qa_reason", "첫 AI 계획 대기"),
                     "created_at": candidate.get("created_at"), "target_date": candidate.get("target_date"),
                     "evidence_date": candidate.get("evidence_date"),
                     "evidence_summary": candidate.get("evidence_summary", {}),
                     "positions": candidate.get("positions", []), "cash_weight_pct": candidate.get("cash_weight_pct", 100)},
            "active_plan_id": active.get("id"), "executed_at": state.get("executed_at"),
            "trades": state.get("trades", [])[-20:], "equity_history": state.get("equity_history", [])[-80:]}
