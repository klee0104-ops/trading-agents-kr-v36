"""Synthetic quotes and plans only; never touches an operational ledger or network."""
from datetime import datetime, timedelta

from data.sessions import KST
from execution.intraday_paper import audit, fresh, tick
from tools.intraday_paper import build_signal


MORNING = datetime(2026, 9, 29, 9, 5, tzinfo=KST)


def quotes(now, index=350.0, long=40_000.0, inverse=4_000.0, capacity=30):
    return {code: {'date': str(now.date()), 'value': price, 'observed_at': now.isoformat(),
                   'traded_at': now.isoformat(), 'market_status': 'OPEN',
                   'simulated_available_qty': capacity}
            for code, price in [('KPI200', index), ('069500', long), ('114800', inverse)]}


def envelope(now=MORNING):
    return {'mode': 'real', 'saved_at': now.isoformat(),
            'outcome': {'target_date': str(now.date()), 'final_qa': {'consistent': True},
                        'final_result': {'etf_execution': {
                            'schema': 2, 'status': 'approved', 'target_date': str(now.date()),
                            'generated_at': now.replace(hour=8, minute=20).isoformat(),
                            'selected_code': '069500', 'weight': .25,
                            'forecasts': [{'code': 'KPI200', 'up_probability': 62},
                                          {'code': '069500', 'up_probability': 65},
                                          {'code': '114800', 'up_probability': 35}]}}}}


def test_schema4_approved_inverse_plan_passes_sidecar_qa_and_weight_gate():
    approved = envelope()
    plan = approved['outcome']['final_result']['etf_execution']
    plan.update(schema=4, etf_qa={'passed': True}, selected_code='114800', weight=.40)
    plan['forecasts'][1]['up_probability'] = 35
    plan['forecasts'][2]['up_probability'] = 65
    signal, reason = build_signal(fresh(MORNING), approved, quotes(MORNING), MORNING)
    assert signal['code'] == '114800' and signal['target_weight'] == .40
    assert reason == '아침 승인 계획 관찰'
    plan['etf_qa']['passed'] = False
    assert build_signal(fresh(MORNING), approved, quotes(MORNING), MORNING)[0] is None
    plan['etf_qa']['passed'] = True
    plan['weight'] = .41
    assert build_signal(fresh(MORNING), approved, quotes(MORNING), MORNING)[0] is None


def test_neutral_probe_is_labeled_as_main_account_only_not_failed_sidecar_qa():
    from data.etf_forecast import validate
    from tests.test_etf_alignment import NOW, neutral_evidence, neutral_raw
    approved = envelope(NOW)
    approved['outcome']['final_result']['etf_execution'] = validate(
        neutral_raw(), neutral_evidence(), NOW.replace(hour=8), str(NOW.date()))
    signal, reason = build_signal(fresh(NOW), approved, quotes(NOW), NOW)
    assert signal is None and '본계좌 5%' in reason


def test_approved_morning_plan_partially_fills_and_reconciles():
    state = fresh(MORNING)
    signal, _ = build_signal(state, envelope(), quotes(MORNING), MORNING)
    assert signal['code'] == '069500' and signal['source'] == 'morning_plan_rules'
    first = tick(state, signal, quotes(MORNING), MORNING)
    assert state['orders'] == []
    assert first['orders'][0]['status'] == 'partially_filled'
    assert first['orders'][0]['filled_qty'] == 29
    assert [row['status'] for row in first['orders'][0]['history']] == ['proposed', 'accepted', 'partially_filled']
    assert audit(first, quotes(MORNING), MORNING)['status'] == 'ok'
    assert tick(first, signal, quotes(MORNING), MORNING) == first
    later = MORNING + timedelta(minutes=5)
    second = tick(first, signal, quotes(later), later)
    assert len(second['orders']) == 1 and second['orders'][0]['filled_qty'] == 58
    assert audit(second, quotes(later), later)['status'] == 'ok'


