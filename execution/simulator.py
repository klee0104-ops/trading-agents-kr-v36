"""
Execution Simulator. V3.5 설계상 "실제 종가 반영"을 담당하는 컴포넌트.

초반 단계 한계: 종목별 실시간 체결가 파이프라인이 아직 없어서(Phase 2+ 항목),
개별 종목 체결 대신 equity_weight 전체가 KOSPI 지수 등락률에 그대로 노출된
것으로 근사한다. 종목별 실제 체결 시뮬레이션은 종목별 가격 데이터가 갖춰진 뒤
고도화한다. 현금 비중은 수익률 0으로 취급한다(예금 이자 등 미반영).
"""
from __future__ import annotations


def simulate_day(portfolio: dict, kospi_change_pct: float) -> float:
    """kospi_change_pct는 퍼센트 단위(예: +1.2)로 주어진다. 반환값은 소수 수익률(예: 0.0102)."""
    equity_weight = portfolio.get("equity_weight", 0.0)
    return equity_weight * (kospi_change_pct / 100.0)
