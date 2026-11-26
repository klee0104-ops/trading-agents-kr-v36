import json
from datetime import datetime

from data.sessions import KST
from tools.morning_retry_once import same_day_analysis, should_retry


def test_retry_only_when_today_analysis_is_missing(tmp_path):
    now = datetime(2026, 10, 22, 8, 20, tzinfo=KST)
    outcome = tmp_path / 'outcome.json'
    status = tmp_path / 'status.json'
    assert should_retry(now, outcome, status)[0]
    outcome.write_text(json.dumps({'mode': 'real', 'saved_at': now.replace(hour=7).isoformat(),
                                   'outcome': {'target_date': str(now.date())}}), encoding='utf-8')
    assert same_day_analysis(outcome, str(now.date()))
    assert not should_retry(now, outcome, status)[0]
    assert not should_retry(now.replace(hour=9), outcome, status)[0]
