"""(V3.37) 개장 전 대형주 공시 — OpenDART 공시 목록(제목만)에서 전·당일 주요 공시를 아침 분석 근거로 넘긴다.
예) 10/23 SK하이닉스 '솔리다임 상장 검토 중단' 공시는 개장 전에 나왔지만 단기 분석가에게 전달되지 않았다.
"""
from __future__ import annotations
from datetime import timedelta

ROUTINE = ('임원ㆍ주요주주', '임원·주요주주', '특정증권등소유', '주식등의대량보유', '대량보유상황',
           '증권발행실적', '투자설명서', '일괄신고', '기업설명회', '결산실적공시예고', '최대주주등소유주식변동',
           '자기주식취득결과', '주주총회소집', '참고서류', '소규모', '사외이사')
CORE = ('005930', '000660')


def collect(now, universe, key, poll=None, limit=8):
    """universe: [{code,name,core}] → '[날짜][종목][OpenDART 공시] 보고서명' 목록. 키가 없거나 실패하면 []."""
    if not key:
        return []
    if poll is None:
        from data.dart_disclosures import poll
    codes = [u.get('code') for u in universe or [] if u.get('code')]
    try:
        state = poll(now, codes, key, None)
    except Exception:  # noqa: BLE001
        return []
    since = (now - timedelta(days=1)).strftime('%Y%m%d')
    rows = []
    for e in (state or {}).get('events') or []:
        title = str(e.get('title') or '')
        if str(e.get('filed_date') or '') < since or not title or any(w in title for w in ROUTINE):
            continue
        d = str(e['filed_date'])
        rows.append((0 if e.get('code') in CORE else 1, d, f"[{d[:4]}-{d[4:6]}-{d[6:]}][{e.get('name')}][OpenDART 공시] {title}"))
    rows.sort(key=lambda r: (r[0], r[1]), reverse=False)
    rows = sorted(rows, key=lambda r: r[1], reverse=True)
    rows = sorted(rows, key=lambda r: r[0])
    return [r[2] for r in rows[:limit]]
