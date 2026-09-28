"""
(V3.11) 관심 종목별 최근 가격 흐름 — 트레이더·종목 중재자가 볼 종목 단위 사실.
FinanceDataReader로 최근 15일 종가를 받아 1일·5일 등락률을 계산한다 (LLM 미사용).
백테스트에서는 as_of 이후 데이터를 자르므로 look-ahead가 없다. 실패한 종목은 None.
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Optional


def stock_momentum(code: str, as_of: date) -> Optional[dict]:
    try:
        import FinanceDataReader as fdr
        df = fdr.DataReader(code, as_of - timedelta(days=15), as_of)
        df = df[df.index.date <= as_of]
        if df.empty or len(df) < 2:
            return None
        close = df["Close"].astype(float)
        d1 = float((close.iloc[-1] / close.iloc[-2] - 1) * 100)
        d5 = float((close.iloc[-1] / close.iloc[-6] - 1) * 100) if len(close) >= 6 else None
        return {"last_date": str(df.index[-1].date()), "close": round(float(close.iloc[-1]), 2),
                "chg_1d_pct": round(d1, 2), "chg_5d_pct": round(d5, 2) if d5 is not None else None}
    except Exception:  # noqa: BLE001
        return None


def build_stock_facts(universe: list[dict], as_of: date, news_titles: list[str]) -> list[dict]:
    """종목별 {code,name,core,momentum,news_count,news_sample} — 프롬프트와 기록에 쓴다."""
    facts = []
    for u in universe:
        tag = f"[{u['name']}]"
        titles = [t.split("] ", 1)[-1] for t in news_titles if tag in t]
        facts.append({"code": u["code"], "name": u["name"], "core": bool(u.get("core")), "rank": u.get("rank"),
                      "momentum": stock_momentum(u["code"], as_of),
                      "news_count": len(titles), "news_sample": titles[:4]})
    return facts


def facts_text(facts: list[dict]) -> str:
    lines = []
    for f in facts:
        m = f.get("momentum") or {}
        mom = (f"1일 {m['chg_1d_pct']:+.2f}%" + (f", 5일 {m['chg_5d_pct']:+.2f}%" if m.get("chg_5d_pct") is not None else "")) \
            if m else "가격 데이터 없음"
        news = f"뉴스 {f['news_count']}건" + (": " + " / ".join(f["news_sample"][:2]) if f["news_sample"] else "")
        lines.append(f"- {f['name']} ({'핵심' if f['core'] else '관심'}{', 시총 ' + str(f['rank']) + '위' if f.get('rank') else ''}): {mom} · {news}")
    return "\n".join(lines)
