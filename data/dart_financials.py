"""Conservative OpenDART CFS reconciliation for the long-term paper portfolio.

Only exact XBRL account IDs and the official company-code map are accepted.
No filing text, credentials or raw provider responses enter saved state.
"""
from datetime import datetime
from io import BytesIO
import calendar
import json
import re
import zipfile
from xml.etree import ElementTree as ET

import requests

from data.long_research import finite


BASE = 'https://opendart.fss.or.kr/api/'
CORP_URL = BASE + 'corpCode.xml'
FIN_URL = BASE + 'fnlttSinglAcntAll.json'
MAX_ZIP = 8_000_000
MAX_XML = 50_000_000
MAX_JSON = 2_000_000
ACCOUNT = {
    'revenue': ('IS', 'ifrs-full_Revenue'),
    'income': ('IS', 'ifrs-full_ProfitLossAttributableToOwnersOfParent'),
    'eps': ('IS', 'ifrs-full_DilutedEarningsLossPerShare'),
    'cfo': ('CF', 'ifrs-full_CashFlowsFromUsedInOperatingActivities'),
    'ppe': ('CF', 'ifrs-full_PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities'),
    'intangible': ('CF', 'ifrs-full_PurchaseOfIntangibleAssetsClassifiedAsInvestingActivities'),
    'equity': ('BS', 'ifrs-full_EquityAttributableToOwnersOfParent'),
}
DEBT_IDS = (
    'dart_CurrentLoansReceived',
    'ifrs-full_CurrentPortionOfLongtermBorrowings',
    'ifrs-full_NoncurrentPortionOfNoncurrentBondsIssued',
    'ifrs-full_NoncurrentPortionOfNoncurrentLoansReceived',
    'ifrs-full_CurrentBorrowings',
    'ifrs-full_NoncurrentBorrowings',
    'ifrs-full_CurrentLeaseLiabilities',
    'ifrs-full_NoncurrentLeaseLiabilities',
    'ifrs-full_CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings',
    'ifrs-full_LongtermBorrowings',
)
REPORT = {3: '11013', 6: '11012', 9: '11014', 12: '11011'}


def _download(url, params, limit):
    response = requests.get(url, params=params, timeout=20, allow_redirects=False,
                            headers={'User-Agent': 'TradingAgents-KR/3.6'})
    data = response.content
    if response.status_code != 200 or len(data) > limit:
        raise ValueError('dart_http_or_size')
    return data


def corp_codes(key, stock_codes):
    """Return only requested six-digit stock codes, never the whole registry."""
    wanted = {str(c) for c in stock_codes if re.fullmatch(r'[0-9]{6}', str(c))}
    if not wanted:
        return {}
    data = _download(CORP_URL, {'crtfc_key': key.reveal()}, MAX_ZIP)
    if not data.startswith(b'PK'):
        raise ValueError('dart_corp_registry_unavailable')
    with zipfile.ZipFile(BytesIO(data)) as archive:
        members = archive.infolist()
        if len(members) != 1 or members[0].file_size > MAX_XML or members[0].filename.upper() != 'CORPCODE.XML':
            raise ValueError('dart_corp_registry_schema')
        xml = archive.read(members[0])
    if b'<!DOCTYPE' in xml.upper() or b'<!ENTITY' in xml.upper() or len(xml) > MAX_XML:
        raise ValueError('dart_corp_registry_unsafe')
    found = {}
    for _, element in ET.iterparse(BytesIO(xml), events=('end',)):
        if element.tag != 'list':
            continue
        code = element.findtext('stock_code') or ''
        corp = element.findtext('corp_code') or ''
        if code in wanted and re.fullmatch(r'[0-9]{8}', corp):
            if code in found and found[code] != corp:
                raise ValueError('dart_duplicate_stock_code')
            found[code] = corp
        element.clear()
    return found


def _number(raw):
    if isinstance(raw, (int, float)) and finite(raw):
        return float(raw)
    if not isinstance(raw, str) or not re.fullmatch(r'-?[0-9][0-9,]*(?:\.[0-9]+)?', raw):
        raise ValueError('dart_number')
    value = float(raw.replace(',', ''))
    if not finite(value) or abs(value) > 1e21:
        raise ValueError('dart_number_range')
    return value


