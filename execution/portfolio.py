"""
Portfolio Optimizer. V3.5의 Execution Layer 컴포넌트: LLM 판단(신뢰도, 리스크 수준,
종목별 방향성 신호)을 입력으로 받아 현금/종목 비중을 순수 규칙으로 계산한다.
LLM은 여기 관여하지 않는다 — "판단은 이미 끝났고, 비중 계산은 코드"라는 원칙.
"""
from __future__ import annotations

# 신뢰도/리스크 수준별 주식 편입 비중표. 신뢰도가 낮거나 리스크가 높을수록 현금 비중을 높인다.
CONFIDENCE_EQUITY_WEIGHT = {"낮음": 0.3, "보통": 0.6, "높음": 0.85}
RISK_LEVEL_MULTIPLIER = {"낮음": 1.0, "보통": 0.85, "높음": 0.6}
SENSITIVITY_SCORE = {"high": 3, "mid": 2, "low": 1}


def optimize_portfolio(trader_result: dict, risk_level: str = "보통") -> dict:
    prediction = trader_result.get("kospi_prediction", {})
    confidence = prediction.get("confidence", "보통")
    direction = prediction.get("direction")

    equity_weight = round(
        CONFIDENCE_EQUITY_WEIGHT.get(confidence, 0.6) * RISK_LEVEL_MULTIPLIER.get(risk_level, 0.85),
        3,
    )
    cash_weight = round(1 - equity_weight, 3)

    stocks = trader_result.get("core_stocks", []) + trader_result.get("watch_stocks", [])
    aligned = [s for s in stocks if s.get("directional_signal") == direction]

    stock_weights: dict[str, float] = {}
    total_score = sum(SENSITIVITY_SCORE.get(s.get("sensitivity", "mid"), 2) for s in aligned)
    if total_score > 0:
        for s in aligned:
            score = SENSITIVITY_SCORE.get(s.get("sensitivity", "mid"), 2)
            stock_weights[s["name"]] = round(equity_weight * score / total_score, 4)

    return {
        "cash_weight": cash_weight,
        "equity_weight": equity_weight,
        "stock_weights": stock_weights,
    }
