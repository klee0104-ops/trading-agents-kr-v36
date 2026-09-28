"""
Rule Engine + Market Regime Detection. V3.5 설계 원칙("계산 가능한 것은 전부 코드")에
따라 판단이 필요없는 임계값 기반 리스크 신호와 장세 분류를 LLM 없이 코드로 산출한다.

이 모듈의 출력(MarketAnalysis)은:
1. MarketAnalyst 프롬프트에 "이미 계산된 사실"로 주입되어 LLM이 다시 계산하지 않게 하고,
2. Verification Agent가 Risk Manager의 LLM 판단과 상충하는지 검사하는 기준값으로 쓰인다.
"""
from __future__ import annotations
from dataclasses import dataclass, field

from data.market_data import MarketSnapshot

RISK_ORDER = {"낮음": 0, "보통": 1, "높음": 2}

# VIX 절대 수준 임계값 (변화율이 아니라 그 자체 수준으로 판단하는 게 관례적으로 맞다)
VIX_HIGH_LEVEL = 30.0
VIX_LOW_LEVEL = 15.0
VIX_SPIKE_CHANGE_PCT = 10.0

US_SELLOFF_CHANGE_PCT = -2.0
US_RALLY_CHANGE_PCT = 2.0

RSI_OVERBOUGHT = 70.0
RSI_OVERSOLD = 30.0

BROAD_DECLINE_RATIO = 0.75
# (V3.6.2) "광범위한 약세"는 하락 종목 수만이 아니라 하락 폭도 의미 있어야 한다.
# -0.02% 같은 보합 수준까지 "하락"으로 세면 거의 매일 리스크 '높음'이 떠서 예측이 하락 쪽으로 쏠렸다.
BROAD_DECLINE_MIN_AVG_PCT = -0.5   # 비VIX 지수 평균 등락률이 이 값 이하일 때만 플래그
BROAD_DECLINE_MIN_EACH_PCT = -0.2  # "하락"으로 셀 최소 낙폭 (그보다 작은 낙폭은 보합 취급)

# SOX(필라델피아 반도체)는 KOSPI(삼성전자·SK하이닉스 비중)의 핵심 선행지표.
# 방향 힌트는 리스크(불확실성)와 별도로 산출한다.
SOX_STRONG_PCT = 1.5
SOX_CRASH_PCT = -3.0

# (V3.7) 거시 지표 임계값
KRW_WEAK_PCT = 0.7        # 원/달러 하루 +0.7% 이상 = 원화 급약세
KRW_STRONG_PCT = -0.7
RATES_SPIKE_PCT = 3.0     # 미국 10년 금리 지수(^TNX) 하루 +3% 이상
OIL_SPIKE_PCT = 3.0
DXY_STRONG_PCT = 0.5


@dataclass
class MarketAnalysis:
    regime_trend: str  # "상승" | "하락" | "횡보"
    regime_volatility: str  # "높음" | "보통" | "낮음"
    risk_flags: list[str] = field(default_factory=list)
    risk_level_hint: str = "보통"  # "낮음" | "보통" | "높음" — 전부 규칙 기반 (불확실성, 방향 아님)
    notes: list[str] = field(default_factory=list)
    # (V3.6.2) 방향 힌트: SOX 중심. "up" | "down" | None(중립). 리스크와 독립.
    direction_hint: str | None = None
    direction_hint_reason: str = ""
    macro_summary: str = ""  # (V3.7) 환율·금리·유가·달러·외국인 수급 한 줄 요약
    quant: dict | None = None  # (V3.14) rules.quant.compute_indicators 결과 (RSI·MACD·변동성·모멘텀 …)


