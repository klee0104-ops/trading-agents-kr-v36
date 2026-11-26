"""Isolated, deterministic intraday paper broker. Never sends real orders."""
from copy import deepcopy
from datetime import datetime, time
from hashlib import sha256
import math

LONG_ETF, INVERSE_ETF = '069500', '114800'
ETF_CODES = (LONG_ETF, INVERSE_ETF)
FEE_RATE = 0.00015
ESTIMATED_HALF_SPREAD = 0.0005
MAX_WEIGHT = 0.40
SIMULATED_NOTIONAL_FRACTION = 0.10


def fresh(now, cash=12_000_000):
    if not math.isfinite(cash) or cash <= 0:
        raise ValueError('Invalid initial cash')
    return {'schema': 1, 'mode': 'paper', 'strategy': 'intraday_morning_plan_rules',
            'created_at': now.isoformat(), 'initial_cash': cash, 'cash': cash,
            'holdings': {}, 'orders': [], 'fills': [], 'events': [], 'quotes': [],
            'realized_pnl': 0.0, 'fees': 0.0, 'equity': cash,
            'session_date': '', 'session_start': cash, 'risk_stop_date': '',
            'last_tick': None, 'last_signal_id': None, 'status': '판단 대기'}


def _timestamp(value, now):
    stamp = datetime.fromisoformat(value)
    if stamp.tzinfo is None:
        raise ValueError('Timezone required')
    return stamp.astimezone(now.tzinfo)


def _finite_positive(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value) and value > 0


def valid_quote(quote, now):
    try:
        observed = _timestamp(quote['observed_at'], now)
        traded = _timestamp(quote['traded_at'], now)
        last = quote.get('value')
        if 'bid' in quote or 'ask' in quote:
            bid, ask = quote.get('bid'), quote.get('ask')
            if (not _finite_positive(bid) or not _finite_positive(ask)
                    or bid > ask or not _finite_positive(last)
                    or bid < last * .95 or ask > last * 1.05):
                return False
        return (quote.get('date') == str(now.date()) and quote.get('market_status') == 'OPEN'
                and 0 <= (now-observed).total_seconds() <= 60
                and 0 <= (now-traded).total_seconds() <= 900
                and _finite_positive(quote.get('value')))
    except (KeyError, TypeError, ValueError):
        return False


def valid_signal(signal, now):
    if not isinstance(signal, dict) or signal.get('mode') != 'paper' or signal.get('source') != 'morning_plan_rules':
        return False
    try:
        made = _timestamp(signal['created_at'], now)
        expiry = _timestamp(signal['expires_at'], now)
        weight = signal['target_weight']
        return (signal['session_date'] == str(now.date())
                and signal.get('code') in (*ETF_CODES, None)
                and isinstance(signal.get('id'), str) and 1 <= len(signal['id']) <= 100
                and isinstance(weight, (int, float)) and not isinstance(weight, bool)
                and math.isfinite(weight) and 0 <= weight <= MAX_WEIGHT
                and (signal['code'] is not None or weight == 0)
                and signal.get('approved') is True
                and 0 <= (now-made).total_seconds() <= 3600
                and made < expiry and now <= expiry)
    except (KeyError, TypeError, ValueError):
        return False


def _event(state, now, status, reason):
    state['events'].append({'at': now.isoformat(), 'status': status, 'reason': reason})
    state['events'] = state['events'][-300:]
    state['status'] = reason


def _mark(state, quotes):
    value = state['cash']
    for code, holding in state['holdings'].items():
        value += holding['qty'] * quotes[code]['value']
    state['equity'] = value


def _price(quote, side):
    key = 'ask' if side == 'buy' else 'bid'
    price = quote.get(key)
    if _finite_positive(price):
        return float(price), 'quoted_bid_ask'
    base = quote['value']
    return base * (1 + ESTIMATED_HALF_SPREAD if side == 'buy' else 1 - ESTIMATED_HALF_SPREAD), 'estimated_spread'


def _cancel_open(state, now, reason):
    for order in state['orders']:
        if order['status'] in ('accepted', 'partially_filled'):
            order['status'] = 'cancelled'
            order['updated_at'] = now.isoformat()
            order['reason'] = reason
            order['history'].append({'at': now.isoformat(), 'status': 'cancelled', 'reason': reason})


def _cancel_open_buys(state, now, reason):
    for order in state['orders']:
        if order['side'] == 'buy' and order['status'] in ('accepted', 'partially_filled'):
            order['status'] = 'cancelled'
            order['updated_at'] = now.isoformat()
            order['reason'] = reason
            order['history'].append({'at': now.isoformat(), 'status': 'cancelled', 'reason': reason})


def _place(state, code, side, qty, signal_id, now, limit_weight=None):
    if qty <= 0:
        return
    key = f'{signal_id}|{code}|{side}|{len(state["orders"])}'
    order = {'id': sha256(key.encode()).hexdigest()[:20], 'signal_id': signal_id,
             'code': code, 'side': side, 'qty': qty, 'filled_qty': 0,
             'limit_weight': limit_weight,
             'status': 'accepted', 'created_at': now.isoformat(),
             'updated_at': now.isoformat(), 'reason': '',
             'history': [{'at': now.isoformat(), 'status': 'proposed'},
                         {'at': now.isoformat(), 'status': 'accepted'}]}
    state['orders'].append(order)


