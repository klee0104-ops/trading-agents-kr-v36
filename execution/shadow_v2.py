"""Isolated prospective paper ledger using the same verified quote snapshot."""
from __future__ import annotations

from copy import deepcopy
from datetime import datetime
import json
import math
from pathlib import Path

from execution.split_portfolio import fresh, tick
from execution.etf_contract import _approved_plan
from rules.activation_policy import (SHADOW_CAPITAL, SHADOW_NEUTRAL_PROBE_WEIGHT,
                                     shadow_weight)

MARKER = 'activation_shadow_v2'


def fresh_shadow(now: datetime, primary_equity: float) -> dict:
    from data.krx_calendar import next_trading_day_after
    state = fresh(now)
    state['sleeves'] = {'short': state['sleeves']['short']}
    state['sleeves']['short']['label'] = '단기 활성화 비교 원장'
    state['initial_cash'] = SHADOW_CAPITAL
    state['short_policy'] = 'etf_v2'
    state['short_policy_started_at'] = now.isoformat()
    state['legacy_trade_count'] = 0
    state['experiment'] = MARKER
    state['comparison_primary_base_equity'] = float(primary_equity)
    state['comparison_shadow_base_equity'] = SHADOW_CAPITAL
    state['comparison_start_date'] = (str(next_trading_day_after(now.date()))
                                     if now.hour > 9 or (now.hour == 9 and now.minute > 15)
                                     else str(now.date()))
    state['policy_version'] = 'v2_experimental'
    return state


def valid_shadow(state: dict) -> bool:
    return (isinstance(state, dict) and state.get('experiment') == MARKER
            and state.get('initial_cash') == SHADOW_CAPITAL
            and set(state.get('sleeves', {})) == {'short'}
            and state['sleeves']['short'].get('initial_cash') == SHADOW_CAPITAL
            and type(state.get('comparison_primary_base_equity')) in (int, float)
            and math.isfinite(state['comparison_primary_base_equity'])
            and state['comparison_primary_base_equity'] > 0
            and type(state.get('comparison_shadow_base_equity')) in (int, float)
            and math.isfinite(state['comparison_shadow_base_equity'])
            and state['comparison_shadow_base_equity'] > 0)


