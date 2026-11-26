"""U.S. research/paper-account entry point. All operations are local or read-only HTTP."""
from __future__ import annotations

import argparse
from datetime import datetime, timezone
import json
import os
from pathlib import Path

from config import Config
from security.secret_store import redact
from tools.paper_portfolio import locked, write
from us_market.feed import AlpacaIEXFeed
from us_market.ledger import fresh, public_summary, tick
from us_market.research import SYMBOLS, LONG_SYMBOLS, analyze


def paths():
    root = Path(__file__).resolve().parent.parent
    account = Path(os.environ.get("US_PAPER_PATH") or root / "us_paper.json").resolve()
    plan = Path(os.environ.get("US_PLAN_PATH") or root / "us_plan.json").resolve()
    if account == plan or account == Path(Config.PAPER_PORTFOLIO_PATH).resolve():
        raise ValueError("U.S. files must be separate from the Korean paper ledger")
    return account, plan


def _feed():
    key_id = Config.api_key("alpaca_id")
    secret = Config.api_key("alpaca_secret")
    if not key_id or not secret:
        raise ValueError("Set Alpaca paper keys with cli.py set-key alpaca_id/alpaca_secret")
    return AlpacaIEXFeed(key_id.reveal(), secret.reveal())


def _read(path):
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def _research(feed, now):
    # This is the only U.S. operation that calls a paid LLM provider.
    from cli import _real_llm
    from us_market.fundamentals import company_facts

    bars = feed.daily_bars(SYMBOLS, now)
    filings = {}
    agent = os.environ.get("US_SEC_USER_AGENT", "").strip()
    if agent:
        try:
            filings = company_facts(LONG_SYMBOLS, agent)
        except (RuntimeError, ValueError):
            filings = {}  # Long buys wait; a provider failure is never positive evidence.
    return analyze(_real_llm(), bars, filings, now)


def _hour_id(value):
    from zoneinfo import ZoneInfo
    return value.astimezone(ZoneInfo("America/New_York")).strftime("%Y-%m-%dT%H")


def run(operation: str, now=None, feed=None):
    now = now or datetime.now(timezone.utc)
    account_path, plan_path = paths()
    with locked(account_path):
        state = _read(account_path)
        if operation == "init":
            if state is None:
                amount = float(os.environ.get("US_PAPER_INITIAL_USD", "10000"))
                write(account_path, fresh(now, amount))
                return {"status": "initialized", "account": str(account_path)}
            return {"status": "already_exists", "account": str(account_path)}
        if operation == "status":
            return {"status": "ready" if state else "not_initialized",
                    "paper": public_summary(state), "plan": _safe_plan(_read(plan_path))}
        if state is None:
            raise ValueError("Initialize the U.S. paper account first (--init)")
        client = feed or _feed()
        clock = client.clock()
        if operation == "analyze":
            plan = _research(client, now)
            write(plan_path, plan)
            return {"status": "approved" if plan["approved"] else "qa_rejected",
                    "plan": _safe_plan(plan)}
        if operation == "cycle" and os.environ.get("US_AUTO_ANALYZE", "false").lower() == "true":
            plan = _read(plan_path)
            if clock["is_open"] and (not plan or _hour_id(datetime.fromisoformat(plan["created_at"])) != _hour_id(now)):
                plan = _research(client, now)
                write(plan_path, plan)
        else:
            plan = _read(plan_path)
        if operation not in ("tick", "cycle"):
            raise ValueError("Unsupported operation")
        needed = {symbol for sleeve in state["sleeves"].values() for symbol in sleeve["holdings"]}
        if plan and plan.get("approved") and plan.get("trade_date") == now.astimezone(
                __import__("zoneinfo").ZoneInfo("America/New_York")).date().isoformat():
            if plan.get("short_symbol"):
                needed.add(plan["short_symbol"])
            needed.update(plan.get("long_symbols") or [])
        quotes = client.quotes(sorted(needed), now) if needed else {}
        updated = tick(state, plan, quotes, clock, now)
        if updated != state:
            write(account_path, updated)
        return {"status": updated.get("valuation", {}).get("status", "pending"),
                "paper": public_summary(updated), "plan": _safe_plan(plan)}


def _safe_plan(plan, include_reports=False):
    if not isinstance(plan, dict):
        return None
    result = {key: plan.get(key) for key in ("created_at", "trade_date", "approved", "reason",
                                             "direction", "up_probability", "short_symbol", "long_symbols")}
    if include_reports:
        result["reports"] = [
            {"role": str(r.get("role", ""))[:60], "valid": r.get("valid") is True,
             "direction": r.get("direction"), "up_probability": r.get("up_probability"),
             "points": [str(p)[:300] for p in (r.get("points") or [])[:8]]}
            for r in (plan.get("reports") or [])[:4] if isinstance(r, dict)
        ]
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="U.S. read-only research and local paper trading")
    choice = parser.add_mutually_exclusive_group(required=True)
    for name in ("init", "analyze", "tick", "cycle", "status", "stream"):
        choice.add_argument("--" + name, action="store_true")
    args = parser.parse_args(argv)
    operation = next(name for name in ("init", "analyze", "tick", "cycle", "status", "stream")
                     if getattr(args, name))
    try:
        if operation == "stream":
            import asyncio
            from us_market.stream import stream_forever
            asyncio.run(stream_forever())
            return 0
        result = run(operation)
        print(json.dumps(result, ensure_ascii=False, allow_nan=False, indent=2))
        return 0 if result["status"] not in ("stale_quote", "qa_rejected") else 2
    except Exception as exc:
        # Never print provider exception bodies or key-bearing URLs.
        print(json.dumps({"status": "error", "type": type(exc).__name__,
                          "detail": redact(str(exc))[:180]}, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
