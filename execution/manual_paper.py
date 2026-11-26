"""Explicit local-only short ETF paper fills; never a broker or AI order."""
from copy import deepcopy
from datetime import datetime, time

from data.sessions import market_status
from execution.split_portfolio import (LONG, INVERSE, instrument_names, mark,
                                       number, rebalance, valuation_audit)

BUY_WEIGHTS = (.05, .10)


class ManualOrderError(ValueError):
    pass


def _valid_quote(quote, now):
    try:
        observed = datetime.fromisoformat(quote['observed_at'])
        traded = datetime.fromisoformat(quote['traded_at'])
        return (observed.tzinfo is not None and traded.tzinfo is not None
                and quote['date'] == str(now.date()) and quote['market_status'] == 'OPEN'
                and 0 <= (now-observed).total_seconds() <= 60
                and 0 <= (now-traded).total_seconds() <= 900
                and number(quote['value']) and quote['value'] > 0)
    except (KeyError, TypeError, ValueError):
        return False


def fetch_quotes(state, code):
    """Fetch current public quotes only; fail closed if a held asset is unavailable."""
    import requests
    from data.sessions import kst_now
    from tools.paper_portfolio import parse_quote
    codes = {code} | {c for b in state['sleeves'].values() for c in b['holdings']}
    quotes = {}
    for ticker in sorted(codes):
        try:
            response = requests.get('https://m.stock.naver.com/api/stock/'+ticker+'/basic',
                                    headers={'User-Agent':'Mozilla/5.0','Referer':'https://m.stock.naver.com/'}, timeout=8)
            response.raise_for_status()
            quotes[ticker] = parse_quote(response.json(), kst_now())
        except (requests.RequestException, KeyError, TypeError, ValueError) as exc:
            raise ManualOrderError('현재 시세를 검증하지 못해 체결을 보류했습니다.') from None
    now = kst_now()
    if not all(_valid_quote(q, now) for q in quotes.values()):
        raise ManualOrderError('당일 최신 체결 시세가 없어 주문을 보류했습니다.')
    return quotes, now


def prepare(state, side, code, weight, quotes, now):
    """Return an audited prospective ledger and its single simulated fill."""
    if (type(state) is not dict or state.get('schema') != 1 or state.get('enabled') is not True
            or state.get('short_policy') != 'etf_v2'):
        raise ManualOrderError('현재 모의계좌 형식을 확인할 수 없습니다.')
    if (side not in ('buy', 'sell') or code not in (LONG, INVERSE)
            or (side == 'buy' and (type(weight) not in (int, float) or weight not in BUY_WEIGHTS))
            or (side == 'sell' and weight is not None)):
        raise ManualOrderError('종목·방향·비중을 확인해 주세요.')
    if now.tzinfo is None or market_status(now)['code'] != 'open':
        raise ManualOrderError('국내 정규장 중에만 수동 모의체결할 수 있습니다.')
    if side == 'buy' and now.time().replace(tzinfo=None) >= time(15, 0):
        raise ManualOrderError('15:00 이후 신규 수동 매수는 보류합니다.')
    if state.get('last_tick') and datetime.fromisoformat(state['last_tick']) >= now:
        raise ManualOrderError('원장 시각이 현재보다 늦습니다. 새로고침 후 다시 시도하세요.')
    held = {c for b in state['sleeves'].values() for c in b['holdings']}
    needed = held | {code}
    if not needed <= set(quotes) or not all(_valid_quote(quotes[c], now) for c in needed):
        raise ManualOrderError('보유 종목을 포함한 최신 시세가 부족합니다.')
    if valuation_audit(state)['status'] == 'mismatch':
        raise ManualOrderError('기존 원장 손익 검증에 실패해 주문을 보류했습니다.')
    result = deepcopy(state)
    names = instrument_names(result)
    prices = {c: quotes[c]['value'] for c in needed}
    today = str(now.date())
    for book in result['sleeves'].values():
        if book['session'] != today:
            book['session_start'] = book['equity']
            book['session'] = today
        mark(book, prices)
    book = result['sleeves']['short']
    if side == 'buy':
        if result.get('active_short_cycle') or book['holdings']:
            raise ManualOrderError('단기 보유분이 있어 추가 강제매수를 허용하지 않습니다.')
        if result.get('manual_short_buy_day') == today:
            raise ManualOrderError('수동 모의매수는 하루 한 번만 허용합니다.')
        if (book['paused'] or book.get('stop_day') == today
                or book['equity'] <= book['session_start'] * .98
                or book['equity'] <= book['peak'] * .90):
            raise ManualOrderError('단기 계좌 손실 제한으로 신규 매수를 보류합니다.')
    elif set(book['holdings']) != {code}:
        raise ManualOrderError('매도할 단기 ETF 보유분이 없습니다.')
    before = len(result['trades'])
    entry_equity, entry_fees = book['equity'], book['fees']
    reason = '사용자 수동 모의체결 · AI·QA 승인 매매 아님'
    rebalance(book, code if side == 'buy' else None, weight or 0, prices, now,
              result['trades'], 'short', reason, names=names)
    new = result['trades'][before:]
    if len(new) != 1 or new[0]['side'] != side or new[0]['ticker'] != code:
        raise ManualOrderError('체결 가능 수량 또는 계좌 상태가 맞지 않습니다.')
    new[0]['origin'] = 'manual'
    if side == 'buy':
        from execution.short_profit_exit import POLICY
        result['active_short_cycle'] = {
            'version':'manual', 'selection_basis':'manual', 'entry_at':now.isoformat(),
            'plan_at':None, 'target_date':today, 'selected_code':code,
            'entry_equity':entry_equity, 'entry_fees':entry_fees,
            'reference_prices':{code:prices[code]}, 'forecasts':[], 'status':'open',
            'peak_equity':book['equity'], 'max_drawdown_pct':0,
            'exit_policy':dict(POLICY), 'transition_from_legacy':False}
        result['manual_short_buy_day'] = today
        book['status'] = '수동 모의매수 보유 · AI 적중률 제외 · 자동 손절/15:20 청산 적용'
    else:
        if result.get('active_short_cycle'):
            from execution.etf_contract import close_cycle
            close_cycle(result, book, quotes, now, 'manual_exit')
        book['status'] = '수동 모의매도 완료 · 실현손익 기록 · AI 적중률 제외'
    result['short_v2_decision_day'] = today
    book['decision_key'] = today
    result['last_tick'] = now.isoformat(timespec='seconds')
    result['quote_at'] = result['last_tick']
    result['last_prices'] = {**(result.get('last_prices') or {}), **prices}
    result['quote_health'] = {'status':'ok','missing_codes':[],'at':result['last_tick']}
    equity = sum(b['equity'] for b in result['sleeves'].values())
    entry = {'date':today,'equity':round(equity,2)}
    if result['equity_history'] and result['equity_history'][-1]['date'] == today:
        result['equity_history'][-1] = entry
    else:
        result['equity_history'].append(entry)
    if valuation_audit(result, now)['status'] != 'ok':
        raise ManualOrderError('체결 후 원장 검증에 실패해 주문을 취소했습니다.')
    return result, new[0]
