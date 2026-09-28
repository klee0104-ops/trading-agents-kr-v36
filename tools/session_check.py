"""진단용: 최근 날짜 기준 "다음 실제 거래일" 판정과 시세 조회가 되는지 확인 (API 과금 없음)."""
import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
import config
from datetime import date
from data.market_data import MarketDataProvider as M
m=M()
for d in [date.today() - timedelta(days=k) for k in (7, 5, 3)]:
    try: print(d, m.fetch_kospi_session_after(d))
    except Exception as e: print(d,"ERR",e)
try: print(m.fetch_stock_actual("삼성전자", None, after_date=date.today() - timedelta(days=7)))
except Exception as e: print("stockERR",e)
