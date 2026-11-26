"""Paper-only KOSPI200 leverage sleeve; never sends a brokerage order."""
from datetime import datetime, time, timedelta

from execution.split_portfolio import FEE, SLIPPAGE, INVERSE, LEVERAGE, LONG, number, rebalance

WEIGHT = .30
INDEX_MOMENTUM_PCT = .15
# (V3.38) 청산 기준을 변동성에 맞춤. 예전 −1.5%(ETF 기준 = 지수 약 −0.75%)는 20일 변동성 45%(지수 하루 표준편차 약 2.8%,
# 15분 약 0.6%)에서 잡음만으로 걸려 10/26 진입 15분 만에 손절됐고, 목표(+1.2%)가 손절보다 작아 손익비도 불리했다.
# ETF 순수익률 기준: 손절 −2.5%(지수 약 −1.25%) · 목표 +3.0% · +1.5% 관측 후 1.0%p 되돌리면 청산.
STOP_NET_PCT = -2.50
TARGET_NET_PCT = 3.00
TRAIL_ARM_PCT = 1.50
TRAIL_GIVEBACK_PP = 1.00
# (V3.38) 추격 매수 방지: 최근 3~12분 최저점 대비 지수 +0.4% 이상 또는 레버리지 ETF +0.8% 이상 급등이면 이번 확인은 쉰다.
SESSION_OBS_FROM = time(9,5)
SPIKE_INDEX_PCT = .40
SPIKE_LEVERAGED_PCT = .80
MAX_TOTAL_DIRECTIONAL_PCT = .50
# (V3.34) 60% → 57%: AI 확률이 대부분 50~58% 구간이라 60%는 사실상 도달 불가(진입 0건). 장중 추세 +0.15%·
# 시세 신선도·인버스 비우세·노출 50% 상한·손실 정지(-6%, 일 -0.6%) 등 나머지 안전장치는 그대로.
MIN_PROBABILITY = 57
LEVERAGE_MULTIPLIER = 2  # (V3.32) 레버리지 ETF 는 보유분·신규 모두 평가금액의 2배로 노출 계산


def directional_exposure(state, prices):
    """전체 계좌 방향성 노출 합계(원). 레버리지 ETF 보유분은 2배, 나머지 보유는 1배(인버스도 상쇄하지 않고 더함 — 보수적).
    시세가 없는 보유분은 평가금액(equity-cash) 차이로 1배 계산."""
    total=0.0
    for b in (state.get('sleeves') or {}).values():
        known=0.0
        for t,h in (b.get('holdings') or {}).items():
            p=prices.get(t)
            if number(p) and number(h.get('qty')):
                v=h['qty']*p; known+=v
                total+=v*(LEVERAGE_MULTIPLIER if t==LEVERAGE else 1)
        total+=max(0.0,(b.get('equity',0)-b.get('cash',0))-known)
    return total


def relative_probability_diagnostic(plan, historical_probabilities):
    """Shadow-only threshold study; never grants a leverage order or bypasses QA."""
    from rules.activation_policy import strong_threshold
    threshold=strong_threshold(historical_probabilities)
    rows=(plan or {}).get('forecasts') or []
    probabilities={r.get('code'):r.get('up_probability') for r in rows if isinstance(r,dict)}
    valid=all(number(probabilities.get(code)) for code in ('KPI200',LONG,INVERSE))
    passed=(valid and (plan or {}).get('status')=='approved'
            and ((plan or {}).get('etf_qa') or {}).get('passed') is True
            and (plan or {}).get('selected_code')==LONG
            and probabilities['KPI200']>=threshold and probabilities[LONG]>=threshold
            and probabilities[INVERSE]<=50)
    return {'threshold_pct':threshold,'probability_gate_passed':bool(passed),
            'execution_approved':False,
            'note':'비교용 확률 관문만 계산; 정식 승인·장중 추세 +0.15%·시세·위험 한도 별도 필수'}


def _session_row(row, now):
    """(V3.39) 같은 날 09:05 이후 관측만 비교 기준으로 쓴다(예전 기록에 남은 장 시작 전 관측도 제외)."""
    try:
        at=datetime.fromisoformat(row['at'])
        return at.date()==now.date() and at.time().replace(tzinfo=None)>=SESSION_OBS_FROM
    except (KeyError,TypeError,ValueError):
        return False


def _fresh(quote, now):
    try:
        observed = datetime.fromisoformat(quote['observed_at'])
        traded = datetime.fromisoformat(quote['traded_at'])
        if traded.tzinfo is None: return False
        return (quote.get('market_status') == 'OPEN' and quote.get('date') == str(now.date())
                and number(quote.get('value')) and quote['value'] > 0
                and 0 <= (now-observed).total_seconds() <= 60
                and 0 <= (now-traded).total_seconds() <= 300)
    except (KeyError, TypeError, ValueError):
        return False


