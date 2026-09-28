from datetime import date
from data.news_search import parse_search_payload, build_query, NOISE_WORDS


PAYLOAD = {"items": [
    {"title": "<b>삼성전자</b>, HBM4 양산 앞당긴다…4분기 공급 &quot;확대&quot;", "originallink": "https://a.com/1",
     "link": "https://n.news.naver.com/1", "pubDate": "Mon, 28 Sep 2026 08:10:00 +0900"},
    {"title": "삼성 라이온즈, 야구 플레이오프 진출", "originallink": "https://a.com/2",
     "link": "", "pubDate": "Mon, 28 Sep 2026 07:00:00 +0900"},          # 종목명 없음 → 제외
    {"title": "삼성전자 임직원 봉사 활동", "originallink": "https://a.com/3",
     "link": "", "pubDate": "Mon, 28 Sep 2026 07:00:00 +0900"},          # 잡음 키워드 → 제외
    {"title": "삼성전자 실적 전망", "originallink": "https://a.com/4",
     "link": "", "pubDate": "Sat, 19 Sep 2026 07:00:00 +0900"},          # 기간 밖 → 제외
    {"title": "삼성전자 실적 전망(중복)", "originallink": "https://a.com/1",
     "link": "", "pubDate": "Sun, 27 Sep 2026 07:00:00 +0900"},          # 링크 중복 → 제외
    {"title": "삼성전자 파운드리 수주", "originallink": "https://b.com/5",
     "link": "", "pubDate": "Sun, 27 Sep 2026 21:00:00 +0900"},
]}


def test_parse_filters_and_cleans():
    items = parse_search_payload(PAYLOAD, "삼성전자", date(2026, 9, 26), date(2026, 9, 28))
    assert [i.title for i in items] == ['삼성전자, HBM4 양산 앞당긴다…4분기 공급 "확대"', "삼성전자 파운드리 수주"]
    assert items[0].date == "2026-09-28" and items[0].source == "a.com"


def test_query_and_noise():
    assert build_query("삼성전자") == "삼성전자 주가"
    assert build_query("현대차") == "현대차"
    assert "야구" in NOISE_WORDS


def test_specialists_falls_back_to_stock_when_no_keys(monkeypatch):
    from config import Config
    import agents.specialists as sp
    monkeypatch.setattr(Config, "NEWS_SOURCE", "auto")
    import data.news_search as ns
    monkeypatch.setattr(ns, "naver_search_available", lambda: False)
    import data.news_collector as nc
    monkeypatch.setattr(nc, "fetch_news_for_range", lambda *a, **k: [])
    assert sp.fetch_recent_news_titles(date(2026, 9, 28), universe=[{"code": "005930", "name": "삼성전자", "core": True}]) == []
    assert sp.LAST_NEWS_SOURCE["source"] == "naver_stock_mobile"


def test_hub_headers_used(monkeypatch):
    import data.news_search as ns
    captured = {}

    class R:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"items": []}

    def fake_get(url, params, headers, timeout):
        captured.update(url=url, headers=headers); return R()
    monkeypatch.setattr(ns.requests, "get", fake_get)
    ns.search_news("현대차", date(2026, 9, 26), date(2026, 9, 28), "ID", "SEC", sleep_sec=0)
    assert captured["url"].startswith("https://naverapihub.apigw.ntruss.com/")
    assert captured["headers"]["X-NCP-APIGW-API-KEY-ID"] == "ID"
    ns.search_news("현대차", date(2026, 9, 26), date(2026, 9, 28), "ID", "SEC", sleep_sec=0, mode="legacy")
    assert captured["headers"]["X-Naver-Client-Id"] == "ID"
