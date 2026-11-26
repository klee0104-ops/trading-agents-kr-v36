"""The higher-risk book is prospective, isolated, and uses the same QA gate."""
from datetime import datetime

import json

from execution.aggressive_shadow import fresh_shadow, valid_shadow, read_comparison
from execution.split_portfolio import fresh, tick, valuation_audit, LONG, INVERSE
from data.sessions import KST

NOW = datetime(2026, 9, 30, 9, 5, tzinfo=KST)


def state():
    d = fresh(NOW)
    d['short_policy'] = 'etf_v2'
    return d


def envelope():
    plan = {'schema': 2, 'status': 'approved', 'target_date': str(NOW.date()),
            'generated_at': NOW.replace(hour=8).isoformat(), 'selected_code': INVERSE,
            'weight': .25,
            'forecasts': [{'code': c, 'up_probability': p} for c, p in
                          [('KPI200', 40), (LONG, 40), (INVERSE, 65)]]}
    return {'mode': 'real', 'saved_at': NOW.replace(hour=8).isoformat(),
            'outcome': {'target_date': str(NOW.date()), 'final_qa': {'consistent': True},
                        'final_result': {'etf_execution': plan}}}


def quotes(now=NOW, inv=100):
    return {c: {'date': str(now.date()), 'value': inv if c == INVERSE else 100,
                'market_status': 'OPEN', 'observed_at': now.isoformat(),
                'traded_at': now.isoformat()} for c in (LONG, INVERSE, 'KPI200')}


def test_same_approved_plan_only_changes_experimental_size():
    primary = state()
    shadow = fresh_shadow(NOW, primary['sleeves']['short']['equity'])
    assert valid_shadow(shadow)
    assert shadow['initial_cash'] == 12_000_000 and list(shadow['sleeves']) == ['short']
    a = tick(primary, envelope(), quotes(), NOW)
    b = tick(shadow, envelope(), quotes(), NOW)
    p = a['sleeves']['short']['holdings'][INVERSE]['qty']
    q = b['sleeves']['short']['holdings'][INVERSE]['qty']
    assert q > p and 1.55 < q / p < 1.65
    assert valuation_audit(a, NOW)['status'] == 'ok'
    assert valuation_audit(b, NOW)['status'] == 'ok'
    close = NOW.replace(hour=15, minute=20)
    b = tick(b, envelope(), quotes(close, inv=102), close)
    assert not b['sleeves']['short']['holdings']
    assert b['short_cycles'][0]['exit_reason'] == 'scheduled_exit'


def test_failed_qa_keeps_both_books_in_cash():
    e = envelope()
    e['outcome']['final_qa']['consistent'] = False
    a = tick(state(), e, quotes(), NOW)
    b = tick(fresh_shadow(NOW, 12_000_000), e, quotes(), NOW)
    assert not a['trades'] and not b['trades']


def test_late_experiment_start_never_replays_morning_order():
    late = NOW.replace(hour=15, minute=18)
    b = tick(fresh_shadow(late, 12_000_000), envelope(), quotes(late), late)
    assert not b['trades']
    assert not b['sleeves']['short']['holdings']


def test_comparison_uses_starting_equity_and_hides_invalid_ledger(tmp_path, monkeypatch):
    monkeypatch.setattr('data.sessions.kst_now', lambda: NOW)
    primary = tick(state(), envelope(), quotes(), NOW)
    shadow = tick(fresh_shadow(NOW, 12_000_000), envelope(), quotes(), NOW)
    main_path, shadow_path = tmp_path / 'primary.json', tmp_path / 'aggressive.json'
    main_path.write_text(json.dumps(primary), encoding='utf-8')
    shadow_path.write_text(json.dumps(shadow), encoding='utf-8')
    comparison = read_comparison(main_path, shadow_path)
    assert comparison['status'] == 'ok'
    assert comparison['experiment_period_pnl'] < comparison['primary_period_pnl'] < 0
    shadow['sleeves']['short']['equity'] += 10
    shadow_path.write_text(json.dumps(shadow), encoding='utf-8')
    comparison = read_comparison(main_path, shadow_path)
    assert comparison['status'] == 'pending'
    assert comparison['experiment_period_pnl'] is None


def test_promoted_policy_archives_shadow_comparison_without_revaluing_old_trades(tmp_path):
    primary = tick(state(), envelope(), quotes(), NOW)
    shadow = tick(fresh_shadow(NOW, 12_000_000), envelope(), quotes(), NOW)
    shadow['comparison_archive'] = {
        'at': NOW.isoformat(), 'comparable': True,
        'primary_equity': primary['sleeves']['short']['equity'],
        'experiment_equity': shadow['sleeves']['short']['equity'],
    }
    main_path, shadow_path = tmp_path / 'primary.json', tmp_path / 'aggressive.json'
    main_path.write_text(json.dumps(primary), encoding='utf-8')
    shadow_path.write_text(json.dumps(shadow), encoding='utf-8')
    archived = read_comparison(main_path, shadow_path)
    assert archived['status'] == 'archived'
    assert archived['primary_weight_pct'] == 25
    assert archived['experiment_weight_pct'] == 40
    assert archived['experiment_period_pnl'] < archived['primary_period_pnl'] < 0
    assert archived['experiment_trades'] == len(shadow['trades'])
