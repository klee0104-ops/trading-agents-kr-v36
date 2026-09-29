import os, sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date, timedelta
import yfinance as yf
print("yf", yf.__version__)
for per in (("start", date(2026,9,14), date(2026,9,30)), ("start", date(2026,6,1), date(2026,9,30))):
    h = yf.download("^KS11", start=per[1], end=per[2], progress=False)
    print("yf", per[1], "->", [str(d.date()) for d in h.index[-4:]], [round(float(x),2) for x in h["Close"].squeeze().tolist()[-4:]])
h = yf.Ticker("^KS11").history(period="10d")
print("yf history", [str(d.date()) for d in h.index[-4:]], [round(float(x),2) for x in h["Close"].tolist()[-4:]])
try:
    import FinanceDataReader as fdr
    d = fdr.DataReader("KS11", "2026-09-14")
    print("fdr", [str(x.date()) for x in d.index[-4:]], d["Close"].tolist()[-4:])
except Exception as e:
    print("fdr ERR", e)
try:
    import requests
    r = requests.get("https://m.stock.naver.com/api/index/KOSPI/price", params={"pageSize": 5, "page": 1},
                     headers={"User-Agent": "Mozilla/5.0"}, timeout=10)
    print("naver", r.status_code, r.text[:400])
except Exception as e:
    print("naver ERR", e)