def test_hourly_review_uses_prior_snapshot_and_cash_on_disagreement():
    now = MORNING.replace(hour=10, minute=0)
    state = fresh(MORNING)
    state['quotes'] = [{'at': MORNING.isoformat(), 'kpi200': 350.0}]
    signal, _ = build_signal(state, envelope(), quotes(now, index=351), now)
    assert signal['code'] == '069500'
    contrary, reason = build_signal(state, envelope(), quotes(now, index=349), now)
    assert contrary['code'] is None and contrary['target_weight'] == 0
    assert '현금' in reason


def test_stale_data_and_failed_qa_never_create_new_buy():
    now = MORNING
    state = fresh(now)
    bad = envelope()
    bad['outcome']['final_qa']['consistent'] = False
    assert build_signal(state, bad, quotes(now), now)[0] is None
    signal, _ = build_signal(state, envelope(), quotes(now), now)
    stale = quotes(now)
    stale['069500']['traded_at'] = (now-timedelta(minutes=16)).isoformat()
    assert tick(state, signal, stale, now)['orders'] == []
    crossed = quotes(now)
    crossed['069500'].update(bid=40_100, ask=40_000)
    assert tick(state, signal, crossed, now)['orders'] == []


def test_late_exit_is_partial_and_never_backdated():
    state = fresh(MORNING)
    signal, _ = build_signal(state, envelope(), quotes(MORNING), MORNING)
    bought = tick(state, signal, quotes(MORNING, long=10_000, capacity=300), MORNING)
    exit_time = MORNING.replace(hour=15, minute=20)
    first_exit = tick(bought, None, quotes(exit_time, long=10_000, capacity=30), exit_time)
    assert first_exit['orders'][-1]['side'] == 'sell'
    assert first_exit['orders'][-1]['status'] == 'partially_filled'
    assert first_exit['holdings']['069500']['qty'] < bought['holdings']['069500']['qty']
    assert all(fill['at'] == MORNING.isoformat() or fill['at'] == exit_time.isoformat()
               for fill in first_exit['fills'])
    assert audit(first_exit, quotes(exit_time, long=10_000), exit_time)['status'] == 'ok'


def test_unfilled_buy_is_cancelled_at_next_session():
    state = fresh(MORNING)
    signal, _ = build_signal(state, envelope(), quotes(MORNING), MORNING)
    entered = tick(state, signal, quotes(MORNING, capacity=0), MORNING)
    assert entered['orders'][0]['status'] == 'accepted'
    next_day = MORNING + timedelta(days=1)
    later = tick(entered, None, quotes(next_day), next_day)
    assert later['orders'][0]['status'] == 'cancelled'
    assert later['fills'] == []


def test_morning_buy_order_expires_after_0915():
    state = fresh(MORNING)
    signal, _ = build_signal(state, envelope(), quotes(MORNING), MORNING)
    entered = tick(state, signal, quotes(MORNING, capacity=0), MORNING)
    later = MORNING.replace(minute=20)
    result = tick(entered, None, quotes(later), later)
    assert result['orders'][0]['status'] == 'cancelled'
    assert result['fills'] == []


def test_qa_revocation_forces_paper_exit():
    state = fresh(MORNING)
    signal, _ = build_signal(state, envelope(), quotes(MORNING), MORNING)
    entered = tick(state, signal, quotes(MORNING, capacity=300), MORNING)
    later = MORNING+timedelta(minutes=5)
    result = tick(entered, None, quotes(later, capacity=300), later, force_exit=True)
    assert result['holdings'] == {}
    assert result['orders'][-1]['status'] == 'filled'
    assert audit(result, quotes(later), later)['status'] == 'ok'


