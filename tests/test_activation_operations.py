import json
from datetime import datetime, timedelta

import pytest

from data.sessions import KST
from execution.exposure_alert import low_exposure_warning
from execution.split_portfolio import enable_long_core, fresh, review_long_core_stop, tick
from memory.long_report import public_summary
from tools.migrate_consensus import migrate

NOW = datetime(2026, 10, 8, 10, tzinfo=KST)


def test_consensus_migration_backup_and_restore(tmp_path):
    path = tmp_path / 'memory.json'
    original = {'2026-10-07': {'target_date': '2026-10-07', 'up_probability': 51,
                               'verified': True}}
    path.write_text(json.dumps(original), encoding='utf-8')
    assert migrate(path)['missing'] == 1
    assert json.loads(path.read_text(encoding='utf-8')) == original
    result = migrate(path, apply=True, now=NOW)
    assert result['applied'] and result['missing'] == 1
    assert json.loads(path.read_text(encoding='utf-8'))['2026-10-07']['consensus_score'] is None
    assert json.loads(open(result['backup'], encoding='utf-8').read()) == original
    assert not migrate(path, apply=True)['applied']


def test_long_core_stop_requires_review_then_explicit_resume():
    state = enable_long_core(fresh(NOW, with_medium=True), NOW)
    state['sleeves']['long_core']['paused'] = True
    state['sleeves']['long_core']['decision_key'] = 'core-initial-entry'
    state['sleeves']['long_core']['equity'] = 2_000_000
    state['sleeves']['long_core']['cash'] = 2_000_000
    with pytest.raises(ValueError):
        review_long_core_stop(state, NOW, '검토자', '사유가 충분히 기록됨', resume=True)
    reviewed = review_long_core_stop(state, NOW, '검토자', '중지 원인과 재진입 위험 점검')
    assert reviewed['sleeves']['long_core']['paused'] is True
    assert state.get('capital_events') is None
    resumed = review_long_core_stop(reviewed, NOW + timedelta(minutes=5),
                                    '검토자', '시세와 위험 한도 재확인 후 재개', resume=True)
    assert resumed['sleeves']['long_core']['paused'] is False
    assert resumed['sleeves']['long_core']['risk_restart_peak'] == 2_000_000
    assert resumed['sleeves']['long_core']['decision_key'] == ''
    assert [e['type'] for e in resumed['capital_events']] == ['long_core_stop_review', 'long_core_stop_resume']


def test_exposure_warning_requires_five_consecutive_sessions():
    days = ['2026-10-12', '2026-10-13', '2026-10-14', '2026-10-15', '2026-10-16']
    rows = [{'date': d, 'exposure_pct': 9.9, 'cash_pct': 90.1} for d in days]
    assert low_exposure_warning(rows)['sessions'] == 5
    assert low_exposure_warning(rows[:4]) is None
    rows[2]['exposure_pct'] = 10.0
    assert low_exposure_warning(rows) is None


def test_long_gate_summary_counts_without_relaxing_policy():
    state = {'report': {'status': 'held', 'fundamentals': [
        {'code': '005930', 'name': '삼성전자', 'status': 'insufficient',
         'metrics': {}, 'missing': ['EPS 없음']}], 'allocations': []}}
    result = public_summary(state, NOW)
    assert sum(r['count'] for r in result['gate_counts']) == len(result['candidates'])
    assert result['allocations'] == []
