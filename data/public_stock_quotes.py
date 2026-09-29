"""Read-only quotes for displayed stocks; never score predictions or trade."""
from concurrent.futures import ThreadPoolExecutor
from datetime import date
from copy import deepcopy
import json
import math
import re


def refresh_stock_quotes(data, now, root):
    from data.kr_daily import fetch_daily
    from data.price_history import _NAME_ALIASES
    from data.sessions import market_status
    try:
        mapping = json.loads((root/'ticker_map_cache.json').read_text(encoding='utf-8'))
    except (OSError, ValueError):
        mapping = {}
    names = list(dict.fromkeys(s.get('name') for s in
                 (data.get('core_stocks', []) + data.get('watch_stocks', []))
                 if isinstance(s.get('name'), str)))[:16]
    previous = data.get('stock_quotes') or {}

    def collect(name):
        old = previous.get(name) or {}
        try:
            # Exact local mapping only. No fuzzy matches, listing downloads or cache writes.
            code = mapping.get(name) or mapping.get(_NAME_ALIASES.get(name, ''))
            if not isinstance(code, str) or not re.fullmatch(r'[0-9]{6}', code):
                raise ValueError('Unknown code')
            rows = fetch_daily(code, 'stock', include_intraday=True)
            r = rows[-1]
            day = date.fromisoformat(r['date'])
            if day > now.date() or old.get('date', '') > r['date']:
                raise ValueError('Invalid date')
            for k in ('close', 'prev_close'):
                if type(r.get(k)) not in (float, int) or not math.isfinite(r[k]) or r[k] <= 0:
                    raise ValueError('Invalid price')
            traded = str(r.get('traded_at') or r['date'])
            if traded[:10] != r['date'] or len(traded) > 19 or old.get('traded_at', '') > traded:
                raise ValueError('Invalid time')
            q = {'status': 'ok', 'code': code, 'date': r['date'], 'value': r['close'],
                 'change_pct': round((r['close']/r['prev_close']-1)*100, 4),
                 'traded_at': traded, 'source': '네이버 증권',
                 'intraday': day == now.date() and market_status(now)['code'] in ('open', 'closing')}
            history = {}
            for item in rows:
                try:
                    d = date.fromisoformat(item['date'])
                    v = item['close']
                    if d <= day and type(v) in (int, float) and math.isfinite(v) and v > 0:
                        history[d.isoformat()] = v
                except (ValueError, TypeError, KeyError):
                    continue
            q['history'] = [{'date': d, 'close': history[d]} for d in sorted(history)[-10:]]
            q['checked_at'] = old.get('checked_at') if all(old.get(k)==v for k,v in q.items()) else now.isoformat(timespec='seconds')
            return name, q
        except Exception:
            # Retain last known quote but explicitly disable fresh comparisons.
            return name, {**old, 'status': 'unavailable'}

    out = deepcopy(data)
    from config import Config
    threshold = Config.STOCK_FLAT_PCT
    out['stock_flat_threshold_pct'] = threshold if math.isfinite(threshold) and threshold > 0 else None
    with ThreadPoolExecutor(max_workers=3) as pool:
        out['stock_quotes'] = dict(pool.map(collect, names))
    return out
