"""Hourly rule review on the existing five-minute paper tick; no LLM or broker calls."""
from datetime import datetime, timedelta, time
import json
import math
from pathlib import Path

from execution.intraday_paper import ETF_CODES, audit, fresh, tick, valid_quote
from execution.split_portfolio import valid_short_signal
from tools.paper_portfolio import write


def build_signal(state, envelope, quotes, now):
    """Recheck a QA-approved morning ETF plan against *observed* intraday movement."""
    if not valid_short_signal(envelope, now) or now.time().replace(tzinfo=None) >= time(15, 20):
        return None, '당일 QA 승인 계획 없음 또는 청산 시간'
    plan = (envelope['outcome'].get('final_result') or {}).get('etf_execution') or {}
    if plan.get('selection_basis') == 'neutral_probe':
        from execution.etf_contract import _approved_plan
        if _approved_plan(envelope['outcome'], now) is not None:
            return None, '중립 탐색은 본계좌 5%에만 적용 · 별도 장중 실험 제외'
        return None, 'ETF 아침 계획 검증 미통과'
    try:
        made = datetime.fromisoformat(plan['generated_at'])
        code = plan['selected_code']
        weight = plan['weight']
        forecasts = plan['forecasts']
        probability = next(row['up_probability'] for row in forecasts if row['code'] == code)
        schema=plan.get('schema')
        if (made.tzinfo is None or made.astimezone(now.tzinfo).date() != now.date()
                or made.astimezone(now.tzinfo).time().replace(tzinfo=None) >= time(9, 15)
                or schema not in (2,3,4) or plan.get('status') != 'approved'
                or (schema in (3,4) and (plan.get('etf_qa') or {}).get('passed') is not True)
                or plan.get('target_date') != str(now.date()) or code not in ETF_CODES
                or not isinstance(weight, (int, float)) or isinstance(weight, bool)
                or not math.isfinite(weight) or not 0 < weight <= (.40 if schema == 4 else .25)
                or not isinstance(probability, (int, float)) or not math.isfinite(probability)
                or (schema == 2 and probability < 60)
                or (schema in (3,4) and (probability <= 50 or
                    weight > ((.20 if probability < 60 else .40) if schema == 4 else (.10 if probability < 60 else .25))))):
            return None, 'ETF 아침 계획 검증 미통과'
    except (KeyError, TypeError, ValueError, StopIteration):
        return None, 'ETF 아침 계획 누락'
    benchmark = quotes.get('KPI200') or {}
    if not valid_quote(benchmark, now):
        return None, 'KOSPI200 장중 시세 누락·지연'
    hour = now.replace(minute=0, second=0, microsecond=0)
    if hour.hour == 9:
        if now.time().replace(tzinfo=None) > time(9, 15):
            return None, '09:15 이후 첫 진입 보류'
        decision, reason = code, '아침 승인 계획 관찰'
    else:
        prior = [q for q in state['quotes'] if q['at'][:10] == str(now.date())
                 and hour-timedelta(minutes=65) <= datetime.fromisoformat(q['at']) <= hour-timedelta(minutes=20)
                 and isinstance(q.get('kpi200'), (int, float)) and q['kpi200'] > 0]
        if not prior:
            return None, '20~65분 전 기준 시세 없음: 신규 판단 보류'
        move = benchmark['value'] / prior[-1]['kpi200'] - 1
        agrees = move >= .0015 if code == '069500' else move <= -.0015
        decision, reason = (code, '최근 지수 흐름이 아침 방향과 일치') if agrees else (None, '최근 지수 흐름 불일치·불분명: 현금')
    signal = {'id': f'{now.date()}:{hour.hour:02d}:{plan["generated_at"]}',
              'mode': 'paper', 'source': 'morning_plan_rules', 'approved': True,
              'session_date': str(now.date()), 'created_at': now.isoformat(),
              'expires_at': min(hour+timedelta(hours=1), now.replace(hour=15, minute=20)).isoformat(),
              'code': decision, 'target_weight': float(weight) if decision else 0.0,
              'reason': reason, 'morning_probability': probability}
    if state.get('last_signal_id') == signal['id'] and state.get('last_signal'):
        previous = state['last_signal']
        return previous, previous['reason'] + ' (해당 시간대 판단 유지)'
    return signal, reason


def run_once(path, envelope, quotes, now):
    """Called under the existing portfolio lock; a new sidecar never alters its 20m ledger."""
    path = Path(path)
    if path.exists():
        state = json.loads(path.read_text(encoding='utf-8'))
    else:
        state = fresh(now)
    signal, reason = build_signal(state, envelope, quotes, now)
    invalid_plan = reason in ('당일 QA 승인 계획 없음 또는 청산 시간', 'ETF 아침 계획 검증 미통과', 'ETF 아침 계획 누락')
    updated = tick(state, signal, quotes, now, force_exit=invalid_plan)
    updated['last_review'] = {'at': now.isoformat(), 'source': 'rules_not_hourly_ai',
                              'reason': reason, 'signal_id': signal['id'] if signal else None}
    checked = audit(updated, quotes, now)
    updated['valuation_audit'] = checked
    if checked['status'] == 'mismatch':
        raise ValueError('Intraday paper ledger mismatch')
    if updated != state or not path.exists():
        write(path, updated)
    return updated
