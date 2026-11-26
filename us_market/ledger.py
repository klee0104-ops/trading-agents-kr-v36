"""Independent USD paper ledger. Quote-driven estimates only; no broker orders."""
from __future__ import annotations

from datetime import datetime, timezone
import copy
import math
from zoneinfo import ZoneInfo


ET = ZoneInfo("America/New_York")
SLIPPAGE = 0.0005
MAX_SHORT_WEIGHT = 0.25
MAX_LONG_WEIGHT = 0.10


def fresh(now: datetime, initial_usd: float = 10000.0) -> dict:
    amount = float(initial_usd)
    if not math.isfinite(amount) or amount < 1000 or amount > 10_000_000:
        raise ValueError("US initial paper cash must be 1,000–10,000,000 USD")
    return {"schema": 1, "market": "US", "currency": "USD", "created_at": now.isoformat(),
            "initial_cash": amount, "last_tick": None, "last_signal_id": None,
            "last_exit_at": None, "day": None, "short_day_start": amount * 0.6,
            "short_blocked": False, "peak_equity": amount,
            "sleeves": {
                "short": {"initial_cash": round(amount * 0.6, 2), "cash": round(amount * 0.6, 2),
                          "holdings": {}, "realized_pnl": 0.0},
                "long": {"initial_cash": round(amount * 0.4, 2), "cash": round(amount * 0.4, 2),
                         "holdings": {}, "realized_pnl": 0.0}},
            "trades": [], "valuation": {"status": "pending", "equity": None}}


def _mid(quote):
    return (quote["bid"] + quote["ask"]) / 2


def _equity(sleeve, quotes):
    return sleeve["cash"] + sum(h["qty"] * _mid(quotes[symbol])
                                for symbol, h in sleeve["holdings"].items())


def _fill(state, sleeve_name, symbol, side, quote, now, budget=0):
    sleeve = state["sleeves"][sleeve_name]
    holdings = sleeve["holdings"]
    if side == "buy":
        price = round(quote["ask"] * (1 + SLIPPAGE), 4)
        qty = math.floor(min(float(budget), sleeve["cash"]) / price)
        if qty <= 0:
            return False
        old = holdings.get(symbol, {"qty": 0, "cost": 0.0})
        cost = round(qty * price, 4)
        holdings[symbol] = {"qty": old["qty"] + qty, "cost": round(old["cost"] + cost, 4)}
        sleeve["cash"] = round(sleeve["cash"] - cost, 4)
        realized = None
    else:
        old = holdings.get(symbol)
        if not old:
            return False
        qty = old["qty"]
        price = round(quote["bid"] * (1 - SLIPPAGE), 4)
        proceeds = round(qty * price, 4)
        realized = round(proceeds - old["cost"], 4)
        sleeve["cash"] = round(sleeve["cash"] + proceeds, 4)
        sleeve["realized_pnl"] = round(sleeve["realized_pnl"] + realized, 4)
        del holdings[symbol]
    state["trades"].append({"at": now.isoformat(), "sleeve": sleeve_name,
                            "symbol": symbol, "side": side, "qty": qty, "price": price,
                            "realized_pnl": realized, "source": quote["source"],
                            "quote_at": quote["at"], "fill_model": "IEX bid/ask ±5bp"})
    return True