def _account(rows, name, field='thstrm_amount'):
    section, account_id = ACCOUNT[name]
    sections = ('IS', 'CIS') if section == 'IS' else (section,)
    matches = [r for r in rows if r.get('sj_div') in sections and r.get('account_id') == account_id]
    if len(matches) != 1 or matches[0].get('currency') != 'KRW':
        raise ValueError('dart_account_missing_or_ambiguous')
    return _number(matches[0].get(field))


def _debt(rows):
    selected = [r for r in rows if r.get('sj_div') == 'BS' and r.get('account_id') in DEBT_IDS]
    ids_present = {r['account_id'] for r in selected}
    if 'ifrs-full_CurrentBorrowingsAndCurrentPortionOfNoncurrentBorrowings' in ids_present:
        selected = [r for r in selected if r['account_id'] not in
                    ('dart_CurrentLoansReceived', 'ifrs-full_CurrentBorrowings',
                     'ifrs-full_CurrentPortionOfLongtermBorrowings')]
    if 'ifrs-full_LongtermBorrowings' in ids_present:
        selected = [r for r in selected if r['account_id'] not in
                    ('ifrs-full_NoncurrentPortionOfNoncurrentLoansReceived',
                     'ifrs-full_NoncurrentBorrowings')]
    ids = [r['account_id'] for r in selected]
    if not ids:
        # (V3.34) 차입금 계정이 하나도 없으면(무차입 기업) 차입금 0. 아래 대조에서 Yahoo 값과 비교해
        # 둘 다 부채 기준을 통과할 때만 넘어가고, 판정에는 더 큰 쪽(Yahoo)을 쓴다.
        return 0.0
    if len(ids) != len(set(ids)) or any(r.get('currency') != 'KRW' for r in selected):
        raise ValueError('dart_debt_definition_unavailable')
    return sum(_number(r.get('thstrm_amount')) for r in selected)


def _report(key, corp, year, report_code):
    raw = _download(FIN_URL, {'crtfc_key': key.reveal(), 'corp_code': corp,
                              'bsns_year': str(year), 'reprt_code': report_code,
                              'fs_div': 'CFS'}, MAX_JSON)
    payload = json.loads(raw)
    if not isinstance(payload, dict) or payload.get('status') != '000' or not isinstance(payload.get('list'), list):
        raise ValueError('dart_report_unavailable')
    rows = payload['list']
    if not rows or len(rows) > 1000 or any(not isinstance(r, dict) or r.get('corp_code') != corp
                                           or str(r.get('bsns_year')) != str(year)
                                           or r.get('reprt_code') != report_code
                                           or r.get('fs_div') not in (None, 'CFS') for r in rows):
        raise ValueError('dart_report_schema')
    receipts = {r.get('rcept_no') for r in rows}
    if len(receipts) != 1 or not re.fullmatch(r'[0-9]{14}', str(next(iter(receipts)))):
        raise ValueError('dart_receipt_schema')
    return rows, next(iter(receipts))


# (V3.34) 희석 EPS 계정이 없는 회사(희석 효과가 없어 '기본및희석' 또는 '기본'만 공시)가 많아
# EPS 를 못 구해 장기 후보 전부가 '자료 대기'에 머물던 문제 → 희석 → 기본및희석 → 기본 순서로 찾는다.
EPS_IDS = ('ifrs-full_DilutedEarningsLossPerShare',
           'ifrs-full_BasicAndDilutedEarningsLossPerShare',
           'ifrs-full_BasicEarningsLossPerShare')


def _eps(rows, field):
    for account_id in EPS_IDS:
        matches = [r for r in rows if r.get('sj_div') in ('IS', 'CIS') and r.get('account_id') == account_id
                   and r.get('currency') == 'KRW']
        values = {_number(r.get(field)) for r in matches if str(r.get(field) or '').strip() not in ('', '-')}
        if len(values) == 1:  # 같은 계정이 여러 줄이어도 값이 같으면 하나로 본다(계속·중단영업 구분 등은 값이 달라 제외)
            return next(iter(values))
    return None


