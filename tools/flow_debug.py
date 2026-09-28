import requests, json
H = {"User-Agent": "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) AppleWebKit/605.1.15", "Referer": "https://m.stock.naver.com/", "Accept": "application/json"}
cands = [
 "https://m.stock.naver.com/api/index/KOSPI/investorTrend?pageSize=5&page=1",
 "https://m.stock.naver.com/api/index/KOSPI/trend?pageSize=5&page=1",
 "https://m.stock.naver.com/api/index/KOSPI/investor?pageSize=5&page=1",
 "https://m.stock.naver.com/api/index/KOSPI/investorDealTrend?pageSize=5&page=1",
 "https://m.stock.naver.com/api/index/KOSPI/price?pageSize=3&page=1",
 "https://m.stock.naver.com/api/stock/005930/trend?pageSize=3&page=1",
 "https://api.stock.naver.com/index/KOSPI/investorTrend?pageSize=5&page=1",
 "https://api.stock.naver.com/index/KOSPI/trend/investor?pageSize=5",
]
for u in cands:
    try:
        r = requests.get(u, headers=H, timeout=10)
        print(r.status_code, u, "|", r.text[:300].replace("\n", " "))
    except Exception as e:
        print("ERR", u, e)
