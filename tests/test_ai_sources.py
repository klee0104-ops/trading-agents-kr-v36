"""(V3.36) 무료 AI·반도체 업계 소식: 공식 RSS·해커뉴스·레딧(키 있을 때)·SEC(연락처 설정 시), 커뮤니티 단독 승격 금지."""
import json
from datetime import datetime, timedelta, timezone

from agents.base import LLMClient
from data import ai_sources as ai
from data import breaking_news as bn

KST = timezone(timedelta(hours=9))
NOW = datetime(2026, 10, 23, 10, 0, tzinfo=KST)

RSS = b"""<?xml version="1.0"?><rss version="2.0"><channel><title>t</title>
<item><title>NVIDIA Unveils <b>Rubin</b> HBM4 platform</title><pubDate>Fri, 23 Oct 2026 00:30:00 GMT</pubDate></item>
<item><title>Old news about GPUs</title><pubDate>Mon, 01 Jun 2026 00:00:00 GMT</pubDate></item>
<item><title>Future dated item</title><pubDate>Sat, 24 Oct 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""


def test_official_feed_parses_recent_titles_only_and_strips_markup():
    rows = ai.parse_feed(RSS, 'NVIDIA Newsroom', NOW, timedelta(hours=24))
    assert [r['title'] for r in rows] == ['NVIDIA Unveils Rubin HBM4 platform']
    assert rows[0]['kind'] == 'official' and rows[0]['source'] == 'NVIDIA Newsroom (공식)'


def test_hn_requires_points_and_ai_relevance():
    ts = int((NOW - timedelta(hours=1)).timestamp())
    payload = json.dumps({'hits': [
        {'title': 'OpenAI releases new model', 'points': 400, 'created_at_i': ts},
        {'title': 'OpenAI minor thing', 'points': 20, 'created_at_i': ts},
        {'title': 'Rust 2.0 released', 'points': 900, 'created_at_i': ts}]}).encode()
    rows = ai.fetch_hn(NOW, get=lambda url: payload)
    assert [r['title'] for r in rows] == ['OpenAI releases new model'] and rows[0]['kind'] == 'community'


def test_reddit_and_sec_skip_without_keys_or_contact(monkeypatch):
    assert ai.fetch_reddit(NOW, token_factory=lambda: None) == []
    monkeypatch.delenv('SEC_USER_AGENT', raising=False)
    assert ai.fetch_sec(NOW, get=lambda *a, **k: (_ for _ in ()).throw(AssertionError('no call'))) == []


def test_news_titles_format_prefers_official():
    rows = [ai._item('HN rumor about nvidia', 'Hacker News (커뮤니티·미확인)', NOW, 'community'),
            ai._item('NVIDIA earnings', 'NVIDIA Newsroom (공식)', NOW - timedelta(hours=2), 'official')]
    titles = ai.as_news_titles(rows)
    assert titles[0].startswith('[2026-10-23][AI업계][NVIDIA Newsroom (공식)]')


class Reply(LLMClient):
    def __init__(self, text): self.text = text
    def complete(self, system, user, max_tokens=1500, use_web_search=False): return self.text


def _ai(title, kind, source):
    return ai._item(title, source, NOW - timedelta(minutes=20), kind)


def test_community_alone_is_not_promoted_but_confirmed_one_is(tmp_path):
    urgent_all = Reply(json.dumps({'items': [{'no': i, 'urgent': True, 'impact': 'up', 'summary': '요약'} for i in range(1, 6)]}))
    path = tmp_path / 'b.json'
    lone = [_ai('Rumor: OpenAI to buy a chip maker', 'community', 'Hacker News (커뮤니티·미확인)')]
    assert bn.run_once(path, NOW, fetch=lambda n: [], fetch_ai=lambda n: lone, client_factory=lambda: urgent_all) == []
    assert json.loads(path.read_text(encoding='utf-8'))['watch'][0]['reason'].startswith('커뮤니티 단독')
    path2 = tmp_path / 'c.json'
    both = [_ai('Nvidia stock jumps on Rubin demand', 'community', 'Reddit r/stocks (커뮤니티·미확인)'),
            _ai('NVIDIA announces Rubin shipments', 'official', 'NVIDIA Newsroom (공식)')]
    new = bn.run_once(path2, NOW, fetch=lambda n: [], fetch_ai=lambda n: both, client_factory=lambda: urgent_all)
    assert {i['kind'] for i in new} == {'community', 'official'}


def test_registry_adds_ai_industry_group_after_news_ids():
    from rules.citations import build_registry
    reg = build_registry(None, None, ['[2026-10-23][삼성전자][연합뉴스] 제목'], None,
                         ai_titles=['[2026-10-23][AI업계][NVIDIA Newsroom (공식)] NVIDIA Rubin'])
    n = [e for e in reg if e['kind'] == 'N']
    assert [e['id'] for e in n] == ['N1', 'N2'] and n[1]['group'] == 'ai_industry'


def test_reddit_keys_registered_and_semis_prompt_guards_community():
    from security.secret_store import KEY_NAMES
    from agents.specialists import SEMI_PROMPT
    assert KEY_NAMES['reddit_id'] == 'REDDIT_CLIENT_ID'
    assert '커뮤니티·미확인' in SEMI_PROMPT
