"""Read-only, like-for-like evaluation of verified forecasts.

This is a diagnostic report, never an order signal. Historical hypothetical
ETF returns are kept separate from the simulated account's actual ledger.
"""
from __future__ import annotations

import math
from datetime import date

from execution.position import COST_BPS_ROUNDTRIP, performance, strategy_return_pct


def _number(value) -> bool:
    return type(value) in (int, float) and math.isfinite(value)


def _wilson_95(correct: int, total: int) -> list[float] | None:
    if total < 1:
        return None
    z = 1.959963984540054
    p = correct / total
    denominator = 1 + z * z / total
    centre = (p + z * z / (2 * total)) / denominator
    margin = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return [round(100 * max(0, centre - margin), 1), round(100 * min(1, centre + margin), 1)]


def evaluate(records: list[dict]) -> dict:
    """Only verified, finite, dated observations enter each stated denominator."""
    scored = []
    neutral = 0
    legacy = 0
    conflicts = 0
    for record in records:
        if not isinstance(record, dict) or record.get('verified') is not True:
            continue
        actual = record.get('actual_direction')
        if actual not in ('up', 'down'):
            continue
        probability = record.get('up_probability')
        if not _number(probability) or not 0 <= probability <= 100:
            legacy += 1
            continue
        scored.append(record)
        if probability == 50:
            neutral += 1
        elif record.get('correct') is not None and bool(record['correct']) != ((probability > 50) == (actual == 'up')):
            conflicts += 1

    directional = [r for r in scored if r['up_probability'] != 50]
    correct = sum((r['up_probability'] > 50) == (r['actual_direction'] == 'up') for r in directional)
    # (V3.37) 실제 등락이 ±0.3% 이내였던 날(사실상 보합)은 따로 센다 — 방향 맞히기 의미가 약하다
    flat = [r for r in directional if _number(r.get('actual_change_pct')) and abs(r['actual_change_pct']) < 0.3]
    decisive = [r for r in directional if r not in flat]
    decisive_correct = sum((r['up_probability'] > 50) == (r['actual_direction'] == 'up') for r in decisive)
    brier = (sum((r['up_probability'] / 100 - (r['actual_direction'] == 'up')) ** 2 for r in scored) / len(scored)
             if scored else None)

    # Historical counterfactual, matched to the *same* scored ETF dates and
    # charged the same daily round-trip cost on each active exposure. This is
    # not the account's fill price, realized P/L, or a continuous buy-and-hold.
    matched = {}
    excluded = 0
    for record in scored:
        session = record.get('actual_session_date')
        etf = record.get('etf_returns') or {}
        position = record.get('position')
        try:
            valid_date = isinstance(session, str) and date.fromisoformat(session).isoformat() == session
        except ValueError:
            valid_date = False
        if (not valid_date or not _number(position) or not -1 <= position <= 1 or
            not isinstance(etf, dict) or not _number(etf.get('long')) or not _number(etf.get('short')) or
            etf['long'] <= -100 or etf['short'] <= -100):
            excluded += 1
            continue
        # A settlement date can be shared by multiple target dates around a
        # holiday. Exclude duplicates rather than count one market day twice.
        matched.setdefault(session, []).append(record)

    ai_returns = []
    benchmark_returns = []
    dates = []
    duplicates = 0
    for session, candidates in sorted(matched.items()):
        if len(candidates) != 1:
            duplicates += len(candidates)
            continue
        record = candidates[0]
        etf = record['etf_returns']
        ai = strategy_return_pct(record['position'], 0.0,
                                 long_etf_ret_pct=etf['long'], short_etf_ret_pct=etf['short'])
        benchmark = strategy_return_pct(1.0, 0.0,
                                        long_etf_ret_pct=etf['long'], short_etf_ret_pct=etf['short'])
        ai_returns.append(ai['return_pct'])
        benchmark_returns.append(benchmark['return_pct'])
        dates.append(session)
    ai_perf = performance(ai_returns)
    benchmark_perf = performance(benchmark_returns)
    return {
        'direction': {'total': len(directional), 'correct': correct,
                      'accuracy_pct': round(100 * correct / len(directional), 1) if directional else None,
                      'wilson_95_pct': _wilson_95(correct, len(directional)),
                      'neutral_excluded': neutral, 'legacy_excluded': legacy,
                      'record_conflicts': conflicts, 'sample_sufficient': len(directional) >= 30,
                      'decisive': {'total': len(decisive), 'correct': decisive_correct,
                                   'accuracy_pct': round(100 * decisive_correct / len(decisive), 1) if decisive else None},
                      'flat_actual_days': len(flat),
                      'flat_actual_correct': sum((r['up_probability'] > 50) == (r['actual_direction'] == 'up') for r in flat)},
        'calibration': {'total': len(scored), 'brier': round(brier, 4) if brier is not None else None,
                        'coin_flip_brier': 0.25},
        'matched_etf': {'days': len(dates), 'first_date': dates[0] if dates else None,
                        'last_date': dates[-1] if dates else None,
                        'ai_net_return_pct': ai_perf.get('total_return_pct'),
                        'always_long_net_return_pct': benchmark_perf.get('total_return_pct'),
                        'excess_percentage_points': round(ai_perf['total_return_pct'] - benchmark_perf['total_return_pct'], 3) if dates else None,
                        'cost_bps_roundtrip': COST_BPS_ROUNDTRIP,
                        'missing_or_invalid_excluded': excluded, 'duplicate_date_excluded': duplicates},
    }
