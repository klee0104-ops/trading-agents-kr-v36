"""Read-only, conditional next-buy preview for the local paper dashboard.

Only existing approved plans are shown as candidates. This module never fetches
prices, places an order, or changes the paper ledger.
"""
from datetime import datetime, time
import math

from data.sessions import market_status
from execution.etf_contract import _approved_plan
from execution.short_intraday_ai import approved_plan as approved_intraday
from execution.split_portfolio import LONG, INVERSE, valid_short_signal
from tools.short_intraday_review import MAX_REVIEWS_PER_DAY

ETF_NAMES = {LONG: 'KODEX 200', INVERSE: 'KODEX 인버스'}
REENTRY_EXITS = {'intraday_exit', 'profit_target_exit', 'profit_trailing_exit'}


def _money(value):
    return round(value) if type(value) in (int, float) and math.isfinite(value) and value >= 0 else None


def _fresh_valuation(state, now):
    try:
        tick = datetime.fromisoformat(state['last_tick'])
        return (tick.tzinfo is not None and 0 <= (now-tick).total_seconds() <= 600
                and (state.get('quote_health') or {}).get('status') == 'ok')
    except (KeyError, TypeError, ValueError):
        return False


def _short_candidate(plan, book, now, fresh, kind):
    code = plan['selected_code']
    weight = plan['weight']
    equity = book.get('equity')
    cash = book.get('cash')
    budget = _money(min(equity * weight, cash)) if fresh and type(equity) in (int, float) and type(cash) in (int, float) else None
    return {'phase': kind, 'code': code, 'name': ETF_NAMES[code], 'weight_pct': round(weight * 100, 2),
            'max_budget_won': budget, 'plan_at': plan.get('generated_at'),
            'expires_at': plan.get('expires_at') if kind == 'conditional' else None,
            'qa_tier': '5% 탐색' if plan.get('status') == 'experimental' else '승인 계획',
            'reason': '승인 계획의 재진입 후보' if kind == 'reentry_wait' else '아직 체결되지 않은 조건부 후보',
            'conditions': (['다음 10~14시 시간별 KOSPI200 방향 검토 통과', '재진입 시각의 세 종목 유효 시세',
                            '현금·손실 한도 재확인'] if kind == 'reentry_wait' else
                           ['계획 유효시간 내 재확인', 'KOSPI200·ETF 세 종목 최신 시세', '현금·손실 한도 재확인'])}


