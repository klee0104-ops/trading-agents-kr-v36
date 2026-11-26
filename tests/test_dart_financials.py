"""OpenDART financial reconciliation with synthetic statements only."""
from datetime import datetime, timedelta, timezone
from io import BytesIO
from unittest.mock import patch
import json
import zipfile

from agents.long_team import research_ready
from data.dart_financials import ACCOUNT, corp_codes, reconcile
from data.long_research import normalize_fundamentals
from memory.long_report import decision, public_summary
from security.secret_store import SecretStr


NOW = datetime(2026, 10, 1, 10, tzinfo=timezone(timedelta(hours=9)))
KEY = SecretStr('SYNTHETIC_DART_KEY_FOR_TESTS')
CORP = '00126380'


def statement(year, code, revenue, income, eps, cfo, ppe, equity, debt,
              quarter_revenue=None, section='IS'):
    rows = []
    values = {'revenue': revenue, 'income': income, 'eps': eps, 'cfo': cfo,
              'ppe': ppe, 'intangible': 0, 'equity': equity}
    for name, value in values.items():
        sj, aid = ACCOUNT[name]
        row = {'sj_div': section if sj == 'IS' else sj, 'account_id': aid,
               'currency': 'KRW', 'thstrm_amount': str(quarter_revenue if name == 'revenue' and quarter_revenue is not None else value),
               'thstrm_add_amount': str(value)}
        rows.append(row)
    rows.append({'sj_div': 'BS', 'account_id': 'ifrs-full_CurrentBorrowings',
                 'currency': 'KRW', 'thstrm_amount': str(debt)})
    return rows, f'{year}{code}00001'[:14].ljust(14, '0')


ANNUAL = statement(2025, '11011', 400, 40, 4, 80, 20, 100, 20)
CURRENT = statement(2026, '11012', 240, 24, 2.4, 48, 12, 110, 22, 120, 'CIS')
PRIOR = statement(2025, '11012', 200, 20, 2, 40, 10, 90, 18, 100, 'CIS')


def yahoo_fact():
    dates = ['2026-06-30', '2026-03-31', '2025-12-31', '2025-09-30', '2025-06-30']
    periods = [{'date': d, 'revenue': 120 if i < 2 else 100,
                'income': 12 if i < 2 else 10, 'fcf': 18 if i < 2 else 15,
                'eps': None if i == 2 else (1.2 if i < 2 else 1)}
               for i, d in enumerate(dates)]
    balance = {'date': dates[0], 'debt': 22, 'equity': 110, 'shares': 10}
    spec = {'name': '가상기업', 'symbol': '005930.KS', 'sector': 'Technology'}
    fact = normalize_fundamentals('005930', periods, balance, 10, NOW, spec=spec)
    fact['sector'] = 'Technology'
    return fact, periods, balance


def fake_report(_key, _corp, year, code):
    return {(2025, '11011'): ANNUAL, (2026, '11012'): CURRENT,
            (2025, '11012'): PRIOR}[(year, code)]


def test_missing_yahoo_eps_gets_labeled_derived_ttm_after_core_checks():
    fact, periods, balance = yahoo_fact()
    assert fact['status'] == 'insufficient'
    with patch('data.dart_financials._report', side_effect=fake_report):
        reconcile(fact, periods, balance, CORP, KEY, NOW)
    verification = fact['dart_verification']
    assert verification['status'] == 'verified_derived_eps'
    assert verification['eps_basis'] == 'derived_annual_plus_current_ytd_minus_prior_ytd'
    assert fact['metrics']['ttm_eps'] == 4.4
    assert fact['status'] == 'ok' and fact['eligible'] and research_ready(fact)
    assert all(row['match'] for row in verification['checks'])
    assert KEY.reveal() not in json.dumps(fact)


def test_mismatch_holds_and_prevents_research():
    fact, periods, balance = yahoo_fact()
    periods[0]['fcf'] = 4
    fact['metrics']['ttm_fcf'] = 52
    with patch('data.dart_financials._report', side_effect=fake_report):
        reconcile(fact, periods, balance, CORP, KEY, NOW)
    assert fact['dart_verification']['status'] == 'mismatch'
    assert not fact['eligible'] and not research_ready(fact)


def test_provider_error_never_exposes_key():
    fact, periods, balance = yahoo_fact()
    with patch('data.dart_financials._report', side_effect=ValueError('dart_' + KEY.reveal())):
        reconcile(fact, periods, balance, CORP, KEY, NOW)
    assert fact['dart_verification']['status'] == 'unavailable'
    assert KEY.reveal() not in json.dumps(fact)