def earlier_probabilities(memory_path: str, today: str) -> list[float]:
    try:
        data = json.loads(Path(memory_path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        return []
    if not isinstance(data, dict):
        return []
    rows = sorted((r for r in data.values() if isinstance(r, dict)
                   and r.get('target_date', '') < today),
                  key=lambda r: r.get('target_date', ''))
    return [p for r in rows if type(p := r.get('up_probability_raw', r.get('up_probability'))) in (int, float)
            and math.isfinite(p) and 0 <= p <= 100][-40:]


def calibration_diagnostic(memory_path: str, envelope: dict | None, today: str) -> dict | None:
    """Early calibration is visible in shadow only; never rewrites the AI forecast."""
    from rules.calibrate import fit_calibration, apply_calibration
    try:
        records = json.loads(Path(memory_path).read_text(encoding='utf-8'))
        if not isinstance(records, dict):
            return None
        prediction = (((envelope or {}).get('outcome') or {}).get('final_result') or {}).get('kospi_prediction') or {}
        probability = prediction.get('up_probability_raw', prediction.get('up_probability'))
        if type(probability) not in (int, float) or not math.isfinite(probability):
            return None
        model = fit_calibration(list(records.values()), as_of=today, experimental=True)
        result = apply_calibration(probability, model)
        return {'sample_count': model['total'], 'active': model['active'],
                'raw_probability': probability, 'calibrated_probability': result['p'],
                'applied': result['applied']}
    except (OSError, ValueError, TypeError, KeyError):
        return None


def experimental_envelope(envelope: dict | None, now: datetime,
                          earlier_probabilities: list[float]) -> tuple[dict | None, dict]:
    """Derive size only from an original, QA-approved plan; do not alter AI odds."""
    if not isinstance(envelope, dict):
        return envelope, {'status': 'no_signal'}
    outcome = envelope.get('outcome') or {}
    original = _approved_plan(outcome, now)
    if not original or original.get('schema') != 4:
        return None, {'status': 'no_approved_plan'}
    code = original.get('selected_code')
    if code is None:
        return envelope, {'status': 'cash_plan'}
    copied = deepcopy(envelope)
    plan = copied['outcome']['final_result']['etf_execution']
    if original.get('selection_basis') == 'neutral_probe':
        plan['weight'] = SHADOW_NEUTRAL_PROBE_WEIGHT
        detail = {'weight': plan['weight'], 'status': 'neutral_probe'}
    else:
        row = next((r for r in plan['forecasts'] if r['code'] == code), None)
        if not row:
            return None, {'status': 'missing_forecast'}
        probability = row['up_probability']
        consensus = (outcome.get('consensus') or {}).get('score')
        vol = (outcome.get('quant') or {}).get('realized_vol_20')
        detail = shadow_weight(probability, earlier_probabilities, consensus, vol, code)
        plan['weight'] = detail['weight']
        if plan['weight'] == 0:
            plan['selected_code'] = None
            plan['selection_basis'] = 'cash_edge'
            detail['status'] = 'cash_edge'
        else:
            detail['status'] = 'sized'
    plan['shadow_policy'] = MARKER
    return copied, detail


def tick_shadow(state: dict, envelope: dict | None, quotes: dict, now: datetime,
                earlier_probabilities: list[float], memory_path: str | None = None) -> dict:
    if not valid_shadow(state):
        raise ValueError('Invalid shadow v2 ledger')
    adjusted, detail = experimental_envelope(envelope, now, earlier_probabilities)
    updated = tick(state, adjusted, quotes, now, long_report=None)
    if updated != state:
        updated['last_policy_review'] = {'at': now.isoformat(), **detail}
        from execution.leverage_contract import relative_probability_diagnostic
        plan = (((envelope or {}).get('outcome') or {}).get('final_result') or {}).get('etf_execution') or {}
        updated['last_policy_review']['leverage_probability_only'] = relative_probability_diagnostic(
            plan, earlier_probabilities)
        if memory_path:
            updated['last_policy_review']['calibration'] = calibration_diagnostic(
                memory_path, envelope, str(now.date()))
    return updated


def read_comparison(primary_path: str, shadow_path: str, memory_path: str) -> dict | None:
    """Read-only normalized comparison; incomplete/stale marks never become results."""
    from execution.split_portfolio import valuation_audit
    from rules.performance_evaluation import evaluate

    path = Path(shadow_path)
    if not path.exists():
        return None
    shadow = json.loads(path.read_text(encoding='utf-8'))
    if not valid_shadow(shadow):
        raise ValueError('Invalid activation shadow ledger')
    primary = json.loads(Path(primary_path).read_text(encoding='utf-8'))
    start = shadow.get('comparison_start_date') or shadow['created_at'][:10]
    common = (bool(shadow.get('comparison_started_at'))
              and primary.get('last_tick') == shadow.get('last_tick')
              and valuation_audit(primary)['status'] == 'ok'
              and valuation_audit(shadow)['status'] == 'ok')
    main_book = (primary.get('sleeves') or {}).get('short') or {}
    trial_book = shadow['sleeves']['short']
    main_base = shadow['comparison_primary_base_equity']
    trial_base = shadow.get('comparison_shadow_base_equity', SHADOW_CAPITAL)
    main_equity, trial_equity = main_book.get('equity'), trial_book.get('equity')
    main_return = 100 * (main_equity / main_base - 1) if common and main_equity else None
    trial_return = 100 * (trial_equity / trial_base - 1) if common and trial_equity else None
    main_rows = {r['date']: r for r in primary.get('equity_history', [])
                 if isinstance(r, dict) and r.get('date', '') >= start}
    trial_rows = {r['date']: r for r in shadow.get('equity_history', [])
                  if isinstance(r, dict) and r.get('date', '') >= start}
    dates = sorted(set(main_rows) & set(trial_rows))

    def drawdown(rows, base):
        peak = base
        worst = 0.0
        for day in dates:
            value = rows[day].get('equity')
            if type(value) not in (int, float) or not math.isfinite(value) or value <= 0:
                return None
            peak = max(peak, value)
            worst = min(worst, 100 * (value / peak - 1))
        return round(worst, 4)

    main_dd = drawdown(main_rows, main_base) if common else None
    trial_dd = drawdown(trial_rows, trial_base) if common else None
    exposures = [trial_rows[d].get('exposure_pct') for d in dates]
    exposure = (round(sum(exposures) / len(exposures), 2)
                if exposures and all(type(v) in (int, float) and math.isfinite(v) for v in exposures) else None)
    main_trades = [t for t in primary.get('trades', []) if t.get('sleeve') == 'short'
                   and str(t.get('at', '')) >= str(shadow.get('comparison_started_at') or shadow['created_at'])]
    trial_trades = [t for t in shadow.get('trades', [])
                    if str(t.get('at', '')) >= str(shadow.get('comparison_started_at') or shadow['created_at'])]
    try:
        raw = json.loads(Path(memory_path).read_text(encoding='utf-8'))
        records = [r for r in raw.values() if isinstance(r, dict) and r.get('target_date', '') >= start]
        scored = evaluate(records)
        direction = scored['direction']
        calibration = scored['calibration']
        neutral_count = direction['neutral_excluded']
        odds_count = calibration['total']
    except (OSError, ValueError, TypeError, KeyError):
        direction = {}; calibration = {}; neutral_count = 0; odds_count = 0
    ready = common and len(dates) >= 10 and odds_count > 0
    mdd_condition = (trial_dd is not None and main_dd is not None
                     and (abs(trial_dd) <= 1.5 * abs(main_dd) if main_dd else trial_dd == 0))
    criteria = (trial_return > main_return and mdd_condition
                and neutral_count / odds_count <= .10) if ready else None
    return {'status': 'review_ready' if ready else 'collecting' if common else 'valuation_pending',
            'started_at': shadow.get('comparison_started_at') or shadow['created_at'],
            'start_date': start, 'last_tick': shadow.get('last_tick'),
            'trading_days': len(dates), 'minimum_days': 10,
            'primary_return_pct': round(main_return, 4) if main_return is not None else None,
            'shadow_return_pct': round(trial_return, 4) if trial_return is not None else None,
            'primary_max_drawdown_pct': main_dd, 'shadow_max_drawdown_pct': trial_dd,
            'primary_fills': len(main_trades), 'shadow_fills': len(trial_trades),
            'shadow_average_exposure_pct': exposure,
            'direction_accuracy_pct': direction.get('accuracy_pct'),
            'brier': calibration.get('brier'),
            'exact_fifty_ratio_pct': round(100 * neutral_count / odds_count, 2) if odds_count else None,
            'criteria_met': bool(criteria) if ready else None,
            'note': '동일 시각 시세·거래비용 가정의 가상 비교. AI 예측 확률·채점은 두 원장이 공유하며 자동 전환은 하지 않습니다.'}