def summarize(state, envelope, review, long_plan, now):
    """Produce allowlisted status; never call an LLM or predict a fill price."""
    today = str(now.date())
    market = market_status(now)['code']
    fresh = _fresh_valuation(state, now)
    sleeves = state.get('sleeves') or {}
    short = sleeves.get('short') or {}
    long = sleeves.get('long') or {}
    review = review if isinstance(review, dict) and review.get('date') == today else {}
    attempts = review.get('attempts', 0)
    attempts = attempts if type(attempts) is int and 0 <= attempts <= MAX_REVIEWS_PER_DAY else 0
    result = {'as_of': state.get('last_tick'), 'market': market, 'fresh_valuation': fresh,
              'short': {'phase': 'no_plan', 'reason': '승인된 추가 매수 계획 없음',
                        'review_attempts': attempts},
              'long': {'phase': 'no_plan', 'reason': '승인된 장기 추가 매수 배분 없음', 'candidates': []}}
    s = result['short']
    if short.get('paused') or short.get('stop_day') == today:
        s.update(phase='blocked', reason='단기 손실 한도·일중 정지로 신규 매수 보류')
    elif state.get('active_short_cycle') or short.get('holdings'):
        s.update(phase='holding', reason='ETF 보유 중 · 현재 정책에는 보유분 증액 계획 없음. 청산 후 새 판단을 기다림')
    elif market != 'open':
        s.update(phase='market_closed', reason='정규장 외 · 다음 거래일 분석 전 추가 매수 계획 없음')
    elif state.get('short_policy') != 'etf_v2':
        s.update(phase='no_plan', reason='ETF 단기 정책 미적용 · 다음 매수 후보 표시 불가')
    else:
        plan = review.get('plan') or {}
        approved = approved_intraday(plan, now) if isinstance(plan, dict) else None
        if approved and approved.get('selected_code') and state.get('last_intraday_plan_at') != approved['generated_at']:
            s.update(_short_candidate(approved, short, now, fresh, 'conditional'))
        elif approved and approved.get('selected_code') is None and state.get('last_intraday_plan_at') != approved['generated_at']:
            s.update(phase='no_plan', reason='장중 트레이더가 현금 유지를 선택 · 추가 매수 후보 없음')
        elif str(state.get('last_intraday_plan_at') or '')[:10] == today:
            s.update(phase='no_plan', reason='최근 장중 판단은 처리 완료 · 새 승인 판단 전 추가 매수 없음')
        else:
            morning = _approved_plan(valid_short_signal(envelope, now), now)
            code = morning.get('selected_code') if morning else None
            if code and state.get('short_v2_decision_day') != today and time(9) <= now.time().replace(tzinfo=None) <= time(9,15):
                s.update(_short_candidate(morning, short, now, fresh, 'conditional'))
                s['expires_at'] = today + 'T09:15:00+09:00'
            elif (code and now.time().replace(tzinfo=None) < time(15) and morning.get('selection_basis') != 'neutral_probe' and
                  any(c.get('target_date') == today and c.get('selected_code') == code and
                      c.get('exit_reason') in REENTRY_EXITS for c in state.get('short_cycles', []))):
                s.update(_short_candidate(morning, short, now, fresh, 'reentry_wait'))
            elif attempts >= MAX_REVIEWS_PER_DAY:
                s.update(phase='no_plan', reason=f'장중 트레이더 검토 {attempts}/{MAX_REVIEWS_PER_DAY}회 사용 · 현재 유효한 재진입 계획 없음')
            else:
                s.update(phase='awaiting_review', reason='새 트레이더 판단 대기 · 승인 전 매수 후보 없음',
                         next_review=('09:20 이후 첫 유효 시세' if attempts == 0 else
                                      '마지막 요청 70분 후 또는 시장 변동·새 위험 뉴스'))

    lp = result['long']
    if long.get('paused') or long.get('stop_day') == today:
        lp.update(phase='blocked', reason='장기 손실 한도·정지로 신규 매수 보류')
    elif not isinstance(long_plan, dict) or long_plan.get('status') != 'approved':
        lp.update(phase='no_plan', reason='유효한 장기팀 보고서·배분 없음')
    else:
        allocations = [a for a in long_plan.get('allocations', []) if isinstance(a, dict) and
                       type(a.get('weight')) in (int, float) and math.isfinite(a['weight']) and a['weight'] > 0 and
                       type(a.get('buy_below')) in (int, float) and math.isfinite(a['buy_below']) and a['buy_below'] > 0]
        month = today[:7]
        execution = long.get('long_execution') or {}
        filled = set(execution.get('filled_codes') or []) if execution.get('month') == month else set()
        if long.get('decision_key') == month and execution.get('month') != month:
            filled = {a['code'] for a in allocations}
        blocked = set(long_plan.get('blocked_codes') or [])
        prices = state.get('last_prices') or {}
        instruments = long_plan.get('instruments') or {}
        candidates = []
        for a in allocations[:4]:
            code = a.get('code')
            if not isinstance(code, str) or code in (LONG, INVERSE):
                continue
            if code in filled:
                phase = 'filled_this_month'
            elif code in blocked:
                phase = 'blocked'
            elif fresh and type(prices.get(code)) in (int, float) and math.isfinite(prices[code]):
                phase = 'price_condition_met' if prices[code] <= a['buy_below'] else 'price_wait'
            else:
                phase = 'quote_wait'
            current = (long.get('holdings') or {}).get(code) or {}
            equity = long.get('equity')
            target = equity * a['weight'] if type(equity) in (int, float) and math.isfinite(equity) else None
            held = current.get('qty', 0) * prices[code] if fresh and type(current.get('qty', 0)) in (int, float) and code in prices else None
            additional = _money(max(0, target - held)) if target is not None and held is not None else None
            candidates.append({'code': code, 'name': str((instruments.get(code) or {}).get('name') or code)[:80],
                               'phase': phase, 'buy_below': round(a['buy_below'], 2),
                               'target_weight_pct': round(a['weight'] * 100, 2),
                               'estimated_additional_won': additional})
        lp.update(phase='conditional' if any(c['phase'] in ('price_condition_met','price_wait','quote_wait') for c in candidates) else 'no_plan',
                  reason='월 1회 · 기준가·재무·뉴스·시세 조건 재확인 후 결정' if candidates else '승인 배분 0건',
                  candidates=candidates, valid_until=long_plan.get('valid_until'))
    return result
