"""
분석가 에이전트: 원시 시장 데이터를 해석해서 다른 에이전트들이 쓸 수 있는
요약 리포트를 만든다. TradingAgents의 fundamental/sentiment/technical analyst를
간소화해서 하나의 "MarketAnalyst"로 합쳤다 (초반 단계에서는 과도한 세분화보다
파이프라인이 끝까지 도는 것이 우선).
"""
from __future__ import annotations
from typing import Optional
from agents.base import LLMClient
from data.market_data import MarketSnapshot
from rules.engine import MarketAnalysis, build_context_text


ANALYST_SYSTEM_PROMPT = """당신은 시장 분석가(Analyst) 에이전트입니다.
주어진 미국 시장 지표 데이터와, 이미 코드로 계산된 기술적 지표/규칙엔진 결과를 근거로,
한국 KOSPI 시장에 미칠 영향을 객관적으로 요약하세요. RSI/MACD/장세 분류 등은 이미
정확히 계산되어 주어지므로 다시 계산하지 말고 해석에만 집중하세요.
원/달러 환율은 외국인 수급의 핵심 변수입니다(원화 약세=외국인 매도 압력). 금리·유가·달러인덱스도
KOSPI에는 "외국인 자금이 들어올 환경인가"의 관점에서 해석하세요.
의견을 강하게 주장하지 말고, 사실과 그 해석만 간결하게 정리하세요.
반드시 JSON만 출력하세요: {"direction": "up 또는 down", "confidence": "낮음/보통/높음", "summary": "...", "notable_points": ["...", "..."]}"""


def run_market_analyst(llm: LLMClient, snapshot: MarketSnapshot,
                        market_analysis: Optional[MarketAnalysis] = None) -> dict:
    lines = []
    for q in snapshot.us_indices:
        detail = f"- {q.name}: {q.change_pct:+.2f}% ({q.direction})"
        if q.value is not None:
            detail += f", 현재가={q.value}"
        if q.rsi14 is not None:
            detail += f", RSI14={q.rsi14}"
        if q.macd_hist is not None:
            detail += f", MACD_hist={q.macd_hist}"
        if q.bb_percent_b is not None:
            detail += f", BB%B={q.bb_percent_b}"
        lines.append(detail)
    indices_text = "\n".join(lines)

    regime_text = build_context_text(market_analysis) if market_analysis else ""
    macro_lines = [f"- {q.name}: {q.change_pct:+.2f}%" + (f", 현재 {q.value}" if q.value is not None else "")
                   for q in (snapshot.macro or [])]
    if snapshot.kr_flow:
        from data.kr_flow import describe
        macro_lines.append("- " + describe(snapshot.kr_flow))
    macro_text = ("\n\n거시 지표 (환율·금리·유가·달러·외국인 수급 — 외국인 자금 흐름의 환경):\n" + "\n".join(macro_lines)) \
        if macro_lines else ""

    user_prompt = f"""기준 시점: {snapshot.as_of}

미국 지표 등락 및 기술적 지표(Feature Engineering, 코드로 자동 계산됨):
{indices_text}{macro_text}
{regime_text}

위 데이터를 근거로 한국 KOSPI에 미칠 영향을 요약하세요."""

    return llm.complete_json(ANALYST_SYSTEM_PROMPT, user_prompt, max_tokens=600)