def test_old_approved_plan_is_blocked_until_dart_verified():
    state = {'dart_financial_required': True, 'news': {'status': 'ok', 'checked_at': NOW.isoformat()},
             'report': {'status': 'approved', 'generated_at': NOW.isoformat(),
                        'valid_until': (NOW + timedelta(days=1)).isoformat(),
                        'fundamentals': [{'code': '005930', 'period_end': '2026-06-30'}],
                        'allocations': [{'code': '005930', 'weight': .1, 'buy_below': 1}]}}
    gate = decision(state, NOW)
    assert gate['status'] == 'approved'
    assert '005930' in gate['blocked_codes']
    assert 'OpenDART 재무·EPS 대조 미완료' in gate['block_by_code']['005930']


def test_public_validation_uses_only_official_receipt_urls():
    fact, periods, balance = yahoo_fact()
    with patch('data.dart_financials._report', side_effect=fake_report):
        reconcile(fact, periods, balance, CORP, KEY, NOW)
    view = public_summary({'fundamentals': [fact], 'news': {'status': 'ok', 'checked_at': NOW.isoformat()}}, NOW)
    validation = view['candidates'][0]['dart_verification']
    assert validation['status'] == 'verified_derived_eps'
    assert all(x['url'].startswith('https://dart.fss.or.kr/dsaf001/main.do?rcpNo=') for x in validation['receipts'])
    assert KEY.reveal() not in json.dumps(view)


def test_year_end_compares_standalone_q4_revenue_and_reported_eps():
    annual = statement(2025, '11011', 400, 40, 4, 80, 20, 100, 20)
    third = statement(2025, '11014', 300, 30, 3, 60, 15, 95, 19)
    old_annual = statement(2024, '11011', 360, 36, 3.6, 72, 18, 90, 18)
    old_third = statement(2024, '11014', 270, 27, 2.7, 54, 13.5, 85, 17)
    responses = {(2025, '11011'): annual, (2025, '11014'): third,
                 (2024, '11011'): old_annual, (2024, '11014'): old_third}
    periods = [{'date': '2025-12-31', 'revenue': 100}]
    balance = {'equity': 100, 'debt': 20}
    fact = {'metrics': {'ttm_income': 40, 'ttm_fcf': 60, 'ttm_eps': 4,
                        'price': 10, 'revenue_yoy_pct': 100 / 90 - 1,
                        'debt_equity': .2, 'pe': 2.5, 'pb': 1},
            'status': 'ok', 'eligible': True}
    periods.append({'date': '2024-12-31', 'revenue': 90})
    with patch('data.dart_financials._report', side_effect=lambda _k, _c, y, code: responses[(y, code)]):
        reconcile(fact, periods, balance, CORP, KEY, NOW)
    assert fact['dart_verification']['status'] == 'verified'
    assert fact['dart_verification']['eps_basis'] == 'reported_annual'
    assert len(fact['dart_verification']['receipts']) == 4


def test_derived_eps_rejected_when_implied_share_base_changes():
    fact, periods, balance = yahoo_fact()
    altered = statement(2025, '11012', 200, 20, 10, 40, 10, 90, 18, 100, 'CIS')
    with patch('data.dart_financials._report',
               side_effect=lambda key, corp, year, code: altered if (year, code) == (2025, '11012')
               else fake_report(key, corp, year, code)):
        reconcile(fact, periods, balance, CORP, KEY, NOW)
    assert fact['dart_verification']['status'] == 'partial_eps'
    assert fact['dart_verification']['reason'] == 'EPS 산출 주식수 변동으로 누적 계산 보류'
    assert fact['metrics']['ttm_eps'] is None
    assert not fact['eligible']
    assert research_ready(fact)


def test_missing_official_eps_still_checks_core_but_holds_buy():
    fact, periods, balance = yahoo_fact()
    no_eps = statement(2026, '11012', 240, 24, 2.4, 48, 12, 110, 22, 120, 'CIS')
    no_eps[0][:] = [r for r in no_eps[0] if r.get('account_id') != ACCOUNT['eps'][1]]
    with patch('data.dart_financials._report',
               side_effect=lambda key, corp, year, code: no_eps if (year, code) == (2026, '11012')
               else fake_report(key, corp, year, code)):
        reconcile(fact, periods, balance, CORP, KEY, NOW)
    assert fact['dart_verification']['status'] == 'partial_eps'
    assert all(c['match'] for c in fact['dart_verification']['checks'])
    assert fact['metrics']['ttm_eps'] is None
    assert not fact['eligible']


