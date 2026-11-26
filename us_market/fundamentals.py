"""Small, optional SEC XBRL evidence adapter for the U.S. long team."""
from __future__ import annotations

from datetime import datetime, timezone
import re

import requests


SEC_SYMBOLS = "https://www.sec.gov/files/company_tickers.json"
SEC_FACTS = "https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json"
_METRICS = {
    "eps_diluted": ("EarningsPerShareDiluted", "USD/shares"),
    "revenue": ("Revenues", "USD"),
    "net_income": ("NetIncomeLoss", "USD"),
}


def _get(url, user_agent, session):
    try:
        response = session.get(url, headers={"User-Agent": user_agent,
                                             "Accept": "application/json"}, timeout=12)
        response.raise_for_status()
        return response.json()
    except (requests.RequestException, ValueError) as exc:
        raise RuntimeError(f"SEC read-only request failed: {type(exc).__name__}") from None


def company_facts(symbols, user_agent: str, session=None) -> dict:
    """Return sourced figures only; never infer growth from mixed fiscal periods."""
    if not re.fullmatch(r"[^\r\n]{8,160}", user_agent or "") or "@" not in user_agent:
        raise ValueError("US_SEC_USER_AGENT must identify an app and contact email")
    client = session or requests.Session()
    mapping = _get(SEC_SYMBOLS, user_agent, client)
    by_symbol = {str(row.get("ticker", "")).upper(): int(row["cik_str"])
                 for row in mapping.values() if isinstance(row, dict) and row.get("cik_str")}
    result = {}
    today = datetime.now(timezone.utc).date().isoformat()
    for symbol in tuple(dict.fromkeys(symbols)):
        cik = by_symbol.get(symbol)
        if not cik:
            continue
        facts = _get(SEC_FACTS.format(cik=cik), user_agent, client)
        gaap = ((facts.get("facts") or {}).get("us-gaap") or {})
        metrics = {}
        for label, (tag, unit) in _METRICS.items():
            rows = (((gaap.get(tag) or {}).get("units") or {}).get(unit) or [])
            valid = [r for r in rows if r.get("form") in ("10-K", "10-Q")
                     and r.get("filed", "") <= today and r.get("end")
                     and isinstance(r.get("val"), (int, float))]
            if valid:
                item = max(valid, key=lambda r: (r["end"], r["filed"]))
                metrics[label] = {"value": item["val"], "unit": unit,
                                  "period_end": item["end"], "filed": item["filed"],
                                  "form": item["form"], "source": "SEC Company Facts",
                                  "url": SEC_FACTS.format(cik=cik)}
        result[symbol] = {"cik": cik, "name": str(facts.get("entityName") or symbol)[:100],
                          "metrics": metrics}
    return result
