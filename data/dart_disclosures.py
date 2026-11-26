"""Bounded OpenDART disclosure-list polling for tracked Korean stocks.

This reads filing metadata only. It neither downloads filing bodies nor supplies
financial values to the long-term purchase gate.
"""
from datetime import timedelta
import hashlib
import re

import requests

from rules.citations import sanitize_external


ENDPOINT = 'https://opendart.fss.or.kr/api/list.json'
VIEWER = 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='
MAX_PAGES = 10
MAX_RESPONSE_BYTES = 1_000_000
MAX_EVENTS = 80
POLL_INTERVAL = timedelta(minutes=55)


def _clean(value, limit=160):
    # Titles come from an external API and may later appear in HTML or a prompt.
    return sanitize_external(str(value or '').replace('<', ' ').replace('>', ' '), limit).strip()


def _request(params):
    response = requests.get(ENDPOINT, params=params, timeout=12, allow_redirects=False,
                            headers={'User-Agent': 'TradingAgents-KR/3.6'})
    if response.status_code != 200 or len(response.content) > MAX_RESPONSE_BYTES:
        raise ValueError('dart_http_or_size')
    payload = response.json()
    if not isinstance(payload, dict):
        raise ValueError('dart_schema')
    return payload


def poll(now, stock_codes, key, previous=None):
    """Poll at most once per hour; retain old filings but label failed/partial checks.

    Key contents and request URLs are never included in the returned state.
    """
    previous = previous if isinstance(previous, dict) else {}
    codes = {str(code) for code in stock_codes if re.fullmatch(r'[0-9]{6}', str(code))}
    code_fingerprint = hashlib.sha256(','.join(sorted(codes)).encode()).hexdigest()[:16]
    if not key:
        return {**previous, 'status': 'not_configured', 'checked_at': now.isoformat(),
                'events': previous.get('events', [])[-MAX_EVENTS:]}
    try:
        from datetime import datetime
        checked = datetime.fromisoformat(previous['checked_at'])
        if (previous.get('status') != 'not_configured'
                and previous.get('tracked_codes_hash') == code_fingerprint
                and timedelta(0) <= now - checked < POLL_INTERVAL):
            return previous
    except (KeyError, TypeError, ValueError):
        pass

    begin = (now - timedelta(days=2)).strftime('%Y%m%d')
    base = {'crtfc_key': key.reveal(), 'bgn_de': begin,
            'end_de': now.strftime('%Y%m%d'), 'corp_cls': 'Y',
            'page_count': 100, 'sort': 'date', 'sort_mth': 'desc'}
    old = {item['id']: item for item in previous.get('events', [])
           if isinstance(item, dict) and re.fullmatch(r'[0-9]{14}', str(item.get('id', '')))
           and re.fullmatch(r'[0-9]{8}', str(item.get('filed_date', '')))}
    events = dict(old)
    result = {'status': 'unavailable', 'checked_at': now.isoformat(),
              'last_success_at': previous.get('last_success_at'),
              'events': list(old.values())[-MAX_EVENTS:], 'new_count': 0,
              'tracked_count': len(codes), 'tracked_codes_hash': code_fingerprint,
              'coverage_start': begin,
              'partial_reason': None}
    try:
        first = _request({**base, 'page_no': 1})
        status = str(first.get('status', ''))
        if status == '013':
            gap = False
            if previous.get('last_success_at'):
                try:
                    from datetime import datetime
                    gap = datetime.fromisoformat(previous['last_success_at']).date() < (now - timedelta(days=2)).date()
                except (TypeError, ValueError):
                    gap = True
            result.update(status='partial' if gap else 'ok',
                          last_success_at=previous.get('last_success_at') if gap else now.isoformat(),
                          partial_reason='offline_gap' if gap else None,
                          events=list(old.values())[-MAX_EVENTS:])
            return result
        if status != '000' or not isinstance(first.get('list'), list):
            result['partial_reason'] = 'api_status_' + status[:3] if re.fullmatch(r'[0-9]{3}', status) else 'api_schema'
            return result
        pages = int(first.get('total_page', 1))
        if pages < 1:
            raise ValueError('dart_pages')
        for page_no in range(1, min(pages, MAX_PAGES) + 1):
            payload = first if page_no == 1 else _request({**base, 'page_no': page_no})
            if payload.get('status') != '000' or not isinstance(payload.get('list'), list):
                raise ValueError('dart_page_status')
            for item in payload['list']:
                if not isinstance(item, dict) or item.get('stock_code') not in codes:
                    continue
                receipt = str(item.get('rcept_no', ''))
                filed = str(item.get('rcept_dt', ''))
                if not re.fullmatch(r'[0-9]{14}', receipt) or not re.fullmatch(r'[0-9]{8}', filed):
                    continue
                events[receipt] = {'id': receipt, 'code': item['stock_code'],
                                   'name': _clean(item.get('corp_name'), 80),
                                   'title': _clean(item.get('report_nm')),
                                   'filed_date': filed,
                                   'url': VIEWER + receipt}
        sorted_events = sorted(events.values(), key=lambda item: (item['filed_date'], item['id']), reverse=True)
        result['events'] = sorted_events[:MAX_EVENTS]
        result['new_count'] = sum(item['id'] not in old for item in sorted_events[:MAX_EVENTS]) if previous.get('last_success_at') else 0
        gap = False
        if previous.get('last_success_at'):
            try:
                from datetime import datetime
                gap = datetime.fromisoformat(previous['last_success_at']).date() < (now - timedelta(days=2)).date()
            except (TypeError, ValueError):
                gap = True
        if pages > MAX_PAGES or gap:
            result.update(status='partial', partial_reason='page_limit' if pages > MAX_PAGES else 'offline_gap')
        else:
            result.update(status='ok', last_success_at=now.isoformat())
    except (requests.RequestException, ValueError, KeyError, TypeError):
        result['partial_reason'] = 'request_or_schema_error'
        # Earlier verified filings remain visible; the new check is not called successful.
        if events != old:
            result['events'] = sorted(events.values(), key=lambda item: (item['filed_date'], item['id']), reverse=True)[:MAX_EVENTS]
    return result