def _approved_direction(morning, intraday, now):
    from execution.etf_contract import _approved_plan
    from execution.short_intraday_ai import approved_plan

    latest = approved_plan(intraday, now) if isinstance(intraday, dict) else None
    if latest:
        if latest.get('status') != 'approved':
            return None, '장중 반대검토 미승인 · 레버리지 진입 불가'
        plan, source = latest, '장중 트레이더·반대검토 승인'
    elif now.time().replace(tzinfo=None) < time(10):
        plan, source = _approved_plan(morning, now), '아침 ETF 전용 QA 승인'
    else:
        return None, '10시 이후 장중 재판단 승인 자료 없음'
    if not plan or plan.get('selected_code') != LONG:
        return None, '승인된 KOSPI200 상승 방향 없음'
    rows = plan.get('forecasts') or []
    probs = {row.get('code'): row.get('up_probability') for row in rows if isinstance(row, dict)}
    if (not all(number(probs.get(code)) for code in ('KPI200', LONG, INVERSE))
            or probs['KPI200'] < MIN_PROBABILITY or probs[LONG] < MIN_PROBABILITY or probs[INVERSE] > 50):
        return None, f'레버리지 추가 검증: 지수·정방향 확률 {MIN_PROBABILITY}% 및 인버스 비우세 미충족'
    return plan, source


def _review(state, book, morning, intraday, prices, quotes, now):
    needed=('KPI200', LONG, INVERSE, LEVERAGE)
    if not all(_fresh(quotes.get(code) or {}, now) for code in needed) or LEVERAGE not in prices:
        return {'passed':False,'reason':'KOSPI200·정방향·인버스·레버리지 동일 거래일 유효 시세 부족'}
    obs=state.get('leverage_observations') or []
    plan, source=_approved_direction(morning,intraday,now)
    if not plan:return {'passed':False,'reason':source}
    if state['sleeves']['short'].get('holdings',{}).get(INVERSE):
        return {'passed':False,'reason':'단기 인버스 보유 중 상승 레버리지 동시 매수 금지'}
    short=state['sleeves']['short']
    if short.get('paused') or short.get('stop_day')==str(now.date()):
        return {'passed':False,'reason':'기존 단기팀 손실 중단 상태 · 레버리지 신규 진입 금지'}
    previous=[row for row in obs[:-1] if
              all(number(row.get(key)) and row[key]>0 for key in ('index','normal','leveraged'))
              and _session_row(row,now)
              and now-timedelta(minutes=65)<=datetime.fromisoformat(row['at'])<=now-timedelta(minutes=20)]
    if not previous:return {'passed':False,'reason':'20~65분 전 KOSPI200·ETF 비교 관측값 부족'}
    prior=previous[-1]
    moves={key:100*(obs[-1][key]/prior[key]-1) for key in ('index','normal','leveraged')}
    if (moves['index'] < INDEX_MOMENTUM_PCT or moves['normal'] <= 0
            or moves['leveraged'] <= 0):
        return {'passed':False,'reason':'최근 지수 +0.15%·일반/레버리지 ETF 상승 방향 일치 미충족',
                'moves_pct':{k:round(v,4) for k,v in moves.items()}}
    last=[row for row in obs[:-1] if
          all(number(row.get(key)) and row[key]>0 for key in ('index','leveraged'))
          and _session_row(row,now)
          and now-timedelta(minutes=12)<=datetime.fromisoformat(row['at'])<=now-timedelta(minutes=3)]
    if last:
        # 최근 3~12분 관측의 최저점 대비 상승폭 — 급등 직후 한 번 쉰 관측에서도 걸리도록(10/26 10:26 급등 → 10:31 진입 사례)
        jump={key:100*(obs[-1][key]/min(r[key] for r in last)-1) for key in ('index','leveraged')}
        if jump['index']>=SPIKE_INDEX_PCT or jump['leveraged']>=SPIKE_LEVERAGED_PCT:
            return {'passed':False,'reason':'직전 5분 급등 · 추격 매수 보류(다음 확인 때 재검토)',
                    'moves_pct':{k:round(v,4) for k,v in moves.items()},
                    'jump_pct':{k:round(v,4) for k,v in jump.items()}}
    gross=directional_exposure(state,prices)
    # 이 계좌가 이미 레버리지를 들고 있으면 목표 비중으로 '교체'되므로 기존분(2배)을 빼고 새 목표(2배)를 더한다
    held=(book.get('holdings') or {}).get(LEVERAGE) or {}
    existing=held.get('qty',0)*prices[LEVERAGE]*LEVERAGE_MULTIPLIER if number(prices.get(LEVERAGE)) else 0
    leverage_notional=book['equity']*WEIGHT*LEVERAGE_MULTIPLIER
    if gross-existing+leverage_notional > state['initial_cash']*MAX_TOTAL_DIRECTIONAL_PCT:
        return {'passed':False,'reason':'전체 계좌 상승 노출 50% 상한 초과'}
    return {'passed':True,'reason':source+' · KOSPI200/ETF 방향 확인 · 추가 입금액 30% 상한',
            'plan_at':plan['generated_at'],'moves_pct':{k:round(v,4) for k,v in moves.items()},
            'target_weight':WEIGHT}


