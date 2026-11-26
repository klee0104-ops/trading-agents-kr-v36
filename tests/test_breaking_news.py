"""(V3.35) 속보 긴급이슈: 규칙 1차·AI 2차·한도·만료·장중 재판단 앞당김·공개 필드."""
import json
from datetime import datetime, timedelta, timezone

from agents.base import LLMClient
from data import breaking_news as bn

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 23, 10, 0, tzinfo=KST)


def head(title, source='연합뉴스', minutes_ago=5):
    p = (NOW - timedelta(minutes=minutes_ago)).isoformat()
    return {'digest': bn.digest(title, source, p), 'title': title, 'source': source, 'published_at': p}


class Reply(LLMClient):
    def __init__(self, text):
        self.text, self.calls, self.user = text, 0, ''
    def complete(self, system, user, max_tokens=1500, use_web_search=False):
        self.calls += 1; self.user = user
        return self.text


def test_rule_requires_urgent_tag_or_high_impact_and_market_relevance():
    assert bn.rule_score('[속보] 코스피 3% 급락, 사이드카 발동', '연합뉴스')['candidate']
    assert bn.rule_score('한은 기준금리 0.5%p 긴급 인하', '뉴스1')['candidate']
    assert not bn.rule_score('[속보] 유명 배우 결혼 발표', '스포츠지')['candidate']
    assert not bn.rule_score('코스피 하반기 전망 칼럼', '연합뉴스')['candidate']
    assert bn.rule_score('BREAKING: Federal Reserve announces emergency rate cut', 'Reuters')['candidate']
    assert not bn.rule_score('Breaking: actor announces new film', 'AP News')['candidate']
    assert not bn.rule_score('금리 인상 그림자 속 미 증시 급락 회고', 'PANews')['candidate']


def test_fetch_headlines_uses_unfiltered_kr_and_us_feeds(monkeypatch):
    from data import news_rss
    from data.news_collector import NewsItem

    calls = []
    def fake_fetch(name, start, end, **kwargs):
        calls.append((name, kwargs['locale'], kwargs['require_name']))
        return [NewsItem(date='2026-10-23', time='09:58', title='Breaking: Federal Reserve emergency rate cut',
                         link='https://example.org/one', source='Reuters')]
    monkeypatch.setattr(news_rss, 'fetch_google_news', fake_fetch)
    rows = bn.fetch_headlines(NOW)
    assert len(rows) == 1
    assert {locale for _, locale, _ in calls} == {'KR', 'US'}
    assert all(name == '' and required is False for name, _, required in calls)


def test_breaking_check_runs_after_krx_close_and_weekend(tmp_path, monkeypatch):
    from config import Config
    from tools import short_intraday_review as watcher
    monkeypatch.setattr(Config, 'BREAKING_NEWS_PATH', str(tmp_path / 'breaking.json'))
    monkeypatch.setattr(Config, 'BREAKING_NEWS_ENABLED', True)
    calls = []
    monkeypatch.setattr(bn, 'run_once', lambda path, now, client_factory=None: calls.append(now) or [])
    assert watcher.run_breaking_check(NOW.replace(hour=22), client_factory=lambda: None) == 'checked'
    assert watcher.run_breaking_check(NOW + timedelta(days=1), client_factory=lambda: None) == 'checked'
    assert len(calls) == 2


def test_breaking_news_publishes_only_public_banner(tmp_path, monkeypatch):
    from security import public_report
    from web.export_site import render_fragment
    from tools.publish_breaking_news import publish_issues
    monkeypatch.setattr(public_report, 'configured_secrets', lambda: set())
    (tmp_path / 'site').mkdir()
    (tmp_path / 'sharing_public.json').write_text('{"enabled":true}', encoding='utf-8')
    original = ('<!doctype html><html lang="ko"><head></head><body>' +
                render_fragment({'prediction': {}, 'urgent_issues': []}) + '</body></html>')
    (tmp_path / 'site' / 'index.html').write_text(original, encoding='utf-8')
    issue = {'title': 'Breaking: Federal Reserve emergency rate cut', 'source': 'Reuters',
             'published_at': NOW.isoformat(), 'detected_at': NOW.isoformat(),
             'expires_at': (NOW + timedelta(hours=3)).isoformat(),
             'impact': 'up', 'summary': 'Federal Reserve rate cut'}
    published = []
    assert publish_issues([issue], tmp_path, publisher=lambda path: published.append(path) or {'changed': True}) == 'published'
    assert len(published) == 1
    parser = public_report.harden_report((tmp_path / 'site' / 'index.html').read_text(encoding='utf-8'), set())
    assert 'Federal Reserve rate cut' in parser
    assert publish_issues([issue], tmp_path, publisher=lambda path: published.append(path) or {'changed': True}) == 'unchanged'
    assert len(published) == 1


