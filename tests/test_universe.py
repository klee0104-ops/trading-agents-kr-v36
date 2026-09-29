"""(V3.10) 뉴스 유니버스: 시총 상위에서 핵심·우선주 제외 N개 선정, 제목 수집 형식."""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from data.universe import select_news_universe, _is_preferred, CORE_CODES


def _rows():
    return [{"code": "005930", "name": "삼성전자"}, {"code": "000660", "name": "SK하이닉스"},
            {"code": "005935", "name": "삼성전자우"}, {"code": "373220", "name": "LG에너지솔루션"},
            {"code": "207940", "name": "삼성바이오로직스"}, {"code": "005380", "name": "현대차"},
            {"code": "005387", "name": "현대차2우B"}, {"code": "000270", "name": "기아"}]


def test_select_excludes_core_and_preferred_and_orders_core_first():
    from data.universe import select_watch_universe
    w = select_watch_universe(_rows(), n=3, seed_date=date(2026, 9, 28), mode="topcap")
    assert [x["name"] for x in w] == ["LG에너지솔루션", "삼성바이오로직스", "현대차"]
    u = select_news_universe(_rows(), watch=w)
    assert [x["name"] for x in u][:2] == ["삼성전자", "SK하이닉스"] and u[0]["core"] and not u[2]["core"]
    assert _is_preferred("현대차2우B") and not _is_preferred("기아")


def test_random_pick_is_seeded_by_date_and_changes_daily():
    from data.universe import select_watch_universe
    rows = [{"code": f"{i:06d}", "name": f"종목{i}"} for i in range(1, 101)]
    a = select_watch_universe(rows, n=10, seed_date=date(2026, 9, 28))
    b = select_watch_universe(rows, n=10, seed_date=date(2026, 9, 28))
    c = select_watch_universe(rows, n=10, seed_date=date(2026, 9, 29))
    assert a == b and a != c and len(a) == 10
    # stratified: 상위 30에서 5, 31~100에서 5
    assert sum(1 for x in a if x["rank"] <= 30) == 5
    r = select_watch_universe(rows, n=10, seed_date=date(2026, 9, 28), mode="random")
    assert len({x["code"] for x in r}) == 10


def test_fetch_titles_groups_by_stock_and_caps(monkeypatch):
    from agents import specialists
    from data.news_collector import NewsItem

    def fake_fetch(code, start, end, max_pages=1, **kw):
        return [NewsItem("2026-09-28", "09:00", f"{code} 뉴스 {i}", "http://x", "src") for i in range(20)]
    monkeypatch.setattr("data.news_collector.fetch_news_for_range", fake_fetch)
    uni = [{"code": "005930", "name": "삼성전자", "core": True}, {"code": "005380", "name": "현대차", "core": False}]
    titles = specialists.fetch_recent_news_titles(date(2026, 9, 28), universe=uni)
    assert titles[0].startswith("[2026-09-28][삼성전자][src] ")  # (V3.24) 세 번째 칸 = 매체
    assert sum(1 for t in titles if "[삼성전자]" in t) == 15 and sum(1 for t in titles if "[현대차]" in t) == 6


def test_parse_market_value_payload_sorts_by_marcap():
    from data.universe import parse_market_value_payload
    p = {"stocks": [{"itemCode": "5930", "stockName": "삼성전자", "marketValue": "15,887,262", "stockEndType": "stock"},
                    {"itemCode": "000660", "stockName": "SK하이닉스", "marketValue": "9,000,000", "stockEndType": "stock"},
                    {"itemCode": "999999", "stockName": "어떤ETF", "marketValue": "99,999,999", "stockEndType": "etf"}]}
    rows = parse_market_value_payload(p)
    assert [r["code"] for r in rows] == ["005930", "000660"] and rows[0]["marcap"] == 15887262.0


def test_parse_news_payload_from_mobile_api():
    from data.news_collector import parse_news_payload
    payload = [{"total": 1, "items": [{"officeId": "016", "articleId": "0002702594", "officeName": "헤럴드경제",
                                        "datetime": "202609281211", "title": "짧은 제목...", "titleFull": "전체 &quot;제목&quot;",
                                        "mobileNewsUrl": "https://n.news.naver.com/mnews/article/016/0002702594"}]},
               {"total": 1, "items": [{"officeId": "001", "articleId": "1", "officeName": "연합", "datetime": "bad"}]}]
    items = parse_news_payload(payload)
    assert len(items) == 1 and items[0].date == "2026-09-28" and items[0].time == "12:11"
    assert items[0].title == '전체 "제목"' and items[0].source == "헤럴드경제"