def _read_values(rows, interim):
    income_field = 'thstrm_add_amount' if interim else 'thstrm_amount'
    try:
        eps = _eps(rows, income_field)
    except ValueError:
        eps = None
    return {'revenue_cumulative': _account(rows, 'revenue', income_field),
            'income_cumulative': _account(rows, 'income', income_field),
            'eps_cumulative': eps,
            'revenue_quarter': _account(rows, 'revenue'),
            'cfo_cumulative': _account(rows, 'cfo'),
            'ppe_cumulative': _account(rows, 'ppe'),
            'intangible_cumulative': _account(rows, 'intangible'),
            'equity': _account(rows, 'equity'),
            'debt': _debt(rows)}


def _near(a, b, relative=.01, absolute=1_000_000):
    scale = max(abs(a), abs(b))
    return abs(a - b) <= max(relative * scale, min(absolute, .001 * scale))


def reconcile(fact, periods, balance, corp, key, now):
    """Compare TTM/quarterly core metrics and cautiously derive missing TTM EPS.

    The EPS arithmetic is explicitly marked derived. It is allowed only if
    reported parent income / diluted EPS implies a stable share denominator.
    """
    fact['dart_verification'] = {'status': 'unavailable', 'checked_at': now.isoformat(),
                                 'reason': '공시 자료 조회 또는 대응 계정 확인 실패', 'checks': [], 'receipts': []}
    fact['eligible'] = False
    if not re.fullmatch(r'[0-9]{8}', str(corp or '')) or not periods:
        fact['dart_verification']['reason'] = '종목·법인 고유번호 연결 실패'
        return fact
    try:
        latest = sorted(periods, key=lambda p: p['date'], reverse=True)[0]
        date = datetime.fromisoformat(latest['date']).date()
        if (date.month not in REPORT or date.day != calendar.monthrange(date.year, date.month)[1]
                or date > now.date()):
            raise ValueError('dart_period_unsupported')
        q4 = date.month == 12
        annual_year = date.year if q4 else date.year - 1
        annual_rows, annual_receipt = _report(key, corp, annual_year, REPORT[12])
        annual = _read_values(annual_rows, False)
        receipts = [annual_receipt]
        eps_reason = None
        if q4:
            ttm = annual
            eps = annual['eps_cumulative']
            eps_basis = 'reported_annual'
            if eps is None:
                eps_reason = '공시 희석 EPS 표준 계정 없음'
            third_rows, third_receipt = _report(key, corp, date.year, REPORT[9])
            old_annual_rows, old_annual_receipt = _report(key, corp, date.year - 1, REPORT[12])
            old_third_rows, old_third_receipt = _report(key, corp, date.year - 1, REPORT[9])
            receipts += [third_receipt, old_annual_receipt, old_third_receipt]
            latest_revenue = annual['revenue_cumulative'] - _account(third_rows, 'revenue', 'thstrm_add_amount')
            prior_revenue = (_account(old_annual_rows, 'revenue') -
                             _account(old_third_rows, 'revenue', 'thstrm_add_amount'))
        else:
            current_rows, current_receipt = _report(key, corp, date.year, REPORT[date.month])
            prior_rows, prior_receipt = _report(key, corp, date.year - 1, REPORT[date.month])
            current = _read_values(current_rows, True)
            prior = _read_values(prior_rows, True)
            receipts += [current_receipt, prior_receipt]
            ttm = {k: annual[k] + current[k] - prior[k]
                   for k in ('revenue_cumulative', 'income_cumulative',
                             'cfo_cumulative', 'ppe_cumulative', 'intangible_cumulative')}
            ttm['equity'] = current['equity']
            ttm['debt'] = current['debt']
            eps_values = [x['eps_cumulative'] for x in (annual, current, prior)]
            eps = (eps_values[0] + eps_values[1] - eps_values[2]
                   if all(value is not None for value in eps_values) else None)
            eps_basis = 'derived_annual_plus_current_ytd_minus_prior_ytd'
            prior_revenue = prior['revenue_quarter']
            latest_revenue = current['revenue_quarter']
            if eps is None:
                eps_reason = '공시 희석 EPS 표준 계정 없음'
            else:
                implied = [x['income_cumulative'] / x['eps_cumulative'] for x in (annual, current, prior)
                           if x['eps_cumulative'] > 0 and x['income_cumulative'] > 0]
                if len(implied) != 3 or max(implied) / min(implied) > 1.02:
                    eps = None
                    eps_reason = 'EPS 산출 주식수 변동으로 누적 계산 보류'
        fcf = ttm['cfo_cumulative'] - ttm['ppe_cumulative'] - ttm['intangible_cumulative']
        if ttm['equity'] <= 0 or ttm['debt'] < 0:
            raise ValueError('dart_nonpositive_core')
        if eps is not None and eps <= 0:
            eps = None
            eps_reason = '공시 누적 EPS가 양수가 아님'
        checks = [
            ('ttm_income', (fact.get('metrics') or {}).get('ttm_income'), ttm['income_cumulative'], .01, 1_000_000),
            # Yahoo CapEx and DART PPE/intangible purchases can differ in scope.
            # Preserve both figures; a small variance is tolerated, a larger one blocks.
            ('ttm_fcf', (fact.get('metrics') or {}).get('ttm_fcf'), fcf, .02, 1_000_000),
            ('equity', balance.get('equity'), ttm['equity'], .01, 1_000_000),
            ('debt', balance.get('debt'), ttm['debt'], .01, 1_000_000),
            ('latest_quarter_revenue', latest.get('revenue'), latest_revenue, .01, 1_000_000),
        ]
        if prior_revenue is not None:
            oldest = next((p for p in periods if p['date'] == f'{date.year-1}-{date.month:02d}-{date.day:02d}'), None)
            checks.append(('prior_year_quarter_revenue', oldest.get('revenue') if oldest else None,
                           prior_revenue, .01, 1_000_000))
        yahoo_eps = (fact.get('metrics') or {}).get('ttm_eps')
        if finite(yahoo_eps) and eps is not None:
            checks.append(('ttm_eps', yahoo_eps, eps, .03, 1))
        outcomes = [{'metric': name, 'yahoo': yahoo, 'dart': official,
                     'difference_pct': 100 * abs(yahoo - official) / max(abs(official), 1) if finite(yahoo) else None,
                     'match': finite(yahoo) and _near(yahoo, official, relative, absolute)}
                    for name, yahoo, official, relative, absolute in checks]
        fact['dart_verification'] = {'status': 'verified', 'checked_at': now.isoformat(),
                                     'period_end': latest['date'], 'basis': 'CFS_KRW',
                                     'eps_basis': eps_basis, 'checks': outcomes,
                                     'receipts': receipts, 'reason': None}
        if any(not row['match'] and row['metric'] != 'debt' for row in outcomes):
            fact['dart_verification'].update(status='mismatch', reason='Yahoo와 공시의 대응 수치가 다름')
            return fact
        debt_gap = any(row['metric'] == 'debt' and not row['match'] for row in outcomes)
        if debt_gap:
            # (V3.34) Yahoo '총부채(Total Debt)'는 리스부채 등을 포함하고 공시 '차입금'은 범위가 좁아 거의 항상 다르다.
            # 두 정의 모두 부채/자본 0~1 기준을 통과하면 판정이 바뀌지 않으므로 막지 않고,
            # 판정용 부채비율은 더 큰(보수적인) 값을 쓴다. 어느 한쪽이라도 기준을 넘으면 기존처럼 보류.
            de_yahoo = balance.get('debt') / balance.get('equity') if finite(balance.get('debt')) and finite(balance.get('equity')) and balance.get('equity') > 0 else None
            de_dart = ttm['debt'] / ttm['equity']
            if de_yahoo is not None and 0 <= de_yahoo <= 1 and 0 <= de_dart <= 1:
                debt_gap = False
                if isinstance(fact.get('metrics'), dict):
                    fact['metrics']['debt_equity'] = max(de_yahoo, de_dart)
                fact['dart_verification']['debt_scope_note'] = (
                    f'차입금 정의 차이(Yahoo 부채비율 {de_yahoo:.2f} / 공시 {de_dart:.2f}) · 둘 다 기준 이내 · 큰 값 적용')
        fact['source'] = 'Yahoo Finance via yfinance / OpenDART 연결재무제표 대조'
        if eps is None:
            fact['dart_verification'].update(
                status='partial_debt_eps' if debt_gap else 'partial_eps',
                reason=('차입금 계정 범위 불일치 · ' if debt_gap else '') + eps_reason)
            return fact
        if not finite(yahoo_eps):
            # A derived TTM EPS is identified as such; no standalone Q4 EPS is claimed.
            fact['metrics']['ttm_eps'] = eps
            fact['metrics']['pe'] = fact['metrics']['price'] / eps if finite(fact['metrics'].get('price')) else None
            fact['dart_verification']['status'] = ('partial_debt' if debt_gap else
                                                   'verified_derived_eps' if eps_basis.startswith('derived_') else 'verified')
            if debt_gap:
                fact['dart_verification']['reason'] = '차입금 계정 범위 불일치'
            if fact.get('status') == 'insufficient' and fact.get('missing') and all('EPS' in str(x) for x in fact['missing']):
                fact['status'] = 'ok'
                fact['missing'] = []
                fact['eligible'] = bool(not debt_gap and fact['metrics']['ttm_income'] > 0 and fact['metrics']['ttm_fcf'] > 0
                                        and eps > 0 and fact['metrics']['revenue_yoy_pct'] >= 0
                                        and 0 <= fact['metrics']['debt_equity'] <= 1
                                        and 0 < fact['metrics']['pe'] <= 20 and 0 < fact['metrics']['pb'] <= 3)
        else:
            if debt_gap:
                fact['dart_verification'].update(status='partial_debt', reason='차입금 계정 범위 불일치')
            fact['eligible'] = bool(not debt_gap and fact.get('status') == 'ok' and fact.get('metrics')
                                    and fact['metrics']['ttm_income'] > 0 and fact['metrics']['ttm_fcf'] > 0
                                    and yahoo_eps > 0 and fact['metrics']['revenue_yoy_pct'] >= 0
                                    and 0 <= fact['metrics']['debt_equity'] <= 1
                                    and 0 < fact['metrics']['pe'] <= 20 and 0 < fact['metrics']['pb'] <= 3)
    except (requests.RequestException, ValueError, TypeError, KeyError, IndexError, ZeroDivisionError, json.JSONDecodeError) as error:
        # Never serialize an exception: request URLs can contain the API key.
        allowed={'dart_period_unsupported','dart_eps_share_base_changed','dart_nonpositive_core',
                 'dart_report_unavailable','dart_report_schema','dart_receipt_schema',
                 'dart_account_missing_or_ambiguous','dart_debt_definition_unavailable',
                 'dart_number','dart_number_range','dart_http_or_size'}
        reason = str(error) if isinstance(error, ValueError) and str(error) in allowed else 'dart_request_or_schema_error'
        fact['dart_verification']['status'] = 'unavailable'
        fact['dart_verification']['reason'] = reason[:80]
        fact['eligible'] = False
    return fact


def verify_batch(facts, raw_inputs, key, now):
    if not key:
        return facts
    try:
        mapping = corp_codes(key, [f.get('code') for f in facts])
    except (requests.RequestException, ValueError, zipfile.BadZipFile, ET.ParseError, OSError):
        mapping = {}
    for fact in facts:
        data = raw_inputs.get(fact.get('code'))
        if data:
            reconcile(fact, data['periods'], data['balance'], mapping.get(fact['code']), key, now)
        else:
            fact['eligible'] = False
            fact['dart_verification'] = {'status': 'unavailable', 'checked_at': now.isoformat(),
                                         'reason': 'Yahoo 원자료 누락', 'checks': [], 'receipts': []}
    return facts