def tick(state: dict, plan: dict | None, quotes: dict, clock: dict, now: datetime) -> dict:
    """Return updated copy; invalid/stale prices pause all mutations and valuation."""
    result = copy.deepcopy(state)
    if result.get("schema") != 1 or result.get("market") != "US":
        raise ValueError("Unknown U.S. paper ledger schema")
    if now.tzinfo is None:
        raise ValueError("Timezone-aware now required")
    if result.get("last_tick") and datetime.fromisoformat(result["last_tick"]) >= now:
        return result
    local = now.astimezone(ET)
    day = local.date().isoformat()
    from us_market.research import LONG_SYMBOLS
    plan_valid = bool(
        isinstance(plan, dict) and plan.get("schema") == 1
        and plan.get("market") == "US" and plan.get("approved") is True
        and plan.get("trade_date") == day
        and plan.get("short_symbol") in (None, "SPY", "SH")
        and isinstance(plan.get("long_symbols"), list)
        and len(plan["long_symbols"]) <= 4
        and all(symbol in LONG_SYMBOLS for symbol in plan["long_symbols"])
        and isinstance(plan.get("created_at"), str)
    )
    if plan_valid:
        try:
            created = datetime.fromisoformat(plan["created_at"])
            plan_valid = created.tzinfo is not None and 0 <= (now - created).total_seconds() <= 4 * 3600
        except ValueError:
            plan_valid = False
    held = {symbol for sleeve in result["sleeves"].values() for symbol in sleeve["holdings"]}
    needed = held | ({plan.get("short_symbol")} if plan_valid and plan.get("short_symbol") else set())
    if plan_valid:
        needed.update(plan.get("long_symbols") or [])
    for symbol in needed:
        q = quotes.get(symbol)
        if not q or q.get("feed") != "iex" or q.get("source") != "Alpaca IEX":
            result["valuation"] = {"status": "stale_quote", "equity": None}
            return result
        try:
            at = datetime.fromisoformat(q["at"])
            if at.tzinfo is None or not 0 <= (now.astimezone(timezone.utc) - at.astimezone(timezone.utc)).total_seconds() <= 120:
                raise ValueError
            if not (math.isfinite(q["bid"]) and math.isfinite(q["ask"]) and 0 < q["bid"] <= q["ask"]):
                raise ValueError
        except (TypeError, ValueError, KeyError):
            result["valuation"] = {"status": "stale_quote", "equity": None}
            return result
    short = result["sleeves"]["short"]
    overnight = result["day"] is not None and result["day"] != day and bool(short["holdings"])
    if result["day"] != day:
        result["day"] = day
        result["short_day_start"] = round(_equity(short, quotes), 4)
        result["short_blocked"] = False
    short_equity = _equity(short, quotes)
    if short_equity <= result["short_day_start"] * 0.98:
        result["short_blocked"] = True
    open_now = clock.get("is_open") is True and 9 <= local.hour < 16
    # A risk exit works even if the daily research is no longer valid.
    exit_due = (local.hour == 15 and local.minute >= 45) or result["short_blocked"] or overnight
    if open_now and exit_due:
        for symbol in list(short["holdings"]):
            if _fill(result, "short", symbol, "sell", quotes[symbol], now):
                result["last_exit_at"] = now.isoformat()
    if open_now and plan_valid and not result["short_blocked"] and not overnight:
        if (local.hour > 9 or local.minute >= 35) and (local.hour < 15 or local.minute < 30):
            target = plan.get("short_symbol") if plan.get("short_symbol") in ("SPY", "SH") else None
            # A new research decision may reverse the position; sell first.
            for symbol in list(short["holdings"]):
                if symbol != target:
                    _fill(result, "short", symbol, "sell", quotes[symbol], now)
            signal_id = plan.get("created_at")
            if target and not short["holdings"] and result.get("last_signal_id") != signal_id:
                p = float(plan.get("up_probability", 0.5))
                confidence = p if target == "SPY" else 1 - p
                weight = 0.10 if confidence < 0.60 else MAX_SHORT_WEIGHT
                if confidence > 0.5 and _fill(result, "short", target, "buy", quotes[target], now,
                                             short["initial_cash"] * weight):
                    result["last_signal_id"] = signal_id
    # Long research is a separate sleeve; a short-sleeve loss limit must not
    # silently suppress a fully approved long-term entry.
    if open_now and plan_valid and (local.hour > 9 or local.minute >= 35):
        if local.hour < 15 or local.minute < 30:
            long = result["sleeves"]["long"]
            for symbol in plan.get("long_symbols") or []:
                if symbol not in long["holdings"] and symbol in quotes:
                    _fill(result, "long", symbol, "buy", quotes[symbol], now,
                          long["initial_cash"] * MAX_LONG_WEIGHT)
    equity = round(sum(_equity(sleeve, quotes) for sleeve in result["sleeves"].values()), 4)
    result["peak_equity"] = max(result["peak_equity"], equity)
    result["valuation"] = {"status": "ok", "equity": equity,
                           "pnl": round(equity - result["initial_cash"], 4),
                           "return_pct": round((equity / result["initial_cash"] - 1) * 100, 4),
                           "source": "Alpaca IEX quote midpoint", "at": now.isoformat()}
    result["last_tick"] = now.isoformat()
    result["trades"] = result["trades"][-500:]
    return result


def public_summary(state: dict | None) -> dict | None:
    if not state or state.get("market") != "US":
        return None
    valuation = state.get("valuation") or {}
    return {"currency": "USD", "initial_cash": state["initial_cash"],
            "last_tick": state.get("last_tick"), "valuation": valuation,
            "cash": round(sum(s["cash"] for s in state["sleeves"].values()), 2),
            "realized_pnl": round(sum(s["realized_pnl"] for s in state["sleeves"].values()), 2),
            "holdings": [{"sleeve": name, "symbol": symbol, "qty": h["qty"]}
                         for name, sleeve in state["sleeves"].items()
                         for symbol, h in sleeve["holdings"].items()],
            "recent_trades": [{k: t[k] for k in ("at", "sleeve", "symbol", "side", "qty", "realized_pnl")}
                              for t in state.get("trades", [])[-8:]],
            "feed_note": "IEX 단일 거래소 시세 · 자체 가상 체결 · 실거래 아님"}
