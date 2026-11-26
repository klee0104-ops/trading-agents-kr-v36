"""Cited U.S. market team. LLM proposes; deterministic QA owns approval."""
from __future__ import annotations

from datetime import datetime, timezone
import math

from rules.citations import CITATION_RULES, UNTRUSTED_DATA_RULE, check_points


LONG_SYMBOLS = ("AAPL", "MSFT", "NVDA", "GOOGL", "AMZN")
SHORT_SYMBOLS = ("SPY", "SH")
SYMBOLS = ("SPY", "SH", "QQQ", *LONG_SYMBOLS)


def evidence(bars: dict, filings: dict) -> list[dict]:
    registry = []
    for symbol in SYMBOLS:
        rows = bars.get(symbol) or []
        closes = [float(x["close"]) for x in rows if math.isfinite(float(x["close"]))]
        if len(closes) < 60:
            continue
        last, ma20, ma60 = closes[-1], sum(closes[-20:]) / 20, sum(closes[-60:]) / 60
        registry.append({"id": f"M{len(registry)+1}", "kind": "M", "group": "us_price",
                         "name": symbol,
                         "text": f"{symbol} 조정 일봉 종가 {last:.2f} USD, 20일 평균 {ma20:.2f} USD, 60일 평균 {ma60:.2f} USD",
                         "source": "Alpaca IEX adjusted daily bars", "date": rows[-1]["date"], "refs": []})
        company = filings.get(symbol) or {}
        for label, metric in (company.get("metrics") or {}).items():
            registry.append({"id": f"M{len(registry)+1}", "kind": "M", "group": "sec",
                             "name": symbol,
                             "text": f"{symbol} {label} {metric['value']} {metric['unit']} ({metric['form']}, 종료 {metric['period_end']})",
                             "source": metric["url"], "date": metric["filed"], "refs": []})
    return registry


def _call_role(llm, role: str, registry: list[dict]) -> dict:
    sources = "\n".join(f"[{r['id']}] {r['text']} (출처: {r['source']}, 기준: {r['date']})"
                        for r in registry)
    system = (f"당신은 미국 주식 모의투자 {role} 분석가입니다. 실거래 주문 권한이 없습니다. "
              "자료에 없는 사실이나 확률을 꾸며내지 마세요. JSON 하나만 출력하세요. "
              + CITATION_RULES + "\n" + UNTRUSTED_DATA_RULE)
    user = ("다음 근거만 사용하세요. JSON 형식: "
            '{"role":"문자열","direction":"up|down|flat","up_probability":0.0,'
            '"long_candidates":["AAPL"],"risk_approved":true,"points":["근거 문장 [M1]"]}. '
            "상승확률은 0~1 숫자이고 근거 부족이면 0.5, 장기 후보는 근거 있는 기업만.\n" + sources)
    try:
        raw = llm.complete_json(system, user, max_tokens=1400, use_web_search=False)
    except Exception:
        return {"role": role, "valid": False, "reason": "model_error", "points": []}
    if not isinstance(raw, dict):
        return {"role": role, "valid": False, "reason": "invalid_schema", "points": []}
    points = raw.get("points")
    if not isinstance(points, list) or not points or len(points) > 8:
        return {"role": role, "valid": False, "reason": "missing_evidence", "points": []}
    checked = check_points([str(p)[:300] for p in points], registry)
    verified = [p["text"] for p in checked if p["status"] == "ok"]
    try:
        p = float(raw["up_probability"])
    except (KeyError, TypeError, ValueError):
        p = float("nan")
    direction = raw.get("direction")
    expected = "up" if p >= 0.56 else "down" if p <= 0.44 else "flat"
    valid = (len(verified) >= 1 and len(verified) == len(points)
             and math.isfinite(p) and 0 <= p <= 1 and direction == expected)
    candidates = raw.get("long_candidates") or []
    by_id = {item["id"]: item for item in registry}
    sourced = {by_id[ref]["name"] for item in checked if item["status"] == "ok"
               for ref in item["ids"] if by_id[ref]["group"] == "sec"}
    return {"role": role, "valid": valid, "reason": "verified" if valid else "qa_rejected",
            "risk_approved": raw.get("risk_approved") is True if role == "독립 위험 QA" else None,
            "direction": direction if direction in ("up", "down", "flat") else "flat",
            "up_probability": p if math.isfinite(p) and 0 <= p <= 1 else 0.5,
            "long_candidates": [s for s in candidates if s in LONG_SYMBOLS and s in sourced]
            if isinstance(candidates, list) else [],
            "points": verified}


def analyze(llm, bars: dict, filings: dict, now: datetime) -> dict:
    registry = evidence(bars, filings)
    at = now.astimezone(timezone.utc)
    reports = [_call_role(llm, role, registry)
               for role in ("시장 상승 근거", "시장 하락 반론", "장기 기업", "독립 위험 QA")]
    priced = {r["name"] for r in registry if r["group"] == "us_price"}
    valid = all(r["valid"] for r in reports) and reports[3].get("risk_approved") is True and {"SPY", "SH"}.issubset(priced)
    probabilities = [r["up_probability"] for r in reports[:2]]
    p = sum(probabilities) / len(probabilities) if valid else 0.5
    direction = "up" if p >= 0.56 else "down" if p <= 0.44 else "flat"
    short = "SPY" if direction == "up" else "SH" if direction == "down" else None
    # Long entries require independent analyst support and an SEC filing figure.
    candidates = set(reports[2].get("long_candidates") or []) if valid else set()
    documented = {r["name"] for r in registry if r["group"] == "sec"}
    longs = [s for s in LONG_SYMBOLS if s in candidates and s in documented][:4]
    return {"schema": 1, "market": "US", "created_at": at.isoformat(),
            "trade_date": at.astimezone(__import__("zoneinfo").ZoneInfo("America/New_York")).date().isoformat(),
            "approved": valid, "reason": "verified" if valid else "team_qa_failed",
            "direction": direction, "up_probability": round(p, 4),
            "short_symbol": short if valid else None, "long_symbols": longs,
            "reports": reports,
            "source_registry": [{k: r[k] for k in ("id", "name", "source", "date", "text")}
                                for r in registry]}
