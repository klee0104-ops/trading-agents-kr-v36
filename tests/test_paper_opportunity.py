"""Prospective no-trade measurements use synthetic data, never a user ledger."""
from datetime import datetime, timedelta

from data.sessions import KST
from execution.paper_opportunity import read_summary, record, update
from execution.short_intraday_ai import validate
from tools.short_intraday_review import source_registry


def quotes(now, long=100, inverse=100):
    return {code: {'date': str(now.date()), 'value': value, 'market_status': 'OPEN',
                   'observed_at': now.isoformat(), 'traded_at': now.isoformat()}
            for code, value in (('KPI200', 100), ('069500', long), ('114800', inverse))}


def test_no_trade_day_records_both_reference_directions_without_creating_orders(tmp_path):
    entry = datetime(2026, 10, 7, 9, 5, tzinfo=KST)
    exit_ = entry.replace(hour=15, minute=25)
    path = tmp_path / 'logs' / 'paper_opportunity_study.json'
    account = {'trades': []}
    record(path, quotes(entry), entry, None, None, account)
    record(path, quotes(exit_, long=102, inverse=98), exit_, None, None, account)
    summary = read_summary(path)
    assert (summary['sessions'], summary['complete_sessions'], summary['short_trades']) == (1, 1, 0)
    result = summary['reference']
    assert result['status'] == 'complete'
    assert result['both_directions']['069500']['return_pct'] > 0
    assert result['both_directions']['114800']['return_pct'] < 0
    assert result['entry_at'].startswith('2026-10-07T09:05')
    assert result['exit_at'].startswith('2026-10-07T15:25')


def test_missing_opening_quote_does_not_backfill_reference(tmp_path):
    now = datetime(2026, 10, 7, 15, 25, tzinfo=KST)
    path = tmp_path / 'logs' / 'paper_opportunity_study.json'
    record(path, quotes(now), now, None, None, {'trades': []})
    assert read_summary(path)['reference']['status'] == 'incomplete'
    after = now + timedelta(minutes=15)
    record(path, quotes(after), after, None, None, {'trades': []})
    assert read_summary(path)['observations'] == 1


def test_invalid_quote_is_counted_without_reference_or_prices():
    now = datetime(2026, 10, 7, 9, 5, tzinfo=KST)
    q = quotes(now)
    q['114800']['value'] = -1
    data = update({'schema': 1, 'days': []}, q, now, None, None, {'trades': []})
    row = data['days'][0]['observations'][0]
    assert row == {'at': now.isoformat(timespec='seconds'), 'valid': False,
                   'unavailable': ['114800']}
    assert data['days'][0]['reference']['status'] == 'incomplete'


def test_intraday_qa_rejection_has_specific_safe_reason():
    now = datetime(2026, 10, 7, 10, 30, tzinfo=KST)
    sources = source_registry(quotes(now), now, .4, [])
    raw = {'forecasts': [{'code': code, 'up_probability': probability,
                         'reasoning': [f'시세 [M{index}]']}
                        for index, (code, probability) in enumerate(
                            (('KPI200', 60), ('069500', 60), ('114800', 60)), 901)]}
    result = validate(raw, {'approve': True, 'reasoning': ['시세 [M901]']},
                      sources, now, 'price')
    assert result['status'] == 'held'
    assert result['qa']['reason_code'] == 'direction_conflict'
    assert '인버스' in result['qa']['reason']
