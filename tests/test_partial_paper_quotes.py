"""A long-stock data outage must not suppress a valid short ETF exit."""
from datetime import datetime
import json

import requests

from config import Config
from data.sessions import KST
from execution.split_portfolio import INVERSE, fresh, summary_at, tick
from tools.paper_portfolio import run_once, write


ENTRY = datetime(2026, 9, 30, 9, 5, tzinfo=KST)
CLOSE = ENTRY.replace(hour=15, minute=20)
STOCK = '005930'


def quotes(now, *, with_stock=True, with_long_etf=True):
    rows = {code: {'date': str(now.date()), 'value': 100,
                   'observed_at': now.isoformat(), 'traded_at': now.isoformat(),
                   'market_status': 'OPEN'}
            for code in ('069500', INVERSE, 'KPI200', STOCK)}
    if not with_stock:
        rows.pop(STOCK)
    if not with_long_etf:
        rows.pop('069500')
    return rows


def envelope():
    plan = {'schema': 2, 'status': 'approved', 'target_date': str(ENTRY.date()),
            'generated_at': ENTRY.replace(hour=8).isoformat(),
            'selected_code': INVERSE, 'weight': .25,
            'forecasts': [{'code': c, 'up_probability': p} for c, p in
                          [('KPI200', 40), ('069500', 40), (INVERSE, 65)]]}
    return {'mode': 'real', 'saved_at': ENTRY.replace(hour=8).isoformat(),
            'outcome': {'target_date': str(ENTRY.date()), 'final_qa': {'consistent': True},
                        'final_result': {'etf_execution': plan}}}


def entered():
    state = fresh(ENTRY)
    state['short_policy'] = 'etf_v2'
    long = state['sleeves']['long']
    long['holdings'][STOCK] = {'qty': 10, 'cost': 1000.0}
    long['cash'] -= 1000
    return tick(state, envelope(), quotes(ENTRY), ENTRY)


def test_missing_long_stock_does_not_block_short_exit_or_fake_total_return():
    state = entered()
    assert state['sleeves']['short']['holdings']
    result = tick(state, envelope(), quotes(CLOSE, with_stock=False), CLOSE)
    assert not result['sleeves']['short']['holdings']
    assert result['trades'][-1]['side'] == 'sell'
    assert result['sleeves']['long']['holdings'] == state['sleeves']['long']['holdings']
    assert result['quote_health']['status'] == 'partial'
    public = summary_at(result, CLOSE)
    assert public['valuation_audit']['status'] == 'partial'
    assert public['pnl'] is None and public['total_return_pct'] is None
    assert public['last_complete_valuation']['date'] == str(ENTRY.date())
    assert public['last_complete_valuation']['equity'] == state['equity_history'][-1]['equity']


def test_missing_unheld_buy_candidate_does_not_hide_account_return():
    state = fresh(ENTRY)
    state['short_policy'] = 'etf_v2'
    candidate = {'status': 'approved', 'allocations': [
        {'code': STOCK, 'weight': .1, 'buy_below': 100}]}
    result = tick(state, envelope(), quotes(ENTRY, with_stock=False), ENTRY,
                  long_report=candidate)
    public = summary_at(result, ENTRY)
    assert result['quote_health']['missing_codes'] == [STOCK]
    assert result['quote_health']['missing_held_codes'] == []
    assert public['valuation_audit']['status'] == 'ok'
    assert public['equity'] is not None and public['total_return_pct'] is not None
    assert len(result['equity_history']) == 1
    assert not result['sleeves']['long']['holdings']


def test_weekend_tick_keeps_last_complete_mark():
    state = entered()
    weekend = datetime(2026, 10, 11, 13, 10, tzinfo=KST)
    assert tick(state, envelope(), {}, weekend) == state
    assert summary_at(state, weekend)['valuation_audit']['status'] == 'ok'


def test_off_session_scheduler_skips_http_and_ledger_write(tmp_path, monkeypatch):
    from data import sessions

    state = entered()
    path = tmp_path/'portfolio.json'
    write(path, state)
    original = path.read_bytes()
    monkeypatch.setattr(sessions, 'kst_now', lambda: datetime(2026, 10, 11, 13, 10, tzinfo=KST))
    monkeypatch.setattr(requests, 'get', lambda *a, **k: (_ for _ in ()).throw(AssertionError('HTTP called')))
    assert run_once(path, envelope()) == 'market_closed'
    assert path.read_bytes() == original
    monkeypatch.setattr(sessions, 'kst_now', lambda: datetime(2026, 10, 8, 16, 0, tzinfo=KST))
    assert run_once(path, envelope()) == 'market_closed'
    assert path.read_bytes() == original


def test_missing_other_etf_allows_held_etf_exit_but_not_full_horizon_grade():
    state = entered()
    result = tick(state, envelope(), quotes(CLOSE, with_long_etf=False), CLOSE)
    assert not result['sleeves']['short']['holdings']
    assert result['short_cycles'][0]['horizon_matched'] is False
    assert all(score['correct'] is None for score in result['short_cycles'][0]['scores'])


def test_http_failure_of_one_long_stock_keeps_short_exit_running(tmp_path, monkeypatch):
    state = entered()
    path = tmp_path/'portfolio.json'
    write(path, state)
    monkeypatch.setattr(Config, 'LONG_RESEARCH_PATH', str(tmp_path/'missing_long.json'))
    monkeypatch.setattr(Config, 'PAPER_AGGRESSIVE_PATH', str(tmp_path/'aggressive.json'))
    monkeypatch.setattr(Config, 'PAPER_INTRADAY_PATH', str(tmp_path/'intraday.json'))
    monkeypatch.setattr(__import__('data.sessions', fromlist=['kst_now']), 'kst_now', lambda: CLOSE)

    class Response:
        def raise_for_status(self):
            return None
        def json(self):
            return {'localTradedAt': CLOSE.isoformat(), 'closePrice': '100',
                    'marketStatus': 'OPEN'}

    requested = []
    def fetch(url, **kwargs):
        requested.append((url, kwargs['timeout']))
        if f'/stock/{STOCK}/' in url:
            raise requests.Timeout('synthetic outage')
        return Response()

    monkeypatch.setattr(requests, 'get', fetch)
    assert run_once(path, envelope()) == 'partial'
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert not saved['sleeves']['short']['holdings']
    assert saved['sleeves']['long']['holdings'][STOCK]['qty'] == 10
    assert saved['quote_health']['status'] == 'partial'
    assert requested[0][0].endswith('/stock/'+STOCK+'/basic')
    assert requested[0][1] == 3
    assert all(timeout == 10 for url, timeout in requested if '/stock/069500/' in url or '/stock/114800/' in url)
