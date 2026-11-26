"""Synthetic OpenDART responses; never use an account key or live requests."""
from datetime import datetime, timedelta, timezone
from unittest.mock import patch
import json

from data.dart_disclosures import poll
from memory.long_report import public_summary
from security.secret_store import SecretStr


NOW = datetime(2026, 10, 1, 9, 40, tzinfo=timezone(timedelta(hours=9)))


class Response:
    status_code = 200
    content = b'{}'

    def __init__(self, payload):
        self.payload = payload

    def json(self):
        return self.payload


def item(receipt, code='005930', title='사업보고서'):
    return {'rcept_no': receipt, 'rcept_dt': '20261001', 'stock_code': code,
            'corp_name': '삼성전자', 'report_nm': title}


def test_pages_filter_and_rate_limit():
    calls = []

    def fake_get(url, **kwargs):
        calls.append(kwargs)
        page = kwargs['params']['page_no']
        return Response({'status': '000', 'total_page': 2,
                         'list': [item('20261001000001'), item('20261001000002', '000660')] if page == 1
                         else [item('20261001000003', title='<script>보안 공시')]})

    with patch('data.dart_disclosures.requests.get', side_effect=fake_get):
        state = poll(NOW, {'005930'}, SecretStr('SYNTHETIC_SECRET_FOR_TESTS'))
        assert state['status'] == 'ok'
        assert len(state['events']) == 2
        assert len(calls) == 2
        assert all(c['allow_redirects'] is False for c in calls)
        assert all(c['timeout'] == 12 for c in calls)
        assert '<' not in json.dumps(state)
        assert 'SYNTHETIC_SECRET_FOR_TESTS' not in json.dumps(state)
        again = poll(NOW + timedelta(minutes=30), {'005930'}, SecretStr('SYNTHETIC_SECRET_FOR_TESTS'), state)
        assert again == state
        assert len(calls) == 2


def test_key_added_after_not_configured_polls_immediately():
    previous = poll(NOW, {'005930'}, None)
    with patch('data.dart_disclosures.requests.get', return_value=Response({'status': '013'})) as get:
        state = poll(NOW + timedelta(minutes=1), {'005930'}, SecretStr('SYNTHETIC_SECRET_FOR_TESTS'), previous)
    assert get.call_count == 1
    assert state['status'] == 'ok'


def test_changed_tracking_set_repolls_within_hour():
    key = SecretStr('SYNTHETIC_SECRET_FOR_TESTS')
    with patch('data.dart_disclosures.requests.get', return_value=Response({'status': '013'})) as get:
        first = poll(NOW, {'005930'}, key)
        second = poll(NOW + timedelta(minutes=5), {'005930', '000660'}, key, first)
    assert get.call_count == 2
    assert first['tracked_count'] == 1 and second['tracked_count'] == 2
    assert first['tracked_codes_hash'] != second['tracked_codes_hash']
    assert 'SYNTHETIC_SECRET_FOR_TESTS' not in json.dumps(second)


def test_failure_preserves_prior_events_and_is_not_success():
    prior = {'status': 'ok', 'checked_at': (NOW - timedelta(hours=2)).isoformat(),
             'last_success_at': (NOW - timedelta(hours=2)).isoformat(),
             'events': [{'id': '20261001000001', 'filed_date': '20261001', 'title': '확인됨'}]}
    with patch('data.dart_disclosures.requests.get', return_value=Response({'status': '010'})):
        state = poll(NOW, {'005930'}, SecretStr('SYNTHETIC_SECRET_FOR_TESTS'), prior)
    assert state['status'] == 'unavailable'
    assert state['partial_reason'] == 'api_status_010'
    assert state['events'] == prior['events']
    assert state['last_success_at'] == prior['last_success_at']


def test_page_limit_marks_partial():
    with patch('data.dart_disclosures.requests.get', return_value=Response({'status': '000', 'total_page': 11, 'list': []})) as get:
        state = poll(NOW, {'005930'}, SecretStr('SYNTHETIC_SECRET_FOR_TESTS'))
    assert get.call_count == 10
    assert state['status'] == 'partial'
    assert state['partial_reason'] == 'page_limit'
    assert state['last_success_at'] is None


def test_public_summary_rebuilds_official_link_and_exposes_no_key():
    private = {'dart': {'status': 'ok', 'checked_at': NOW.isoformat(),
                        'api_key': 'SYNTHETIC_SECRET_FOR_TESTS',
                        'events': [{'id': '20261001000001', 'code': '005930',
                                    'name': '삼성전자', 'title': '사업보고서',
                                    'filed_date': '20261001',
                                    'url': 'https://attacker.invalid/open'}]}}
    public = public_summary(private, NOW)
    assert public['dart_events'][0]['url'] == 'https://dart.fss.or.kr/dsaf001/main.do?rcpNo=20261001000001'
    assert 'SYNTHETIC_SECRET_FOR_TESTS' not in json.dumps(public)