def _fill_open(state, quotes, now):
    for order in state['orders']:
        if order['status'] not in ('accepted', 'partially_filled'):
            continue
        quote = quotes.get(order['code'])
        if not valid_quote(quote or {}, now):
            continue
        remaining = order['qty'] - order['filled_qty']
        price, model = _price(quote, order['side'])
        # A fixed share cap would favor expensive ETFs. Simulate equal won capacity.
        modeled_capacity = int(state['initial_cash'] * SIMULATED_NOTIONAL_FRACTION / price)
        capacity = quote.get('simulated_available_qty', modeled_capacity)
        if not isinstance(capacity, int) or isinstance(capacity, bool) or capacity < 0:
            capacity = 0
        qty = min(remaining, capacity, modeled_capacity)
        if order['side'] == 'buy':
            if any(code != order['code'] for code in state['holdings']):
                continue
            qty = min(qty, int(state['cash'] / (price * (1 + FEE_RATE))))
            current_exposure = sum(h['qty'] * quotes[c]['value'] for c, h in state['holdings'].items())
            limit_weight = order.get('limit_weight')
            if not isinstance(limit_weight, (int, float)) or not math.isfinite(limit_weight):
                limit_weight = .25
            capacity_won = max(0, state['equity'] * min(limit_weight, MAX_WEIGHT) - current_exposure)
            qty = min(qty, int(capacity_won / (price * (1 + FEE_RATE))))
        else:
            qty = min(qty, state['holdings'].get(order['code'], {}).get('qty', 0))
        if qty <= 0:
            if order['side'] == 'buy' and state['cash'] < price * (1 + FEE_RATE):
                order['status'] = 'rejected'
                order['reason'] = '현금 부족'
                order['updated_at'] = now.isoformat()
                order['history'].append({'at': now.isoformat(), 'status': 'rejected', 'reason': '현금 부족'})
            continue
        gross = qty * price
        fee = gross * FEE_RATE
        holding = state['holdings'].get(order['code'], {'qty': 0, 'cost': 0.0})
        if order['side'] == 'buy':
            state['cash'] -= gross + fee
            holding['qty'] += qty
            holding['cost'] += gross + fee
            state['holdings'][order['code']] = holding
            realized = 0.0
        else:
            cost = holding['cost'] * qty / holding['qty']
            realized = gross - fee - cost
            state['cash'] += gross - fee
            state['realized_pnl'] += realized
            holding['qty'] -= qty
            holding['cost'] -= cost
            if holding['qty']:
                state['holdings'][order['code']] = holding
            else:
                state['holdings'].pop(order['code'])
        state['fees'] += fee
        order['filled_qty'] += qty
        order['status'] = 'filled' if order['filled_qty'] == order['qty'] else 'partially_filled'
        order['updated_at'] = now.isoformat()
        order['history'].append({'at': now.isoformat(), 'status': order['status'], 'qty': qty})
        state['fills'].append({'at': now.isoformat(), 'order_id': order['id'],
                               'code': order['code'], 'side': order['side'], 'qty': qty,
                               'price': round(price, 4), 'fee': round(fee, 4),
                               'realized_pnl': round(realized, 4), 'price_model': model})


