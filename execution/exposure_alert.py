"""Local diagnostic only; no notification or order action."""
from __future__ import annotations

from datetime import date, timedelta
import math

LOW_EXPOSURE_PCT = 10.0
CONSECUTIVE_SESSIONS = 5


def low_exposure_warning(history: list[dict]) -> dict | None:
    from data.sessions import market_status, KST
    from datetime import datetime
    rows = [r for r in history if isinstance(r, dict)
            and isinstance(r.get('date'), str)
            and type(r.get('exposure_pct')) in (int, float)
            and math.isfinite(r['exposure_pct'])]
    rows = rows[-CONSECUTIVE_SESSIONS:]
    if len(rows) != CONSECUTIVE_SESSIONS or any(r['exposure_pct'] >= LOW_EXPOSURE_PCT for r in rows):
        return None
    dates = []
    try:
        dates = [date.fromisoformat(r['date']) for r in rows]
        if dates != sorted(set(dates)):
            return None
        cursor = dates[0]
        expected = [cursor]
        while cursor < dates[-1]:
            cursor += timedelta(days=1)
            if market_status(datetime.combine(cursor, datetime.min.time(), KST))['code'] not in ('weekend', 'holiday'):
                expected.append(cursor)
        if dates != expected:
            return None
    except (ValueError, TypeError):
        return None
    return {'date': rows[-1]['date'], 'first_date': rows[0]['date'],
            'sessions': CONSECUTIVE_SESSIONS, 'threshold_pct': LOW_EXPOSURE_PCT,
            'exposure_pct': [r['exposure_pct'] for r in rows],
            'note': '전체 투자 노출 5거래일 연속 10% 미만 · 원인 검토 필요'}
