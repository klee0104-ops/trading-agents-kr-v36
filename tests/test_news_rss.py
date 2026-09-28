from datetime import date
from data.news_rss import parse_rss

XML = """<?xml version="1.0"?><rss version="2.0"><channel><title>x</title>
<item><title>삼성전자, HBM4 공급 확대 &quot;가속&quot; - 한국경제</title><link>https://a/1</link>
<pubDate>Mon, 28 Sep 2026 00:30:00 GMT</pubDate><source url="https://hankyung.com">한국경제</source></item>
<item><title>삼성 라이온즈 야구 승리 - 스포츠서울</title><link>https://a/2</link><pubDate>Mon, 28 Sep 2026 01:00:00 GMT</pubDate></item>
<item><title>삼성전자 실적 발표 - 매일경제</title><link>https://a/3</link><pubDate>Sat, 12 Sep 2026 01:00:00 GMT</pubDate></item>
<item><title>[속보] 삼성전자 파운드리 수주</title><link>https://a/4</link><pubDate>Sun, 27 Sep 2026 22:00:00 +0900</pubDate></item>
<item><title>삼성전자 파운드리 수주</title><link>https://a/4</link><pubDate>Sun, 27 Sep 2026 22:00:00 +0900</pubDate></item>
</channel></rss>"""


def test_parse_rss_filters_and_source_split():
    items = parse_rss(XML, "삼성전자", date(2026, 9, 26), date(2026, 9, 28))
    assert [i.title for i in items] == ['삼성전자, HBM4 공급 확대 "가속"', "[속보] 삼성전자 파운드리 수주"]
    assert items[0].source == "한국경제" and items[0].date == "2026-09-28" and items[0].time == "09:30"  # GMT→KST
    assert items[1].source == ""


def test_parse_rss_bad_xml():
    assert parse_rss("<not xml", "삼성전자", date(2026, 9, 1), date(2026, 9, 30)) == []