def test_synthetic_liquidity_cap_uses_won_not_shares():
    state = fresh(MORNING)
    signal, _ = build_signal(state, envelope(), quotes(MORNING), MORNING)
    order = tick(state, signal, quotes(MORNING, long=10_000, capacity=100_000), MORNING)
    long_notional = order['fills'][0]['qty'] * order['fills'][0]['price']
    inverse_signal = {**signal, 'id': 'inverse-example', 'code': '114800'}
    inverse = tick(state, inverse_signal, quotes(MORNING, capacity=100_000), MORNING)
    inverse_notional = inverse['fills'][0]['qty'] * inverse['fills'][0]['price']
    assert order['fills'][0]['qty'] > 100
    assert inverse['fills'][0]['qty'] > order['fills'][0]['qty']
    assert 1_190_000 <= long_notional <= 1_200_000
    assert 1_190_000 <= inverse_notional <= 1_200_000


def test_etf_rotation_sells_old_position_before_buying_opposite():
    state = fresh(MORNING)
    morning, _ = build_signal(state, envelope(), quotes(MORNING), MORNING)
    for minute in (5, 10, 15):
        at = MORNING.replace(minute=minute)
        state = tick(state, morning, quotes(at), at)
    assert state['holdings'].get('069500', {}).get('qty', 0) > 0
    switch_at = MORNING.replace(hour=10, minute=0)
    opposite = {**morning, 'id': 'opposite-10', 'code': '114800',
                'created_at': switch_at.isoformat(),
                'expires_at': switch_at.replace(hour=11).isoformat()}
    for minute in (0, 5, 10):
        at = switch_at.replace(minute=minute)
        state = tick(state, opposite, quotes(at), at)
        assert not ('069500' in state['holdings'] and '114800' in state['holdings'])
        assert audit(state, quotes(at), at)['status'] == 'ok'


def test_audit_detects_tampering_and_stale_mark():
    state = fresh(MORNING)
    signal, _ = build_signal(state, envelope(), quotes(MORNING), MORNING)
    traded = tick(state, signal, quotes(MORNING), MORNING)
    traded['equity'] += 100
    assert audit(traded, quotes(MORNING), MORNING)['status'] == 'mismatch'
    traded['equity'] -= 100
    later = MORNING+timedelta(minutes=20)
    assert audit(traded, quotes(MORNING), later)['status'] == 'stale'


def test_private_ledger_excluded_and_public_summary_allowlisted():
    from pathlib import Path
    from security.scan import _excluded
    from web.export_site import render_fragment
    assert _excluded(Path('paper_intraday.json'))
    summary = {'label': '장중 가상 자동운용 실험', 'private_marker': 'not-for-public-page',
               'orders': [{'code': '069500', 'side': 'buy', 'qty': 1, 'private_marker': 'not-for-public-page'}]}
    page = render_fragment({'paper_intraday': summary})
    assert '장중 가상 자동운용 실험' in page
    assert 'not-for-public-page' not in page


def test_new_hour_signals_can_reenter_third_time_without_duplicate_orders():
    state = fresh(MORNING)
    morning, _ = build_signal(state, envelope(), quotes(MORNING), MORNING)
    for hour, code in ((9, '069500'), (10, None), (11, '069500'),
                       (12, None), (13, '069500')):
        at = MORNING.replace(hour=hour, minute=5 if hour == 9 else 0)
        signal = {**morning, 'id': f'synthetic-{hour}', 'code': code,
                  'target_weight': .05 if code else 0.,
                  'created_at': at.isoformat(),
                  'expires_at': (at + timedelta(hours=1)).isoformat()}
        state = tick(state, signal, quotes(at, long=10_000, capacity=300), at)
        assert audit(state, quotes(at, long=10_000), at)['status'] == 'ok'
    buys = [row for row in state['orders'] if row['side'] == 'buy']
    assert len(buys) == 3 and all(row['status'] == 'filled' for row in buys)
    repeat = MORNING.replace(hour=13, minute=5)
    again = tick(state, signal, quotes(repeat, long=10_000, capacity=300), repeat)
    assert len(again['orders']) == len(state['orders'])
