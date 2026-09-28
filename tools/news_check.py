"""진단: 오늘 추첨 관심 종목 + 종목별 사실(가격·뉴스) 수집 (LLM 미사용)."""
import os, sys, time
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
import config
from datetime import date
from data.universe import select_watch_universe, select_news_universe
from agents.specialists import fetch_recent_news_titles, LAST_NEWS_SOURCE
from data.stock_features import build_stock_facts, facts_text
t0 = time.time()
w = select_watch_universe(seed_date=date.today())
uni = select_news_universe(watch=w)
titles = fetch_recent_news_titles(date.today(), universe=uni)
facts = build_stock_facts(uni, date.today(), titles)
print(f"뉴스 {len(titles)}건, {time.time()-t0:.0f}s, 출처: {LAST_NEWS_SOURCE['source']} "
      f"({ {'naver_search_api':'네이버 검색 API(공식)','rss':'Google 뉴스 RSS + 언론사 RSS (키 불필요)'}.get(LAST_NEWS_SOURCE['source'],'증권 모바일 API(비공개, 폴백)') })")
for t in titles[:15]: print("  ", t)
print(facts_text(facts))
