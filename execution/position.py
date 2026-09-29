"""
(V3.14) 확률 → 포지션 → 수익률. 적중률이 아니라 "얼마나 걸어서 얼마를 벌었나"를 채점한다.

공식 출처
- 변동성 타게팅: Moreira & Muir (2017) "Volatility-Managed Portfolios", JF.
  비중 ∝ 목표변동성 / 실현변동성 — 변동성이 높을 때 노출을 줄이면 샤프가 개선된다는 결과.
- 확률 → 방향 강도(edge = 2p − 1): Kelly(1956) 이항 베팅의 최적 비율 f* = 2p − 1 (배당 1:1).
  전액 Kelly는 변동이 커서 실무는 1/2 Kelly 이하를 쓴다 → KELLY_FRACTION.
- 거래비용: 왕복 10bp 가정(ETF 스프레드+수수료 근사). 기대 edge가 비용보다 작으면 현금.

포지션 모델(사용자 선택): 롱 = KODEX 200(069500), 쇼트 = KODEX 인버스(114800), 그 외 현금.
ETF 실제 수익률을 못 받으면 KOSPI 지수 등락률로 근사한다(KOSPI200과 거의 같은 방향).
"""
from __future__ import annotations
import math
from typing import Optional, Sequence

TARGET_VOL_PCT = 15.0        # 연율 목표 변동성
MIN_EDGE = 0.10              # |2p-1| < 0.10 (p 45~55%) 이면 현금
KELLY_FRACTION = 0.5         # 하프 켈리
MAX_POSITION = 1.0           # 레버리지 없음
COST_BPS_ROUNDTRIP = 10.0    # 왕복 0.10%
LONG_ETF = "069500.KS"       # KODEX 200
SHORT_ETF = "114800.KS"      # KODEX 인버스


def vol_scale(realized_vol_pct: Optional[float], target_vol_pct: float = TARGET_VOL_PCT,
              lo: float = 0.25, hi: float = 1.5) -> float:
    """목표변동성/실현변동성. 실현변동성이 없으면 1.0."""
    try:
        v = float(realized_vol_pct) if realized_vol_pct is not None else None
    except (TypeError, ValueError):
        v = None
    if not v or v != v or v <= 0 or v == float("inf"):  # (V3.25) NaN/무한대/음수 → 스케일 1.0 (예전엔 NaN 이 1.5배로)
        return 1.0
    return max(lo, min(hi, target_vol_pct / v))


def position_from_probability(up_probability: Optional[float], realized_vol_pct: Optional[float] = None,
                              *, target_vol_pct: float = TARGET_VOL_PCT, min_edge: float = MIN_EDGE,
                              kelly_fraction: float = KELLY_FRACTION, max_position: float = MAX_POSITION) -> dict:
    """상승확률(0~100) → 포지션(-1~+1). 반환에 이유를 함께 넣어 대시보드에 보인다."""
    from rules.outlook import clamp_probability
    if clamp_probability(up_probability) is None:  # (V3.25) None/NaN/무한대/문자 → 현금
        return {"position": 0.0, "edge": None, "vol_scale": None, "instrument": "cash",
                "reason": "확률 없음 → 현금"}
    up_probability = float(up_probability)
    p = max(0.0, min(1.0, float(up_probability) / 100.0))
    edge = 2 * p - 1                      # Kelly f* (1:1 배당)
    vs = vol_scale(realized_vol_pct, target_vol_pct)
    if abs(edge) < min_edge:
        return {"position": 0.0, "edge": round(edge, 3), "vol_scale": round(vs, 3), "instrument": "cash",
                "reason": f"확률 {up_probability:.0f}% → edge {edge:+.2f} 가 기준 {min_edge} 미만 → 현금"}
    raw = edge * kelly_fraction * 2       # 하프 켈리를 |edge|=1 일 때 1.0 이 되도록 정규화
    pos = max(-max_position, min(max_position, raw * vs))
    pos = round(pos, 2)
    inst = LONG_ETF if pos > 0 else SHORT_ETF if pos < 0 else "cash"
    return {"position": pos, "edge": round(edge, 3), "vol_scale": round(vs, 3), "instrument": inst,
            "reason": f"확률 {up_probability:.0f}% → edge {edge:+.2f} × 변동성 스케일 {vs:.2f} → "
                      f"{'롱' if pos > 0 else '쇼트'} {abs(pos):.0%} ({'KODEX200' if pos > 0 else 'KODEX 인버스'})"}


