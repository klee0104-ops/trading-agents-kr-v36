"""Update only public KOSPI quotes. No analysis, grading, trading or DB writes."""
from __future__ import annotations

from copy import deepcopy
from datetime import date
import json
import math
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def refresh_flow(data, now):
    from data.kr_flow import fetch_foreign_net_buy_naver
    out = deepcopy(data)
    try:
        flow = fetch_foreign_net_buy_naver(now.date())
        value = flow.get('foreign_net_krw_bn') if flow else None
        if type(value) not in (int, float) or not math.isfinite(value):
            raise ValueError('Invalid flow')
        if date.fromisoformat(flow['date']) > now.date():
            raise ValueError('Future flow')
        previous = data.get('public_flow') or {}
        if previous.get('date', '') > flow['date']:
            raise ValueError('Older flow')
        public = {'date': flow['date'], 'foreign_net_krw_bn': value, 'source': '네이버 증권'}
        unchanged = all(previous.get(k) == v for k, v in public.items())
        public['checked_at'] = previous.get('checked_at') if unchanged else now.isoformat(timespec='seconds')
        out['public_flow'] = public
        out['public_flow_status'] = 'ok'
    except Exception:
        out['public_flow_status'] = 'unavailable'
    # Older public reports omitted performance dates; recover only when the
    # complete scored sample is present. Never change stored return values.
    rb = out.get('returns')
    if rb and not rb.get('dates'):
        dates = sorted(r.get('actual_session_date') or r['target_date'] for r in out.get('records', [])
                       if r.get('verified') is True and r.get('actual_change_pct') is not None)
        if len(dates) == rb.get('days'):
            rb['dates'] = dates
    return out


def merge_quote(data, rows, now):
    from data.sessions import market_status
    if not rows:
        raise ValueError('No quote')
    r = rows[-1]
    day = date.fromisoformat(r['date'])
    if day > now.date():
        raise ValueError('Future quote')
    for key in ('close', 'prev_close'):
        value = r.get(key)
        if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
            raise ValueError('Invalid price')
    traded = str(r.get('traded_at') or r['date'])
    if len(traded) > 19 or traded[:10] != r['date']:
        raise ValueError('Invalid quote timestamp')
    previous = data.get('public_quote') or {}
    if previous.get('traded_at', '') > traded:
        raise ValueError('Quote moved backwards')
    out = deepcopy(data)
    history = out.get('kospi_history') or {}
    dates, closes = history.get('dates', []), history.get('closes', [])
    if not dates or len(dates) != len(closes) or dates[-1] > r['date']:
        raise ValueError('Invalid history')
    intraday = day == now.date() and market_status(now)['code'] in ('open', 'closing')
    quote = {'date': r['date'], 'value': r['close'], 'prev': r['prev_close'],
             'change_pct': round((r['close']/r['prev_close']-1)*100, 2),
             'traded_at': traded, 'intraday': intraday, 'source': '네이버 증권'}
    # Unchanged quotes do not produce a new Git commit on weekends/after hours.
    same = all(previous.get(k) == v for k, v in quote.items())
    quote['checked_at'] = previous.get('checked_at') if same else now.isoformat(timespec='seconds')
    out['public_quote'] = quote
    points = dict(zip(dates, closes))
    points[r['date']] = r['close']
    keys = sorted(points)[-80:]
    out['kospi_history'] = {'dates': keys, 'closes': [points[k] for k in keys]}
    return out


def run(root=ROOT):
    from data.kr_daily import fetch_daily
    from data.sessions import kst_now
    from security.public_report import Scripts, check_data, configured_secrets, harden_report
    from web.export_site import render_fragment
    from web.publish_site import publish_if_enabled
    config = json.loads((root/'sharing_public.json').read_text(encoding='utf-8'))
    if config.get('enabled') is not True:
        return 'disabled'
    path = root/'site/index.html'
    original = path.read_bytes()
    parsed = Scripts()
    parsed.feed(original.decode('utf-8'))
    payloads = [s for a, s in parsed.scripts if a.get('id') == 'ta-data' and a.get('type') == 'application/json']
    if len(payloads) != 1:
        raise ValueError('Invalid existing report')
    secrets = configured_secrets()
    data = json.loads(payloads[0])
    check_data(data, secrets)
    now = kst_now()
    updated = merge_quote(data, fetch_daily('KOSPI', 'index', include_intraday=True), now)
    updated = refresh_flow(updated, now)
    from data.public_stock_quotes import refresh_stock_quotes
    updated = refresh_stock_quotes(updated, now, root)
    from execution.split_portfolio import read_summary
    from config import Config
    from memory.long_report import read_public
    updated['long_research'] = read_public(Config.LONG_RESEARCH_PATH)
    from memory.research_reports import sync_latest
    updated['research_reports']=sync_latest(Config.REPORT_ARCHIVE_PATH,Config.LAST_OUTCOME_PATH,Config.LONG_RESEARCH_PATH,now)
    split = read_summary(Config.PAPER_PORTFOLIO_PATH)
    if split is not None:
        updated['paper'] = split
    page = '<!doctype html><html lang="ko"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover"></head><body>'+render_fragment(updated)+'</body></html>'
    # Validate before replacing any file or making an external write.
    harden_report(page, secrets)
    blob = page.encode('utf-8')
    if blob == original:
        return 'unchanged'
    if path.read_bytes() != original:
        raise RuntimeError('Report changed during quote collection')
    temp = path.with_name('quotes.pending.html')
    temp.write_bytes(blob)
    os.replace(temp, path)
    try:
        result = publish_if_enabled(path)
        if result is None:
            raise RuntimeError('Publication was disabled')
    except Exception:
        # Never leave an unpublished quote marked as successfully updated.
        if path.read_bytes() == blob:
            temp.write_bytes(original)
            os.replace(temp, path)
        raise
    return 'published' if result['changed'] else 'unchanged'


def main():
    from datetime import datetime
    status = 'failed'
    try:
        status = run()
        code = 0
    except Exception:
        # Do not persist provider responses, credentials or exception messages.
        code = 1
    folder = ROOT/'logs'
    folder.mkdir(exist_ok=True)
    (folder/'public_quotes_status.json').write_text(json.dumps({
        'attempted_at': datetime.now().isoformat(timespec='seconds'), 'status': status,
    }), encoding='utf-8')
    print('Public quotes: '+status)
    return code


if __name__ == '__main__':
    raise SystemExit(main())