def test_debt_scope_difference_is_partial_and_does_not_approve_buy():
    # (V3.34) 정의 차이로 다르더라도 한쪽이 부채/자본 1을 넘으면 기존처럼 보류
    fact, periods, balance = yahoo_fact()
    balance['debt'] = 150
    with patch('data.dart_financials._report', side_effect=fake_report):
        reconcile(fact, periods, balance, CORP, KEY, NOW)
    assert fact['dart_verification']['status'] == 'partial_debt'
    assert fact['metrics']['ttm_eps'] == 4.4
    assert not fact['eligible']
    assert research_ready(fact)
    assert [c['metric'] for c in fact['dart_verification']['checks'] if not c['match']] == ['debt']


def test_debt_scope_difference_tolerated_when_both_definitions_pass():
    # (V3.34) Yahoo 총부채(리스 포함)와 공시 차입금이 달라도 둘 다 기준 이내면 막지 않고 큰 부채비율을 적용
    fact, periods, balance = yahoo_fact()
    balance['debt'] = 50
    with patch('data.dart_financials._report', side_effect=fake_report):
        reconcile(fact, periods, balance, CORP, KEY, NOW)
    v = fact['dart_verification']
    assert v['status'] == 'verified_derived_eps' and 'debt_scope_note' in v
    assert abs(fact['metrics']['debt_equity'] - 50 / 110) < 1e-9
    assert fact['eligible']


def test_corp_registry_filters_and_rejects_xml_entities():
    xml = b'<result><list><corp_code>00126380</corp_code><stock_code>005930</stock_code></list><list><corp_code>00164779</corp_code><stock_code>000660</stock_code></list></result>'
    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('CORPCODE.xml', xml)
    with patch('data.dart_financials._download', return_value=stream.getvalue()):
        assert corp_codes(KEY, ['005930']) == {'005930': '00126380'}
    stream = BytesIO()
    with zipfile.ZipFile(stream, 'w') as archive:
        archive.writestr('CORPCODE.xml', b'<!DOCTYPE x><result/>')
    with patch('data.dart_financials._download', return_value=stream.getvalue()):
        try:
            corp_codes(KEY, ['005930'])
            assert False, 'unsafe XML accepted'
        except ValueError:
            pass


def test_hourly_cycle_rechecks_financials_once_even_if_yahoo_was_checked_today(tmp_path, monkeypatch):
    from data.long_research import UNIVERSE
    from tools.long_research import cycle
    from tools.paper_portfolio import write
    import data.long_research as data
    import data.long_universe as catalog_module
    import data.dart_disclosures as disclosure_module
    import security.secret_store as secrets
    import os

    path = tmp_path / 'long.json'
    book = tmp_path / 'paper.json'
    write(path, {'last_model_review_at': NOW.isoformat(),
                 'last_candidate_attempt_day': str(NOW.date()),
                 'fundamentals_checked_day': str(NOW.date()),
                 'fundamentals': [{'code': '005930', 'status': 'ok'}]})
    calls = []
    monkeypatch.setenv('TA_HARNESS_OFFLINE', '0')
    monkeypatch.setattr(catalog_module, 'update_catalog',
                        lambda old, now, held, short_names: {'watchlist': ['005930'],
                                                               'review_history': {},
                                                               'instruments': {'005930': {**UNIVERSE['005930'], 'sector': 'Technology'}},
                                                               'status': 'ok', 'checked_at': now.isoformat()})
    monkeypatch.setattr(catalog_module, 'choose_batch', lambda *args: ['005930'])
    monkeypatch.setattr(disclosure_module, 'poll', lambda *args: {'status': 'ok', 'events': []})
    monkeypatch.setattr(secrets, 'get_api_key', lambda provider: KEY)
    monkeypatch.setattr(data, 'collect_news', lambda now, held, universe: {'status': 'ok', 'events': [], 'checked_at': now.isoformat()})
    monkeypatch.setattr(data, 'collect_fundamentals',
                        lambda now, universe, dart_key=None: calls.append((list(universe), bool(dart_key))) or
                        [{'code': '005930', 'status': 'ok', 'metrics': {}, 'dart_verification': {'status': 'verified'}}])
    cycle(path, book, NOW, expanded=True)
    cycle(path, book, NOW + timedelta(hours=1), expanded=True)
    assert calls == [(['005930'], True)]
    assert json.loads(path.read_text(encoding='utf-8'))['dart_financial_required'] is True
