"""진단: 거시 지표(환율·금리·유가·달러)와 외국인 수급 조회가 되는지 (API 과금 없음)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from data.market_data import MarketDataProvider
s = MarketDataProvider().fetch_us_snapshot()
print("as_of", s.as_of)
for q in s.us_indices: print(" US ", q.name, q.value, q.change_pct)
for q in s.macro: print(" MACRO", q.name, q.value, q.change_pct, q.as_of)
print(" FLOW", s.kr_flow)
print(" NOTES", s.data_notes)