def direction_position(direction: Optional[str], realized_vol_pct: Optional[float] = None,
                       size: float = 1.0) -> float:
    """방향만 있는 예측(베이스라인·분석가)을 같은 규칙의 포지션으로: ±size × 변동성 스케일(상한 1)."""
    if direction not in ("up", "down"):
        return 0.0
    pos = size * vol_scale(realized_vol_pct)
    pos = min(MAX_POSITION, pos)
    return round(pos if direction == "up" else -pos, 2)


def strategy_return_pct(position: float, index_change_pct: Optional[float], *,
                        long_etf_ret_pct: Optional[float] = None, short_etf_ret_pct: Optional[float] = None,
                        cost_bps_roundtrip: float = COST_BPS_ROUNDTRIP) -> Optional[dict]:
    """하루 전략 수익률(%). 롱은 KODEX200, 쇼트는 인버스 ETF 실제 수익률을 쓰고, 없으면 지수로 근사."""
    if index_change_pct is None and long_etf_ret_pct is None and short_etf_ret_pct is None:
        return None
    pos = float(position or 0.0)
    if pos == 0:
        return {"return_pct": 0.0, "gross_pct": 0.0, "cost_pct": 0.0, "leg": "cash", "proxy": False}
    if pos > 0:
        leg_ret = long_etf_ret_pct if long_etf_ret_pct is not None else index_change_pct
        proxy = long_etf_ret_pct is None
        gross = pos * float(leg_ret)
        leg = "long"
    else:
        if short_etf_ret_pct is not None:
            leg_ret, proxy = short_etf_ret_pct, False
        else:
            leg_ret, proxy = -float(index_change_pct), True
        gross = abs(pos) * float(leg_ret)
        leg = "short"
    cost = abs(pos) * cost_bps_roundtrip / 100.0
    return {"return_pct": round(gross - cost, 4), "gross_pct": round(gross, 4), "cost_pct": round(cost, 4),
            "leg": leg, "proxy": proxy}


def performance(returns_pct: Sequence[Optional[float]], positions: Optional[Sequence[float]] = None) -> dict:
    """일별 수익률(%) → 누적·연율 샤프·최대낙폭·승률·손익비·노출. 표본이 적을 땐 그대로 보고(주석 없이) 한다."""
    rets = [float(r) for r in returns_pct if r is not None]
    n = len(rets)
    if n == 0:
        return {"days": 0}
    eq, peak, mdd, curve = 1.0, 1.0, 0.0, []
    for r in rets:
        eq *= (1 + r / 100.0)
        curve.append(round((eq - 1) * 100, 3))
        peak = max(peak, eq)
        mdd = min(mdd, eq / peak - 1)
    mean = sum(rets) / n
    sd = math.sqrt(sum((r - mean) ** 2 for r in rets) / (n - 1)) if n > 1 else 0.0
    sharpe = (mean / sd * math.sqrt(252)) if sd > 0 else None
    traded = [r for r in rets if r != 0.0]
    wins = [r for r in traded if r > 0]
    losses = [-r for r in traded if r < 0]
    pf = (sum(wins) / sum(losses)) if losses else (None if not wins else float("inf"))
    out = {
        "days": n,
        "total_return_pct": round((eq - 1) * 100, 3),
        "mean_daily_pct": round(mean, 4),
        "daily_vol_pct": round(sd, 4),
        "sharpe_annual": round(sharpe, 2) if sharpe is not None else None,
        "max_drawdown_pct": round(mdd * 100, 3),
        "traded_days": len(traded),
        "win_rate": round(len(wins) / len(traded), 3) if traded else None,
        "profit_factor": (round(pf, 2) if pf not in (None, float("inf")) else pf),
        "equity_curve_pct": curve,
    }
    if positions is not None:
        pos = [abs(float(p or 0)) for p in positions]
        out["avg_exposure"] = round(sum(pos) / len(pos), 3) if pos else None
    return out