def close_cycle(state,book,now,reason):
    cycle=state.get('active_leverage_cycle')
    if not cycle:return
    cycle.update(status='closed',exit_at=now.isoformat(),exit_reason=reason,
                 net_pnl=round(book['equity']-cycle['entry_equity'],2),
                 net_return_pct=round(100*(book['equity']/cycle['entry_equity']-1),4),
                 costs=round(book['fees']-cycle['entry_fees'],2))
    state.setdefault('leverage_cycles',[]).append(cycle)
    state['active_leverage_cycle']=None


def apply(state,book,morning,intraday,prices,quotes,now,names):
    clock=now.time().replace(tzinfo=None);today=str(now.date())
    if (LEVERAGE in prices and LONG in prices and
            all(_fresh(quotes.get(code) or {},now) for code in ('KPI200',LONG,INVERSE,LEVERAGE))):
        obs=state.setdefault('leverage_observations',[])
        # (V3.39) 09:05 이전 관측은 전일 종가가 섞여 있어(10/27 09:01=전일 종가) 갭을 '장중 상승'으로 오인 → 기록하지 않음
        if clock>=SESSION_OBS_FROM and (not obs or obs[-1]['at']!=now.isoformat()):
            obs.append({'at':now.isoformat(),'index':quotes['KPI200']['value'],
                        'normal':prices[LONG],'leveraged':prices[LEVERAGE]})
        state['leverage_observations']=obs[-100:]
    active=state.get('active_leverage_cycle')
    if active or book['holdings']:
        if set(book['holdings'])!={LEVERAGE}:
            book['status']='레버리지 계정 예상 외 보유 자산 · 자동 처리 보류';return
        if LEVERAGE not in prices:
            book['status']='레버리지 시세 미검증 · 다음 유효 시세에서 청산 확인';return
        reason=None
        if not active or active.get('entry_at','')[:10]!=today or clock>=time(15,20):
            reason='scheduled_exit'
        else:
            h=book['holdings'][LEVERAGE]
            net=100*(h['qty']*prices[LEVERAGE]*(1-SLIPPAGE)*(1-FEE)/h['cost']-1)
            previous_peak=active.get('peak_net_pct')
            peak=max(previous_peak,net) if number(previous_peak) else net
            active['peak_net_pct']=peak
            if net<=STOP_NET_PCT:reason='stop_loss'
            elif net>=TARGET_NET_PCT:reason='profit_target'
            elif peak>=TRAIL_ARM_PCT and peak-net>=TRAIL_GIVEBACK_PP:reason='profit_trail'
        if reason:
            rebalance(book,None,0,prices,now,state['trades'],'short_leverage',
                      '레버리지 모의 청산 · '+reason,names=names)
            close_cycle(state,book,now,reason)
            book['status']='레버리지 모의 청산 · '+reason+' · 실현손익 기록';return
        book['status']='레버리지 2배 ETF 보유 · 유효 시세로 손익·청산 조건 점검';return
    if state.get('leverage_last_entry_day')==today:
        book['status']='레버리지 당일 1회 운용 완료 · 현금 유지';return
    if not time(9,25)<=clock<time(14,50):
        book['status']='레버리지 신규 진입 시간 외 · 현금 유지';return
    review=_review(state,book,morning,intraday,prices,quotes,now)
    state['leverage_last_review']={'at':now.isoformat(),**review}
    reviews=state.setdefault('leverage_reviews',[])
    reviews.append({'at':now.isoformat(),'passed':review['passed'],'reason':review['reason']})
    state['leverage_reviews']=reviews[-500:]
    if not review['passed']:
        book['status']='레버리지 QA 보류 · '+review['reason'];return
    before=len(state['trades']);entry_equity=book['equity'];entry_fees=book['fees']
    rebalance(book,LEVERAGE,WEIGHT,prices,now,state['trades'],'short_leverage',
              '별도 1천만원 가상 입금 · 레버리지 추가 QA 승인 · 30% 한도',names=names)
    if not any(t['sleeve']=='short_leverage' and t['side']=='buy' for t in state['trades'][before:]):
        book['status']='레버리지 정수 수량 매수 불가 · 현금 유지';return
    state['active_leverage_cycle']={'entry_at':now.isoformat(),'entry_equity':entry_equity,
        'entry_fees':entry_fees,'selected_code':LEVERAGE,'plan_at':review['plan_at'],
        'qa':review,'status':'open','peak_net_pct':None}
    state['leverage_last_entry_day']=today
    book['status']='레버리지 ETF 30% 모의 매수 · 15:20 이전 청산 조건 감시'