def analyze_market(snapshot: MarketSnapshot) -> MarketAnalysis:
    quotes = {q.name: q for q in snapshot.us_indices}
    flags: list[str] = []
    notes: list[str] = []

    volatility = "보통"
    vix = quotes.get("VIX")
    if vix is not None:
        if vix.value is not None and vix.value >= VIX_HIGH_LEVEL:
            flags.append("VIX_HIGH_LEVEL")
            volatility = "높음"
            notes.append(f"VIX 절대 수준 {vix.value:.1f} (임계값 {VIX_HIGH_LEVEL} 이상, 공포 구간)")
        elif vix.value is not None and vix.value <= VIX_LOW_LEVEL:
            volatility = "낮음"

        if vix.change_pct >= VIX_SPIKE_CHANGE_PCT:
            flags.append("VIX_SPIKE")
            notes.append(f"VIX 급등 {vix.change_pct:+.2f}% (임계값 {VIX_SPIKE_CHANGE_PCT}% 이상)")

    trend = "횡보"
    sp500 = quotes.get("S&P500")
    if sp500 is not None:
        if sp500.change_pct <= US_SELLOFF_CHANGE_PCT:
            flags.append("US_SELLOFF")
            trend = "하락"
            notes.append(f"S&P500 급락 {sp500.change_pct:+.2f}% (임계값 {US_SELLOFF_CHANGE_PCT}% 이하)")
        elif sp500.change_pct >= US_RALLY_CHANGE_PCT:
            trend = "상승"
            notes.append(f"S&P500 급등 {sp500.change_pct:+.2f}% (임계값 {US_RALLY_CHANGE_PCT}% 이상)")

        if sp500.rsi14 is not None:
            if sp500.rsi14 >= RSI_OVERBOUGHT:
                flags.append("SP500_OVERBOUGHT")
                notes.append(f"S&P500 RSI14 {sp500.rsi14} (과매수 {RSI_OVERBOUGHT} 이상)")
            elif sp500.rsi14 <= RSI_OVERSOLD:
                flags.append("SP500_OVERSOLD")
                notes.append(f"S&P500 RSI14 {sp500.rsi14} (과매도 {RSI_OVERSOLD} 이하)")

    non_vix = [q for q in snapshot.us_indices if q.name != "VIX"]
    if non_vix:
        down_count = sum(1 for q in non_vix if q.change_pct <= BROAD_DECLINE_MIN_EACH_PCT)
        avg_pct = sum(q.change_pct for q in non_vix) / len(non_vix)
        if down_count / len(non_vix) >= BROAD_DECLINE_RATIO and avg_pct <= BROAD_DECLINE_MIN_AVG_PCT:
            flags.append("BROAD_BASED_DECLINE")
            notes.append(f"주요 지수 {down_count}/{len(non_vix)}개 의미 있는 하락, 평균 {avg_pct:+.2f}% (광범위한 약세)")

    # SOX 방향 힌트 (리스크와 분리)
    sox = quotes.get("SOX")
    direction_hint = None
    direction_reason = ""
    if sox is not None:
        if sox.change_pct >= SOX_STRONG_PCT:
            flags.append("SOX_STRONG_UP")
            direction_hint, direction_reason = "up", f"SOX {sox.change_pct:+.2f}% 강세 (임계 {SOX_STRONG_PCT}% 이상)"
        elif sox.change_pct <= SOX_CRASH_PCT:
            flags.append("SOX_CRASH")
            direction_hint, direction_reason = "down", f"SOX {sox.change_pct:+.2f}% 급락 (임계 {SOX_CRASH_PCT}% 이하)"
        elif sox.change_pct <= -SOX_STRONG_PCT:
            direction_hint, direction_reason = "down", f"SOX {sox.change_pct:+.2f}% 약세"
        elif abs(sox.change_pct) >= 0.5:
            direction_hint = sox.direction
            direction_reason = f"SOX {sox.change_pct:+.2f}%"
        if direction_reason:
            notes.append("방향 힌트: " + direction_reason)

    # (V3.7) 거시 지표 — 외국인 수급 환경. 리스크(불확실성)에는 '보통'까지만 기여한다.
    m = {q.name: q for q in (snapshot.macro or [])}
    krw = m.get("원/달러")
    if krw is not None:
        if krw.change_pct >= KRW_WEAK_PCT:
            flags.append("KRW_WEAK")
            notes.append(f"원/달러 {krw.change_pct:+.2f}% 급등(원화 약세, 외국인 매도 압력)")
        elif krw.change_pct <= KRW_STRONG_PCT:
            flags.append("KRW_STRONG")
            notes.append(f"원/달러 {krw.change_pct:+.2f}% 하락(원화 강세, 외국인 유입 우호)")
    tnx = m.get("미국10년금리")
    if tnx is not None and tnx.change_pct >= RATES_SPIKE_PCT:
        flags.append("RATES_SPIKE")
        notes.append(f"미국 10년 금리 {tnx.change_pct:+.2f}% 급등(위험자산 부담)")
    oil = m.get("WTI유가")
    if oil is not None and oil.change_pct >= OIL_SPIKE_PCT:
        flags.append("OIL_SPIKE")
        notes.append(f"WTI {oil.change_pct:+.2f}% 급등")
    dxy = m.get("달러인덱스")
    if dxy is not None and dxy.change_pct >= DXY_STRONG_PCT:
        flags.append("DXY_STRONG")
        notes.append(f"달러인덱스 {dxy.change_pct:+.2f}% 강세(신흥국 자금 유출 우려)")
    flow = snapshot.kr_flow
    if flow and flow.get("strong"):
        flags.append("FOREIGN_BIG_BUY" if flow["direction"] == "buy" else "FOREIGN_BIG_SELL")
        notes.append(f"외국인 대규모 {'순매수' if flow['direction'] == 'buy' else '순매도'} {abs(flow['foreign_net_krw_bn']):,.0f}억원")

    # 리스크 = 불확실성. 방향(상승/하락)과 별개다.
    if any(f in flags for f in ("VIX_HIGH_LEVEL", "US_SELLOFF", "BROAD_BASED_DECLINE", "SOX_CRASH")):
        risk_level = "높음"
    elif any(f in flags for f in ("VIX_SPIKE", "SP500_OVERBOUGHT", "SP500_OVERSOLD",
                                  "KRW_WEAK", "RATES_SPIKE", "DXY_STRONG")):
        risk_level = "보통"
    else:
        risk_level = "낮음"

    return MarketAnalysis(
        regime_trend=trend,
        regime_volatility=volatility,
        risk_flags=flags,
        risk_level_hint=risk_level,
        notes=notes,
        direction_hint=direction_hint,
        direction_hint_reason=direction_reason,
        macro_summary=macro_summary_text(snapshot),
    )