def tick(state, signal, quotes, now, force_exit=False):
    """One idempotent paper transition; invalid market data never produces a fill."""
    from data.sessions import market_status
    result = deepcopy(state)
    if result.get('schema') != 1 or result.get('mode') != 'paper':
        raise ValueError('Invalid paper ledger')
    if result['last_tick'] and _timestamp(result['last_tick'], now) >= now:
        return result
    if result['session_date'] != str(now.date()):
        previous_day = result['session_date']
        if previous_day:
            _cancel_open(result, now, '거래일 변경: 미체결 주문 취소')
            if result['holdings']:
                result['carry_exit_date'] = str(now.date())
        result['session_date'] = str(now.date())
        result['session_start'] = result['equity']
    required = set(ETF_CODES) | set(result['holdings'])
    if market_status(now)['code'] != 'open' or not all(valid_quote(quotes.get(c) or {}, now) for c in required):
        _event(result, now, 'blocked', '장외 또는 ETF 시세 누락·지연: 주문 보류')
        result['last_tick'] = now.isoformat()
        return result
    _mark(result, quotes)
    result['quotes'].append({'at': now.isoformat(), 'kpi200': (quotes.get('KPI200') or {}).get('value'),
                             'long_etf': quotes[LONG_ETF]['value'], 'inverse_etf': quotes[INVERSE_ETF]['value']})
    result['quotes'] = result['quotes'][-100:]
    if result['equity'] <= result['session_start'] * 0.98:
        result['risk_stop_date'] = str(now.date())
    nowtime = now.time().replace(tzinfo=None)
    if nowtime > time(9, 15) and nowtime < time(10):
        for order in result['orders']:
            if order['side'] == 'buy' and order['status'] in ('accepted', 'partially_filled') and ':09:' in order['signal_id']:
                order['status'] = 'cancelled'
                order['reason'] = '09:15 진입 시간 종료'
                order['updated_at'] = now.isoformat()
                order['history'].append({'at': now.isoformat(), 'status': 'cancelled', 'reason': order['reason']})
    forced_exit = (force_exit or nowtime >= time(15, 20) or result['risk_stop_date'] == str(now.date())
                   or result.get('carry_exit_date') == str(now.date()))
    if forced_exit:
        _cancel_open(result, now, '시간 청산 또는 일일 손실 제한')
        for code, holding in list(result['holdings'].items()):
            _place(result, code, 'sell', holding['qty'], f'exit:{now.date()}', now)
        _fill_open(result, quotes, now)
        _event(result, now, 'exit', '15:20 청산 또는 일일 손실 제한')
    elif valid_signal(signal, now):
        if signal['id'] != result['last_signal_id']:
            _cancel_open(result, now, '새 판단으로 교체')
            code, weight = signal['code'], signal['target_weight']
            for held_code, holding in list(result['holdings'].items()):
                if held_code != code or weight == 0:
                    _place(result, held_code, 'sell', holding['qty'], signal['id'], now)
            if code and weight:
                price, _ = _price(quotes[code], 'buy')
                desired = int(result['equity'] * weight / (price * (1 + FEE_RATE)))
                owned = result['holdings'].get(code, {}).get('qty', 0)
                if desired > owned:
                    _place(result, code, 'buy', desired - owned, signal['id'], now, limit_weight=weight)
                elif desired < owned:
                    _place(result, code, 'sell', owned - desired, signal['id'], now)
            result['last_signal_id'] = signal['id']
            result['last_signal'] = signal
        _fill_open(result, quotes, now)
        _event(result, now, 'active', '승인된 장중 가상 판단 적용')
    else:
        _cancel_open_buys(result, now, '유효한 장중 판단 없음')
        _fill_open(result, quotes, now)
        _event(result, now, 'waiting', '유효한 장중 판단 없음: 신규 주문 보류')
    _mark(result, quotes)
    result['last_tick'] = now.isoformat()
    return result


def audit(state, quotes, now=None):
    """Reconcile cash, holdings and marked equity without trusting a displayed return."""
    if not all(c in quotes and _finite_positive(quotes[c].get('value')) for c in state['holdings']):
        return {'status': 'missing_quote'}
    if now is not None and not all(valid_quote(quotes[c], now) for c in state['holdings']):
        return {'status': 'stale'}
    computed = state['cash'] + sum(h['qty'] * quotes[c]['value'] for c, h in state['holdings'].items())
    cost = sum(h['cost'] for h in state['holdings'].values())
    unrealized = sum(h['qty'] * quotes[c]['value'] for c, h in state['holdings'].items()) - cost
    pnl = computed - state['initial_cash']
    return {'status': 'ok' if abs(computed - state['equity']) <= 0.01 and
            abs(pnl - state['realized_pnl'] - unrealized) <= 0.01 else 'mismatch',
            'equity': round(computed, 2), 'realized_pnl': round(state['realized_pnl'], 2),
            'unrealized_pnl': round(unrealized, 2), 'total_pnl': round(pnl, 2)}


def public_summary(path):
    """Small allowlisted snapshot; the private order ledger is never published."""
    import json
    from pathlib import Path
    path = Path(path)
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding='utf-8'))
    if state.get('schema') != 1 or state.get('mode') != 'paper':
        return None
    checked = state.get('valuation_audit') or {}
    ready = checked.get('status') == 'ok'
    return {'label': '장중 규칙 재평가 · 별도 가상 실험', 'initial_cash': state['initial_cash'],
            'last_tick': state.get('last_tick'), 'status': state.get('status'),
            'review_reason': (state.get('last_review') or {}).get('reason'),
            'analysis_source': '아침 AI 계획 + 장중 규칙 (매시간 AI 호출 아님)',
            'valuation_status': checked.get('status'),
            'equity': checked.get('equity') if ready else None,
            'total_pnl': checked.get('total_pnl') if ready else None,
            'realized_pnl': checked.get('realized_pnl') if ready else None,
            'unrealized_pnl': checked.get('unrealized_pnl') if ready else None,
            'cash': round(state['cash'], 2) if ready else None,
            'holdings': [{'code': code, 'qty': h['qty']} for code, h in state['holdings'].items() if code in ETF_CODES],
            'orders': [{'id': o['id'], 'code': o['code'], 'side': o['side'],
                        'qty': o['qty'], 'filled_qty': o['filled_qty'], 'status': o['status']}
                       for o in state['orders'][-5:] if o['code'] in ETF_CODES],
            'fills': [{'at': f['at'], 'code': f['code'], 'side': f['side'],
                       'qty': f['qty'], 'price': f['price'], 'price_model': f['price_model']}
                      for f in state['fills'][-5:] if f['code'] in ETF_CODES]}
