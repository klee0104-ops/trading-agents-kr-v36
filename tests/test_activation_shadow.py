"""Synthetic paper-only regression for the isolated activation policy."""
from datetime import datetime

from data.sessions import KST
from execution.shadow_v2 import (fresh_shadow, tick_shadow, valid_shadow, read_comparison,
                                 experimental_envelope)
from execution.split_portfolio import fresh, tick, valuation_audit
from rules.activation_policy import strong_threshold, shadow_weight

NOW = datetime(2026, 9, 30, 9, 5, tzinfo=KST)


def _envelope(probability=55, *, qa=True):
    plan = {'schema': 4, 'status': 'approved', 'target_date': str(NOW.date()),
            'generated_at': NOW.replace(hour=8).isoformat(), 'selected_code': '114800',
            'weight': .20, 'selection_basis': 'model_probability',
            'etf_qa': {'passed': qa},
            'forecasts': [{'code': c, 'up_probability': p} for c, p in
                          [('KPI200', 45), ('069500', 45), ('114800', probability)]]}
    return {'mode': 'real', 'saved_at': NOW.replace(hour=8).isoformat(),
            'outcome': {'target_date': str(NOW.date()),
                        'consensus': {'score': -.8}, 'quant': {'realized_vol_20': 40},
                        'final_result': {'etf_execution': plan}}}


def _quotes(now=NOW):
    return {code: {'date': str(now.date()), 'value': 100.,
                   'market_status': 'OPEN', 'observed_at': now.isoformat(),
                   'traded_at': now.isoformat()}
            for code in ('KPI200', '069500', '114800')}


def test_threshold_insufficient_ties_and_nan():
    assert strong_threshold([55] * 39) == 57
    assert strong_threshold([55] * 40) == 55
    assert strong_threshold([float('nan')] + [57] * 40) == 57
    assert strong_threshold([90] * 40) == 57
    from execution.leverage_contract import relative_probability_diagnostic
    plan = _envelope()['outcome']['final_result']['etf_execution']
    plan['selected_code'] = '069500'
    plan['forecasts'] = [{'code': 'KPI200', 'up_probability': 58},
                         {'code': '069500', 'up_probability': 58},
                         {'code': '114800', 'up_probability': 42}]
    diagnostic = relative_probability_diagnostic(plan, [])
    assert diagnostic['probability_gate_passed'] is True
    assert diagnostic['execution_approved'] is False


def test_shadow_sizing_is_bounded_and_keeps_probability_unchanged():
    original = _envelope()
    adjusted, detail = experimental_envelope(original, NOW, [55] * 40)
    assert original['outcome']['final_result']['etf_execution']['weight'] == .20
    assert adjusted['outcome']['final_result']['etf_execution']['weight'] == .50
    assert adjusted['outcome']['final_result']['etf_execution']['forecasts'] == original['outcome']['final_result']['etf_execution']['forecasts']
    assert detail['aligned_consensus'] is True
    assert shadow_weight(99, [], 1, float('nan'), '069500')['weight'] <= .50
    weak = _envelope(51)
    weak['outcome']['consensus']['score'] = 0
    held, reason = experimental_envelope(weak, NOW, [])
    assert held['outcome']['final_result']['etf_execution']['selected_code'] is None
    assert held['outcome']['final_result']['etf_execution']['weight'] == 0
    assert reason['status'] == 'cash_edge'


def test_shadow_has_same_quote_and_cost_but_independent_capital():
    primary = fresh(NOW)
    primary['short_policy'] = 'etf_v2'
    shadow = fresh_shadow(NOW, primary['sleeves']['short']['equity'])
    assert valid_shadow(shadow)
    envelope = _envelope()
    live = tick(primary, envelope, _quotes(), NOW)
    trial = tick_shadow(shadow, envelope, _quotes(), NOW, [55] * 40)
    assert len(live['trades']) == len(trial['trades']) == 1
    assert trial['trades'][0]['qty'] > live['trades'][0]['qty']
    assert valuation_audit(trial, NOW)['status'] == 'ok'
    assert primary['trades'] == [] and shadow['trades'] == []
    close = NOW.replace(hour=15, minute=20)
    ended = tick_shadow(trial, envelope, _quotes(close), close, [55] * 40)
    assert not ended['sleeves']['short']['holdings']
    assert ended['short_cycles'][0]['exit_reason'] == 'scheduled_exit'


def test_failed_qa_and_late_entry_do_not_buy():
    shadow = fresh_shadow(NOW, 12_000_000)
    held = tick_shadow(shadow, _envelope(qa=False), _quotes(), NOW, [])
    assert not held['trades']
    late = NOW.replace(hour=14)
    other = tick_shadow(fresh_shadow(late, 12_000_000), _envelope(), _quotes(late), late, [])
    assert not other['trades']


def test_scheduled_tick_reuses_primary_quote_snapshot_and_writes_isolated_shadow(tmp_path, monkeypatch):
    import json
    import requests
    from config import Config
    from data import sessions
    from tools.paper_portfolio import run_once, write

    path = tmp_path / 'paper_portfolio.json'
    write(path, fresh(NOW, with_medium=True))
    (tmp_path / 'memory.json').write_text('{}', encoding='utf-8')
    monkeypatch.setattr(Config, 'PAPER_SHADOW_V2_ENABLED', True)
    monkeypatch.setattr(Config, 'PAPER_SHADOW_V2_PATH', str(tmp_path / 'paper_shadow_v2.json'))
    monkeypatch.setattr(Config, 'PAPER_AGGRESSIVE_PATH', str(tmp_path / 'paper_aggressive.json'))
    monkeypatch.setattr(Config, 'PAPER_INTRADAY_ENABLED', False)
    monkeypatch.setattr(Config, 'MEMORY_DB_PATH', str(tmp_path / 'memory.json'))
    monkeypatch.setattr(Config, 'LONG_RESEARCH_PATH', str(tmp_path / 'missing_long.json'))
    monkeypatch.setattr(Config, 'SHORT_INTRADAY_REVIEW_PATH', str(tmp_path / 'missing_review.json'))
    monkeypatch.setattr(sessions, 'kst_now', lambda: NOW)
    calls = []

    class Response:
        def raise_for_status(self):
            return None
        def json(self):
            return {'localTradedAt': NOW.isoformat(), 'closePrice': '100', 'marketStatus': 'OPEN'}

    def get(url, **kwargs):
        calls.append(url)
        return Response()

    monkeypatch.setattr(requests, 'get', get)
    assert run_once(path, _envelope()) in ('updated', 'partial')
    primary = json.loads(path.read_text(encoding='utf-8'))
    shadow = json.loads((tmp_path / 'paper_shadow_v2.json').read_text(encoding='utf-8'))
    assert valid_shadow(shadow)
    assert primary['last_tick'] == shadow['last_tick']
    assert len([t for t in primary['trades'] if t['sleeve'] == 'short']) == 1
    assert len(shadow['trades']) == 1
    assert len(calls) <= 5  # shadow makes no additional HTTP request
    comparison = read_comparison(path, tmp_path / 'paper_shadow_v2.json', tmp_path / 'memory.json')
    assert comparison['status'] == 'collecting'
    assert comparison['trading_days'] == 1
    assert comparison['primary_return_pct'] is not None
    assert comparison['shadow_return_pct'] is not None