def macro_summary_text(snapshot: MarketSnapshot) -> str:
    parts = [f"{q.name} {q.value} ({q.change_pct:+.2f}%)" if q.value is not None else f"{q.name} {q.change_pct:+.2f}%"
             for q in (snapshot.macro or [])]
    from data.kr_flow import describe
    if snapshot.kr_flow:
        parts.append(describe(snapshot.kr_flow))
    for n in (snapshot.data_notes or []):
        parts.append(f"[{n}]")
    return ", ".join(parts)


def build_context_text(analysis: MarketAnalysis) -> str:
    """MarketAnalyst 프롬프트에 주입할 텍스트. LLM이 재계산할 필요 없는 사실만 전달한다."""
    lines = [
        "\n[Rule Engine / Market Regime Detection — 코드로 자동 산출, LLM 재계산 불필요]",
        f"장세 분류: 추세={analysis.regime_trend}, 변동성={analysis.regime_volatility}",
        f"규칙엔진 리스크 힌트: {analysis.risk_level_hint} "
        "(※ 리스크는 '불확실성'의 크기이지 방향이 아닙니다. 리스크가 높다고 하락을 뜻하지 않으며, "
        "확신도를 낮추는 근거로만 쓰세요)",
        f"규칙엔진 방향 힌트(SOX 기준): {analysis.direction_hint or '중립'}"
        + (f" — {analysis.direction_hint_reason}" if analysis.direction_hint_reason else ""),
        "참고: KOSPI는 삼성전자·SK하이닉스 비중이 커서 미국 지수 중 SOX(반도체)와 방향 일치율이 가장 높습니다.",
    ]
    if analysis.notes:
        lines.append("근거: " + "; ".join(analysis.notes))
    if analysis.macro_summary:
        lines.append("거시 지표: " + analysis.macro_summary)
    if not analysis.risk_flags:
        lines.append("트리거된 규칙 없음 (정상 범위)")
    if analysis.quant:
        from rules.quant import quant_text
        qt = quant_text(analysis.quant)
        if qt:
            lines.append(qt)
    lines.append("※ 채점은 적중률이 아니라 수익률입니다: 상승확률 45~55%는 현금(무포지션)으로 처리되고, "
                 "확률이 50에서 멀수록 포지션이 커집니다(변동성이 높으면 축소). 근거가 약하면 50 근처를, "
                 "근거가 강하면 과감한 확률을 내세요. 틀렸을 때의 손실도 같은 크기로 채점됩니다.")
    return "\n".join(lines)
