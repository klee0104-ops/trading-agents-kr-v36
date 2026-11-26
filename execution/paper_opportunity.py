"""Private, prospective quote coverage and no-trade reference study.

This file never creates an order, selects a direction, or scores an AI forecast.
"""
from datetime import datetime, time
import json
from pathlib import Path

from data.sessions import market_status
from execution.manual_paper import _valid_quote
from execution.split_portfolio import FEE, SLIPPAGE

CODES = ('KPI200', '069500', '114800')
REFERENCE_BUDGET = 1_200_000  # 10% of the short sleeve, as a fixed comparison only.


def _reference(entry, exit_):
    result = {}
    for code in ('069500', '114800'):
        buy = entry['prices'][code] * (1 + SLIPPAGE)
        sell = exit_['prices'][code] * (1 - SLIPPAGE)
        qty = int(REFERENCE_BUDGET / (buy * (1 + FEE)))
        cost = qty * buy * (1 + FEE)
        proceeds = qty * sell * (1 - FEE)
        result[code] = {'quantity': qty, 'return_pct': round((proceeds - cost) / REFERENCE_BUDGET * 100, 4),
                        'pnl_won': round(proceeds - cost, 2)}
    return {'status': 'complete', 'entry_at': entry['at'], 'exit_at': exit_['at'],
            'budget_won': REFERENCE_BUDGET, 'fee_rate_each_side': FEE,
            'slippage_rate_each_side': SLIPPAGE, 'both_directions': result}


def update(data, quotes, now, envelope, intraday_plan, account):
    """Append only observations made during today's open session; never backfill."""
    if market_status(now)['code'] != 'open':
        return data
    if not isinstance(data, dict) or data.get('schema') != 1 or not isinstance(data.get('days'), list):
        raise ValueError('Invalid opportunity study schema')
    today = str(now.date())
    days = data['days']
    if days and days[-1].get('date', '') > today:
        raise ValueError('Opportunity observations cannot be backdated')
    day = next((d for d in days if d.get('date') == today), None)
    if day is None:
        day = {'date': today, 'observations': [], 'reference': {'status': 'incomplete'},
               'short_trades': 0, 'morning_plan_status': None, 'morning_qa_code': None,
               'intraday_plan_status': None, 'intraday_qa_code': None}
        days.append(day)
    plan = ((((envelope if isinstance(envelope, dict) else {}).get('outcome') or {})
             .get('final_result') or {}).get('etf_execution') or {})
    qa = plan.get('etf_qa') or {}
    if isinstance(envelope, dict) and envelope.get('mode') == 'real' and (envelope.get('outcome') or {}).get('target_date') == today:
        day['morning_plan_status'] = plan.get('status')
        day['morning_qa_code'] = qa.get('reason_code') if qa.get('passed') is not True else None
    if isinstance(intraday_plan, dict) and intraday_plan.get('target_date') == today:
        day['intraday_plan_status'] = intraday_plan.get('status')
        iq = intraday_plan.get('qa') or {}
        day['intraday_qa_code'] = iq.get('reason_code') if iq.get('passed') is not True else None
    day['short_trades'] = sum(t.get('sleeve') == 'short' and str(t.get('at', '')).startswith(today)
                              for t in account.get('trades', []) if isinstance(t, dict))
    valid = {c: _valid_quote(quotes.get(c) or {}, now) for c in CODES}
    row = {'at': now.isoformat(timespec='seconds'), 'valid': all(valid.values())}
    if row['valid']:
        row['prices'] = {c: round(quotes[c]['value'], 4) for c in CODES}
    else:
        row['unavailable'] = [c for c in CODES if not valid[c]]
    observations = day['observations']
    if observations and observations[-1]['at'] > row['at']:
        raise ValueError('Opportunity observations cannot be backdated')
    if observations and observations[-1]['at'] == row['at']:
        observations[-1] = row
    else:
        observations.append(row)
    day['observations'] = observations[-100:]
    entry = next((r for r in day['observations'] if r['valid'] and
                  time(9, 0) <= datetime.fromisoformat(r['at']).time() <= time(9, 15)), None)
    exit_ = next((r for r in day['observations'] if r['valid'] and
                  time(15, 20) <= datetime.fromisoformat(r['at']).time() < time(15, 30)), None)
    if entry and exit_:
        day['reference'] = _reference(entry, exit_)
    data['days'] = days[-45:]
    return data


def record(path, quotes, now, envelope, intraday_plan, account):
    """Atomic local sidecar update, called under the portfolio's process lock."""
    from tools.paper_portfolio import write
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'schema': 1, 'days': []}
    updated = update(data, quotes, now, envelope, intraday_plan, account)
    if market_status(now)['code'] == 'open':
        path.parent.mkdir(parents=True, exist_ok=True)
        write(path, updated)


def read_summary(path):
    """Local dashboard allowlist. Raw observations stay in the private sidecar."""
    try:
        data = json.loads(Path(path).read_text(encoding='utf-8'))
        if data.get('schema') != 1 or not isinstance(data.get('days'), list):
            raise ValueError('schema')
        days = data['days']
        last = days[-1] if days else None
        if not last:
            return {'status': 'waiting', 'sessions': 0, 'complete_sessions': 0}
        no_trade_reason = None
        if not last.get('short_trades'):
            from data.etf_forecast import QA_REASONS as MORNING_REASONS
            from execution.short_intraday_ai import QA_REASONS as INTRADAY_REASONS
            morning = MORNING_REASONS.get(last.get('morning_qa_code'))
            intraday = INTRADAY_REASONS.get(last.get('intraday_qa_code'))
            if morning:
                no_trade_reason = '아침 ETF 검증 보류: ' + morning
            elif intraday:
                no_trade_reason = '장중 재검토 보류: ' + intraday
            elif last.get('morning_plan_status') == 'approved':
                no_trade_reason = '승인 계획은 있었으나 진입 시간·시세·위험 한도 중 체결 조건이 충족되지 않았습니다.'
            else:
                no_trade_reason = '당일 승인된 ETF 방향 계획이 없어 신규 모의매매를 보류했습니다.'
        return {'status': 'recorded', 'date': last['date'], 'sessions': len(days),
                'complete_sessions': sum(d.get('reference', {}).get('status') == 'complete' for d in days),
                'observations': len(last['observations']),
                'valid_observations': sum(r.get('valid') is True for r in last['observations']),
                'short_trades': last.get('short_trades', 0),
                'no_trade_reason': no_trade_reason,
                'morning_plan_status': last.get('morning_plan_status'),
                'morning_qa_code': last.get('morning_qa_code'),
                'intraday_plan_status': last.get('intraday_plan_status'),
                'intraday_qa_code': last.get('intraday_qa_code'),
                'reference': last.get('reference', {'status': 'incomplete'})}
    except (OSError, ValueError, TypeError, KeyError):
        return {'status': 'waiting', 'sessions': 0, 'complete_sessions': 0}
