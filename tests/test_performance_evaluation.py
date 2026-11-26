"""Performance reporting uses only synthetic, settled records."""
from copy import deepcopy

from rules.performance_evaluation import evaluate
from memory.store import MemoryStore
from web.export_site import _evaluation_public


def _record(day, probability, actual, position, long_etf=None, short_etf=None):
    return {'verified': True, 'target_date': day, 'actual_session_date': day,
            'actual_direction': actual, 'up_probability': probability,
            'correct': (probability >= 50) == (actual == 'up'),
            'position': position,
            'etf_returns': {'long': long_etf, 'short': short_etf}}


def test_small_sample_neutral_brier_and_matched_etf_costs():
    rows = [_record('2026-10-06', 60, 'up', 1, 1.0, -0.8),
            _record('2026-10-07', 40, 'up', -1, 0.5, -0.4),
            _record('2026-10-08', 50, 'down', 0, -0.3, 0.2),
            _record('2026-10-09', 70, 'up', 1, None, None),
            {'verified': False, 'up_probability': 99, 'actual_direction': 'up'}]
    original = deepcopy(rows)
    result = evaluate(rows)
    assert rows == original
    direction = result['direction']
    assert (direction['correct'], direction['total'], direction['neutral_excluded']) == (2, 3, 1)
    assert direction['sample_sufficient'] is False
    assert direction['wilson_95_pct'][0] < 50 < direction['wilson_95_pct'][1]
    assert result['calibration']['total'] == 4
    assert result['calibration']['brier'] is not None
    etf = result['matched_etf']
    assert etf['days'] == 3 and etf['missing_or_invalid_excluded'] == 1
    assert etf['cost_bps_roundtrip'] == 10.0
    # 100% long on 10/6: 1% gross less 0.1% daily round-trip cost.
    # Cash on 10/8 pays no cost; 10/7 short loses when the inverse ETF falls.
    assert etf['ai_net_return_pct'] < etf['always_long_net_return_pct']


def test_duplicate_settlement_dates_and_invalid_prices_cannot_inflate_sample():
    rows = [_record('2026-10-06', 60, 'up', 1, 1.0, -1.0),
            _record('2026-10-06', 70, 'up', 1, 1.0, -1.0),
            _record('2026-10-08', 70, 'up', 1, float('nan'), -1.0),
            _record('2026-10-09', 70, 'up', 1, 0.2, -0.1)]
    result = evaluate(rows)['matched_etf']
    assert result['days'] == 1
    assert result['duplicate_date_excluded'] == 2
    assert result['missing_or_invalid_excluded'] == 1
    assert result['first_date'] == result['last_date'] == '2026-10-09'


def test_no_settled_records_never_claims_skill_or_etf_outperformance():
    result = evaluate([])
    assert result['direction']['accuracy_pct'] is None
    assert result['direction']['wilson_95_pct'] is None
    assert result['calibration']['brier'] is None
    assert result['matched_etf']['excess_percentage_points'] is None


def test_memory_stats_and_public_allowlist_keep_evaluation_separate():
    memory = MemoryStore(None)
    record = _record('2026-10-06', 60, 'up', 1, 1.0, -0.5)
    record.update({'confidence': '보통', 'stocks': [], 'agent_predictions': [],
                   'direction': 'up', 'us_indices': [], 'actual_change_pct': 0.8})
    memory._in_memory_data = {'a': record}
    evaluation = memory.compute_stats()['performance_evaluation']
    assert evaluation['direction']['total'] == 1
    evaluation['private_note'] = 'DO_NOT_SHARE'
    evaluation['matched_etf']['secret'] = 'DO_NOT_SHARE'
    public = _evaluation_public(evaluation)
    assert public['direction']['correct'] == 1
    assert 'DO_NOT_SHARE' not in str(public)
