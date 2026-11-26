"""Alpaca IEX market data adapter. Read-only endpoints, keys never enter URLs/logs."""
from __future__ import annotations

from datetime import datetime, timezone, timedelta
import math
import re

import requests


DATA_BASE = "https://data.alpaca.markets"
PAPER_BASE = "https://paper-api.alpaca.markets"
SYMBOL = re.compile(r"^[A-Z][A-Z0-9.]{0,9}$")


def _stamp(value: str) -> datetime:
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("Market-data timestamp has no timezone")
    return result.astimezone(timezone.utc)


def _price(value) -> float:
    result = float(value)
    if not math.isfinite(result) or result <= 0:
        raise ValueError("Invalid market price")
    return result


class AlpacaIEXFeed:
    """Free single-venue feed; never represent it as consolidated NBBO."""

    def __init__(self, key_id: str, secret: str, session=None):
        if not key_id or not secret:
            raise ValueError("Alpaca API credentials are required")
        self._session = session or requests.Session()
        self._headers = {"APCA-API-KEY-ID": key_id, "APCA-API-SECRET-KEY": secret,
                         "Accept": "application/json"}

    def _get(self, base: str, path: str, params=None):
        try:
            response = self._session.get(base + path, headers=self._headers,
                                         params=params, timeout=12)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict):
                raise ValueError("Invalid market-data response")
            return result
        except (requests.RequestException, ValueError) as exc:
            # Provider error bodies can contain request details; never include them.
            raise RuntimeError(f"Alpaca read-only request failed: {type(exc).__name__}") from None

    @staticmethod
    def _symbols(symbols):
        unique = tuple(dict.fromkeys(str(s).upper().strip() for s in symbols))
        if not unique or len(unique) > 30 or any(not SYMBOL.fullmatch(s) for s in unique):
            raise ValueError("Invalid U.S. symbol list")
        return unique

    def clock(self) -> dict:
        raw = self._get(PAPER_BASE, "/v2/clock")
        return {"is_open": raw.get("is_open") is True,
                "timestamp": _stamp(raw["timestamp"]).isoformat(),
                "next_open": _stamp(raw["next_open"]).isoformat(),
                "next_close": _stamp(raw["next_close"]).isoformat()}

    def quotes(self, symbols, now: datetime, max_age_seconds: int = 120) -> dict:
        names = self._symbols(symbols)
        raw = self._get(DATA_BASE, "/v2/stocks/quotes/latest",
                        {"symbols": ",".join(names), "feed": "iex"}).get("quotes") or {}
        result = {}
        current = now.astimezone(timezone.utc)
        for symbol in names:
            item = raw.get(symbol)
            if not isinstance(item, dict):
                continue
            try:
                bid, ask = _price(item["bp"]), _price(item["ap"])
                at = _stamp(item["t"])
                age = (current - at).total_seconds()
                if bid > ask or age < -5 or age > max_age_seconds:
                    continue
                result[symbol] = {"symbol": symbol, "bid": bid, "ask": ask,
                                  "at": at.isoformat(), "source": "Alpaca IEX",
                                  "feed": "iex", "age_seconds": round(age, 1)}
            except (KeyError, ValueError, TypeError):
                continue
        return result

    def daily_bars(self, symbols, now: datetime, days: int = 100) -> dict:
        names = self._symbols(symbols)
        if not 20 <= days <= 200:
            raise ValueError("days must be between 20 and 200")
        raw = self._get(DATA_BASE, "/v2/stocks/bars", {
            "symbols": ",".join(names), "timeframe": "1Day", "feed": "iex",
            "adjustment": "all", "start": (now - timedelta(days=days * 2)).date().isoformat(),
            "end": now.date().isoformat(), "limit": 10000,
        }).get("bars") or {}
        result = {}
        for symbol in names:
            rows = []
            for item in raw.get(symbol) or []:
                try:
                    at = _stamp(item["t"])
                    if at > now.astimezone(timezone.utc):
                        continue
                    rows.append({"date": at.date().isoformat(), "close": _price(item["c"]),
                                 "volume": max(0, int(item.get("v") or 0))})
                except (KeyError, ValueError, TypeError):
                    continue
            if rows:
                result[symbol] = sorted(rows, key=lambda x: x["date"])[-days:]
        return result
