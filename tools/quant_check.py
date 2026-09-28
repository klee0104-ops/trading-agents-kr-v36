"""진단: 오늘 기준 KOSPI 정량 지표 + 확률별 포지션 예시 (LLM 미사용, yfinance만)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config  # noqa: F401  (마스킹 설치)
from datetime import date
from data.market_data import MarketDataProvider
from rules.quant import compute_indicators, quant_text
from execution.position import position_from_probability

hist = MarketDataProvider().fetch_kospi_history(date.today())
ind = compute_indicators(hist["closes"], hist["highs"], hist["lows"])
print(f"데이터: {hist['dates'][0]} ~ {hist['dates'][-1]} ({len(hist['closes'])}일)")
print(quant_text(ind))
print("\n확률 → 포지션 (오늘 실현변동성 기준):")
for p in (35, 45, 55, 62, 70, 80):
    print(f"  {p}% → {position_from_probability(p, ind.get('realized_vol_20'))['reason']}")