def test_ai_confirms_by_number_and_issue_is_stored_with_review_request(tmp_path):
    path = tmp_path / 'b.json'
    heads = [head('[속보] 코스피 3% 급락, 사이드카 발동'), head('[속보] 원·달러 환율 급등 1,400원 돌파')]
    llm = Reply(json.dumps({'items': [{'no': 1, 'urgent': True, 'impact': 'down', 'summary': '코스피 급락·사이드카'},
                                      {'no': 2, 'urgent': 'false', 'impact': 'down', 'summary': 'x'}]}, ensure_ascii=False))
    new = bn.run_once(path, NOW, fetch=lambda now: heads, client_factory=lambda: llm)
    assert llm.calls == 1 and len(new) == 1 and new[0]['impact'] == 'down'
    assert '(1)' in llm.user and '신뢰할 수 없는' in llm.user
    pending = bn.take_pending_review(path, NOW + timedelta(minutes=1))
    assert pending and pending['id'] == new[0]['id']
    assert bn.take_pending_review(path, NOW + timedelta(minutes=2)) is None  # 한 번만 소비
    # 같은 제목은 다시 판단하지 않음
    assert bn.run_once(path, NOW + timedelta(minutes=15), fetch=lambda now: heads, client_factory=lambda: llm) == []
    assert llm.calls == 1


def test_broken_ai_reply_promotes_nothing(tmp_path):
    path = tmp_path / 'b.json'
    new = bn.run_once(path, NOW, fetch=lambda now: [head('[속보] 코스피 급락 서킷브레이커')],
                      client_factory=lambda: Reply('설명만 있고 JSON 없음'))
    assert new == [] and bn.public_issues(path, NOW) == []


def test_daily_ai_limit_and_expiry(tmp_path):
    path = tmp_path / 'b.json'
    llm = Reply(json.dumps({'items': [{'no': 1, 'urgent': True, 'impact': 'up', 'summary': '금리 인하'}]}, ensure_ascii=False))
    for i in range(bn.MAX_AI_CALLS_PER_DAY + 3):
        bn.run_once(path, NOW + timedelta(minutes=i), fetch=lambda now, i=i: [head(f'[속보] 기준금리 인하 {i}차 코스피')],
                    client_factory=lambda: llm)
    assert llm.calls == bn.MAX_AI_CALLS_PER_DAY
    assert len(bn.public_issues(path, NOW + timedelta(minutes=20))) == 3
    assert bn.public_issues(path, NOW + bn.ISSUE_TTL + timedelta(hours=1)) == []


def test_public_fields_are_limited_and_markup_removed(tmp_path):
    path = tmp_path / 'b.json'
    llm = Reply(json.dumps({'items': [{'no': 1, 'urgent': True, 'impact': 'weird', 'summary': '<script>x</script>요약'}]}))
    bn.run_once(path, NOW, fetch=lambda now: [head('[속보] <b>코스피</b> 급락')], client_factory=lambda: llm)
    row = bn.public_issues(path, NOW)[0]
    assert set(row) == {'title', 'source', 'published_at', 'detected_at', 'expires_at', 'impact', 'summary', 'kind'}
    assert '<' not in row['title'] + row['summary'] and row['impact'] == 'unclear'
    from security.public_report import check_data
    check_data({'urgent_issues': [row]}, [])


def test_intraday_review_skips_cooldown_for_urgent_issue_but_keeps_daily_limit():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / 'tools/short_intraday_review.py').read_text(encoding='utf-8')
    assert "trigger='urgent_news'" in src and "and trigger!='urgent_news'" in src
    assert "state['attempts']<MAX_REVIEWS_PER_DAY" in src and 'URGENT_MIN_GAP' in src
