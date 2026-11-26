from datetime import datetime, timedelta
import json

from config import Config
from data.sessions import KST
from execution.split_portfolio import LONG, INVERSE, enable_long_core, fresh, summary, tick
from tools.paper_portfolio import run_once, write


NOW = datetime(2026, 10, 8, 10, 0, tzinfo=KST)


def quotes(now, etf_price=40_000):
    return {
        code: {
            'date': str(now.date()), 'value': value, 'market_status': 'OPEN',
            'observed_at': now.isoformat(), 'traded_at': now.isoformat(),
        }
        for code, value in ((LONG, etf_price), (INVERSE, 4_000))
    }


def test_long_core_transfer_preserves_total_and_is_idempotent():
    original = fresh(NOW, with_medium=True)
    moved = enable_long_core(original, NOW)
    assert original['sleeves']['long']['initial_cash'] == 8_000_000
    assert moved['sleeves']['long']['initial_cash'] == 5_600_000
    assert moved['sleeves']['long_core']['initial_cash'] == 2_400_000
    assert sum(book['initial_cash'] for book in moved['sleeves'].values()) == 20_000_000
    assert sum(book['equity'] for book in moved['sleeves'].values()) == 20_000_000
    assert moved['capital_transfers'][-1]['amount'] == 2_400_000
    assert enable_long_core(moved, NOW + timedelta(minutes=1)) == moved


def test_existing_stock_holding_refuses_transfer():
    state = fresh(NOW, with_medium=True)
    state['sleeves']['long']['holdings']['005930'] = {'qty': 1, 'cost': 50_000}
    assert enable_long_core(state, NOW) == state


def test_core_entry_is_separate_from_unapproved_stock_research_and_marks_profit():
    state = enable_long_core(fresh(NOW, with_medium=True), NOW)
    held_report = {'status': 'held', 'allocations': []}
    entered = tick(state, None, quotes(NOW), NOW, long_report=held_report)
    buys = [trade for trade in entered['trades'] if trade['side'] == 'buy']
    assert len(buys) == 1
    assert buys[0]['sleeve'] == 'long_core' and buys[0]['ticker'] == LONG
    assert buys[0]['qty'] > 0
    assert not entered['sleeves']['long']['holdings']
    assert entered['sleeves']['long']['cash'] == 5_600_000
    assert entered['sleeves']['long_core']['decision_key'] == 'core-initial-entry'
    later = NOW + timedelta(minutes=5)
    marked = tick(entered, None, quotes(later, 40_500), later, long_report=held_report)
    assert len(marked['trades']) == 1
    public = summary(marked)
    core = next(s for s in public['sleeves'] if s['key'] == 'long_core')
    stock = next(s for s in public['sleeves'] if s['key'] == 'long')
    assert core['unrealized_pnl'] > 0 and stock['pnl'] == 0


def test_core_needs_current_open_quote_and_stops_without_reentry():
    state = enable_long_core(fresh(NOW, with_medium=True), NOW)
    bad = quotes(NOW)
    bad[LONG]['observed_at'] = (NOW - timedelta(minutes=2)).isoformat()
    assert not tick(state, None, bad, NOW, long_report={'status': 'held', 'allocations': []})['trades']
    after_close = NOW.replace(hour=15, minute=35)
    assert not tick(state, None, quotes(after_close), after_close, long_report={'status': 'held', 'allocations': []})['trades']
    entered = tick(state, None, quotes(NOW), NOW, long_report={'status': 'held', 'allocations': []})
    later = NOW + timedelta(minutes=5)
    # (V3.39) 4회 분할 매수 첫 회차(25%)만 들어가므로 계좌 −15% 정지에는 ETF −60% 하락이 필요
    assert entered['sleeves']['long_core']['dca']['done'] == 1
    exited = tick(entered, None, quotes(later, 14_000), later, long_report={'status': 'held', 'allocations': []})
    assert exited['sleeves']['long_core']['paused']
    assert exited['trades'][-1]['side'] == 'sell'
    assert exited['trades'][-1]['sleeve'] == 'long_core'
    assert not tick(exited, None, quotes(later + timedelta(minutes=5)), later + timedelta(minutes=5), long_report={'status': 'held', 'allocations': []})['sleeves']['long_core']['holdings']


def test_scheduled_tick_migrates_with_backup_and_executes_once(tmp_path, monkeypatch):
    import requests
    from data import sessions

    path = tmp_path / 'paper_portfolio.json'
    write(path, fresh(NOW, with_medium=True))
    monkeypatch.setattr(Config, 'LONG_RESEARCH_PATH', str(tmp_path / 'missing_long.json'))
    monkeypatch.setattr(Config, 'PAPER_AGGRESSIVE_PATH', str(tmp_path / 'aggressive.json'))
    monkeypatch.setattr(Config, 'PAPER_INTRADAY_ENABLED', False)
    monkeypatch.setattr(sessions, 'kst_now', lambda: NOW)

    class Response:
        def raise_for_status(self):
            return None
        def json(self):
            return {'localTradedAt': NOW.isoformat(), 'closePrice': '40000',
                    'marketStatus': 'OPEN'}

    monkeypatch.setattr(requests, 'get', lambda *_args, **_kwargs: Response())
    assert run_once(path, None) in ('updated', 'partial')
    saved = json.loads(path.read_text(encoding='utf-8'))
    assert saved['sleeves']['long_core']['initial_cash'] == 2_400_000
    assert saved['sleeves']['long']['initial_cash'] == 5_600_000
    assert sum(book['initial_cash'] for book in saved['sleeves'].values()) == 30_000_000
    assert saved['initial_cash'] == 30_000_000
    assert saved['capital_events'][-1]['amount'] == 10_000_000
    assert len([trade for trade in saved['trades'] if trade['sleeve'] == 'long_core']) == 1
    assert list((tmp_path / '.review-backups').rglob('paper_portfolio.json'))
    run_once(path, None)
    saved_again = json.loads(path.read_text(encoding='utf-8'))
    assert len([trade for trade in saved_again['trades'] if trade['sleeve'] == 'long_core']) == 1


def test_core_buys_in_four_weekly_tranches():
    state = enable_long_core(fresh(NOW, with_medium=True), NOW)
    lr = {'status': 'held', 'allocations': []}
    s1 = tick(state, None, quotes(NOW), NOW, long_report=lr)
    b = s1['sleeves']['long_core']
    assert b['dca']['done'] == 1 and len([t for t in s1['trades'] if t['sleeve'] == 'long_core']) == 1
    invested1 = b['equity'] - b['cash']
    assert 0.2 < invested1 / b['equity'] <= 0.25
    same_week = NOW + timedelta(days=3)
    s2 = tick(s1, None, quotes(same_week), same_week, long_report=lr)
    assert s2['sleeves']['long_core']['dca']['done'] == 1 and '다음 회차' in s2['sleeves']['long_core']['status']
    nxt = NOW + timedelta(days=7)
    s3 = tick(s2, None, quotes(nxt), nxt, long_report=lr)
    b3 = s3['sleeves']['long_core']
    assert b3['dca']['done'] == 2 and 0.45 < (b3['equity'] - b3['cash']) / b3['equity'] <= 0.5
