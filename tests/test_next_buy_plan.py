"""Next-buy display is read-only and uses synthetic account/plan snapshots."""
from datetime import datetime, timedelta

from data.sessions import KST
from execution.next_buy_plan import summarize
from execution.short_intraday_ai import validate
from execution.split_portfolio import fresh
from tools.short_intraday_review import MAX_REVIEWS_PER_DAY, source_registry

NOW = datetime(2026, 10, 7, 10, 31, tzinfo=KST)


def account(now=NOW):
    state = fresh(now)
    state['short_policy'] = 'etf_v2'
    state['last_tick'] = (now-timedelta(seconds=30)).isoformat()
    state['quote_health'] = {'status': 'ok'}
    return state


def intraday_plan(now=NOW, probability=60, action='069500'):
    at = now-timedelta(minutes=1)
    quotes = {code: {'date': str(at.date()), 'value': 100, 'market_status': 'OPEN',
                     'observed_at': at.isoformat(), 'traded_at': at.isoformat()}
              for code in ('KPI200', '069500', '114800')}
    sources = source_registry(quotes, at, .4, [])
    raw = {'forecasts': [{'code': code, 'up_probability': prob,
                         'reasoning': [f'시세 [M{index}]']}
                        for index, (code, prob) in enumerate(
                            (('KPI200', probability), ('069500', probability),
                             ('114800', 100-probability)), 901)],
           'paper_action': action,
           'paper_rationale': ['지수와 ETF [M901] [M902]']}
    return validate(raw, {'approve': True, 'reasoning': ['검토 [M901]']},
                    sources, at, 'scheduled_open')


def test_approved_intraday_plan_is_previewed_with_budget_but_no_order():
    state = account()
    before = list(state['trades'])
    plan = intraday_plan()
    preview = summarize(state, None, {'date': str(NOW.date()), 'attempts': 1, 'plan': plan},
                        {}, NOW)
    short = preview['short']
    assert (short['phase'], short['code'], short['weight_pct']) == ('conditional', '069500', 10)
    assert short['max_budget_won'] == 1_200_000
    assert state['trades'] == before


def test_existing_holding_has_no_additional_buy_even_with_new_plan():
    state = account()
    state['sleeves']['short']['holdings'] = {'069500': {'qty': 10, 'cost': 1000}}
    state['active_short_cycle'] = {'selected_code': '069500', 'entry_at': NOW.isoformat()}
    preview = summarize(state, None, {'date': str(NOW.date()), 'attempts': 1,
                                      'plan': intraday_plan()}, {}, NOW)
    assert preview['short']['phase'] == 'holding'
    assert '증액 계획 없음' in preview['short']['reason']


def test_finished_neutral_probe_and_exhausted_reviews_show_no_buy_plan():
    state = account()
    state['short_v2_decision_day'] = str(NOW.date())
    state['short_cycles'] = [{'target_date': str(NOW.date()), 'selected_code': '069500',
                              'exit_reason': 'profit_trailing_exit',
                              'selection_basis': 'neutral_probe'}]
    held = {'status': 'held', 'selected_code': None, 'qa': {'reason_code': 'reviewer_veto'}}
    preview = summarize(state, None, {'date': str(NOW.date()), 'attempts': MAX_REVIEWS_PER_DAY, 'plan': held},
                        {'status': 'held'}, NOW)
    assert preview['short']['phase'] == 'no_plan'
    assert f'{MAX_REVIEWS_PER_DAY}/{MAX_REVIEWS_PER_DAY}' in preview['short']['reason']
    assert preview['long']['phase'] == 'no_plan'


def test_long_plan_shows_price_condition_and_target_gap_only():
    state = account()
    state['last_prices'] = {'005930': 70_000}
    long_plan = {'status': 'approved', 'valid_until': (NOW+timedelta(days=1)).isoformat(),
                 'allocations': [{'code': '005930', 'weight': .25, 'buy_below': 75_000}],
                 'instruments': {'005930': {'name': '삼성전자'}}, 'blocked_codes': []}
    preview = summarize(state, None, {}, long_plan, NOW)
    candidate = preview['long']['candidates'][0]
    assert candidate['phase'] == 'price_condition_met'
    assert candidate['estimated_additional_won'] == 2_000_000
    assert candidate['buy_below'] == 75_000
    assert state['trades'] == []
    state['last_tick'] = (NOW-timedelta(minutes=11)).isoformat()
    stale = summarize(state, None, {}, long_plan, NOW)['long']['candidates'][0]
    assert stale['phase'] == 'quote_wait' and stale['estimated_additional_won'] is None


def test_trader_cash_plan_does_not_advertise_a_buy():
    state = account()
    cash = intraday_plan(probability=50, action='cash')
    preview = summarize(state, None, {'date': str(NOW.date()), 'attempts': 1, 'plan': cash},
                        {}, NOW)
    assert preview['short']['phase'] == 'no_plan'
    assert '현금 유지' in preview['short']['reason']
