"""(V3.36) 무료 AI·반도체 업계 소식 수집 — 공식 발표 RSS · 해커뉴스 · 레딧(키 있을 때) · 미국 공시(연락처 설정 시).

- 모두 신뢰할 수 없는 외부 데이터: 제목만, 길이 제한·마크업 제거, 본문 미저장.
- kind: official(기업 공식 발표, 가장 신뢰) / filing(미국 SEC 8-K) / community(해커뉴스·레딧, 미확인).
- 커뮤니티 글은 단독으로 긴급이슈가 되지 않는다(breaking_news 에서 공식·언론 교차 확인 필요).
- 한 출처가 실패해도 나머지는 계속한다. 응답 크기 상한 2MB.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from email.utils import parsedate_to_datetime
from hashlib import sha256
import os
import re
from xml.etree import ElementTree as ET

import requests

UA = {'User-Agent': 'Mozilla/5.0 (TradingAgents-KR research reader; non-commercial)'}
MAX_BYTES = 2_000_000
OFFICIAL_FEEDS = {
    'OpenAI News': 'https://openai.com/news/rss.xml',
    'NVIDIA Newsroom': 'https://nvidianews.nvidia.com/releases.xml',
    'Google DeepMind': 'https://deepmind.google/blog/rss.xml',
    'Microsoft Blog': 'https://blogs.microsoft.com/feed/',
}
SUBREDDITS = ('MachineLearning', 'LocalLLaMA', 'singularity', 'hardware', 'stocks')
SEC_COMPANIES = {  # 반도체·AI 대형주 (CIK)
    'NVIDIA': 1045810, 'Micron': 723125, 'AMD': 2488, 'Broadcom': 1730168,
    'Microsoft': 789019, 'Alphabet': 1652044, 'Meta': 1326801, 'Apple': 320193,
}
AI_WORDS = ('openai', 'chatgpt', 'gpt-', 'gpt5', 'gpt 5', 'anthropic', 'claude', 'gemini', 'deepmind', 'llama', 'grok',
            'nvidia', 'blackwell', 'rubin', 'cuda', 'hbm', 'tsmc', 'micron', 'samsung', 'sk hynix', 'hynix',
            'semiconductor', 'chip', 'gpu', 'data center', 'datacenter', 'export control', 'tariff', 'ai model',
            'artificial intelligence', 'agi', 'earnings', 'guidance', 'acquisition', 'acquire', 'capex')
HN_MIN_POINTS = 150
REDDIT_MIN_SCORE = 800


def clean(text, limit=200):
    t = re.sub(r'<[^>]*>', '', str(text or ''))
    t = re.sub(r'(?i)(javascript|vbscript)\s*:', '', t)
    t = re.sub(r'[\x00-\x1f\x7f]+', ' ', t)
    return re.sub(r'\s+', ' ', t).strip()[:limit]


def ai_related(title):
    t = str(title or '').lower()
    return [w for w in AI_WORDS if w in t]


def _item(title, source, published, kind, score=None):
    title, source = clean(title), clean(source, 60)
    p = published.isoformat()
    return {'digest': sha256((p + source + title).encode('utf-8')).hexdigest()[:20], 'title': title,
            'source': source, 'published_at': p, 'kind': kind, 'score': score}


def _get(url, **kw):
    r = requests.get(url, headers=kw.pop('headers', UA), timeout=kw.pop('timeout', 12), stream=True,
                     allow_redirects=False, **kw)
    if r.status_code != 200:
        raise ValueError('http')
    body = r.raw.read(MAX_BYTES + 1, decode_content=True)
    if len(body) > MAX_BYTES:
        raise ValueError('too_large')
    return body


def parse_feed(xml_bytes, source, now, max_age):
    """RSS 2.0 / Atom 제목·시각만. 오래된 글·미래 시각 글은 버린다."""
    try:
        root = ET.fromstring(xml_bytes)
    except ET.ParseError:
        return []
    out = []
    atom = '{http://www.w3.org/2005/Atom}'
    nodes = list(root.iter('item')) or list(root.iter(atom + 'entry'))
    for it in nodes[:40]:
        node = it.find('title')
        if node is None:
            node = it.find(atom + 'title')
        title = ''.join(node.itertext()) if node is not None else ''  # 제목 안에 태그가 섞여 와도 전체 텍스트
        raw = it.findtext('pubDate') or it.findtext(atom + 'updated') or it.findtext(atom + 'published')
        try:
            dt = parsedate_to_datetime(raw) if raw and ',' in raw else datetime.fromisoformat(str(raw).replace('Z', '+00:00'))
        except (TypeError, ValueError):
            continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        dt = dt.astimezone(now.tzinfo)
        if not title or not timedelta(minutes=-5) <= now - dt <= max_age:
            continue
        out.append(_item(title, source + ' (공식)', dt, 'official'))
    return out


def fetch_official(now, max_age=timedelta(hours=24), get=_get):
    out = []
    for name, url in OFFICIAL_FEEDS.items():
        try:
            out.extend(parse_feed(get(url), name, now, max_age))
        except Exception:  # noqa: BLE001 — 한 피드 실패는 건너뜀
            continue
    return out


def fetch_hn(now, max_age=timedelta(hours=24), get=_get):
    """해커뉴스 첫 화면(무료 공개 API). 점수 150 이상 + AI·반도체 관련 제목만."""
    import json
    try:
        data = json.loads(get('https://hn.algolia.com/api/v1/search?tags=front_page&hitsPerPage=60'))
    except Exception:  # noqa: BLE001
        return []
    out = []
    for h in data.get('hits', []) if isinstance(data, dict) else []:
        try:
            dt = datetime.fromtimestamp(int(h['created_at_i']), timezone.utc).astimezone(now.tzinfo)
            pts = int(h.get('points') or 0)
        except (KeyError, TypeError, ValueError):
            continue
        if pts >= HN_MIN_POINTS and ai_related(h.get('title')) and now - dt <= max_age:
            out.append(_item(h.get('title'), 'Hacker News (커뮤니티·미확인)', dt, 'community', pts))
    return out


def _reddit_token(get_key=None):
    """레딧 앱 키(자격 증명 관리자 REDDIT_CLIENT_ID/SECRET)가 있을 때만. 없으면 None → 레딧 건너뜀."""
    try:
        from security.secret_store import get_api_key
        cid, sec = get_api_key('reddit_id'), get_api_key('reddit_secret')
        if not cid or not sec:
            return None
        r = requests.post('https://www.reddit.com/api/v1/access_token', auth=(cid.reveal(), sec.reveal()),
                          data={'grant_type': 'client_credentials'}, headers=UA, timeout=12, allow_redirects=False)
        tok = r.json().get('access_token') if r.status_code == 200 else None
        return tok if isinstance(tok, str) and tok else None
    except Exception:  # noqa: BLE001 — 키·토큰 값은 절대 기록하지 않는다
        return None


def fetch_reddit(now, max_age=timedelta(hours=24), token_factory=_reddit_token, get=_get):
    import json
    token = token_factory()
    if not token:
        return []
    out = []
    for sub in SUBREDDITS:
        try:
            data = json.loads(get(f'https://oauth.reddit.com/r/{sub}/hot?limit=25',
                                  headers={**UA, 'Authorization': 'bearer ' + token}))
        except Exception:  # noqa: BLE001
            continue
        for c in ((data.get('data') or {}).get('children') or []) if isinstance(data, dict) else []:
            d = c.get('data') or {}
            try:
                dt = datetime.fromtimestamp(float(d['created_utc']), timezone.utc).astimezone(now.tzinfo)
                score = int(d.get('score') or 0)
            except (KeyError, TypeError, ValueError):
                continue
            if d.get('stickied') or d.get('over_18'):
                continue
            if score >= REDDIT_MIN_SCORE and ai_related(d.get('title')) and now - dt <= max_age:
                out.append(_item(d.get('title'), f'Reddit r/{sub} (커뮤니티·미확인)', dt, 'community', score))
    return out


def fetch_sec(now, max_age=timedelta(hours=24), get=_get):
    """미국 SEC 공시(8-K). SEC 는 연락처가 담긴 User-Agent 를 요구 → SEC_USER_AGENT 를 설정했을 때만 조회."""
    import json
    ua = os.environ.get('SEC_USER_AGENT', '').strip()
    if not ua or '@' not in ua:
        return []
    out = []
    for name, cik in SEC_COMPANIES.items():
        try:
            data = json.loads(get(f'https://data.sec.gov/submissions/CIK{cik:010d}.json', headers={'User-Agent': ua}))
            recent = (data.get('filings') or {}).get('recent') or {}
            for form, filed, acc in list(zip(recent.get('form', []), recent.get('acceptanceDateTime', []),
                                             recent.get('accessionNumber', [])))[:15]:
                if form != '8-K':
                    continue
                dt = datetime.fromisoformat(str(filed).replace('Z', '+00:00')).astimezone(now.tzinfo)
                if now - dt <= max_age:
                    out.append(_item(f'{name} 8-K 공시 제출 (중요 사건 보고)', 'SEC EDGAR (공식 공시)', dt, 'filing'))
        except Exception:  # noqa: BLE001
            continue
    return out


def fetch_all(now, max_age=timedelta(hours=24)):
    seen, out = set(), []
    for fn in (fetch_official, fetch_sec, fetch_hn, fetch_reddit):
        for it in fn(now, max_age):
            if it['digest'] not in seen:
                seen.add(it['digest'])
                out.append(it)
    return sorted(out, key=lambda x: x['published_at'], reverse=True)[:40]


def as_news_titles(items, limit=10):
    """아침 분석용 '[날짜][AI업계][매체] 제목' 형식 (공식·공시 우선)."""
    rank = {'official': 0, 'filing': 1, 'community': 2}
    rows = sorted(sorted(items, key=lambda x: x['published_at'], reverse=True), key=lambda x: rank.get(x['kind'], 3))[:limit]
    return [f"[{r['published_at'][:10]}][AI업계][{r['source']}] {r['title']}" for r in rows]
