"""Deterministic, paper-only medium position lifecycle."""
from datetime import datetime, timedelta

from execution.split_portfolio import FEE, SLIPPAGE, number, rebalance
from rules.medium_sizing import POLICY, POSITION_STOP, TARGET_WEIGHT, execution_weight


def approved_target(plan,code,now,names):
    if not isinstance(plan,dict) or plan.get('status')!='approved' or plan.get('selected_code')!=code:
        return None
    weight=execution_weight(plan.get('weight'))
    limit=plan.get('buy_below');days=plan.get('horizon_days')
    try:
        valid=(code in names and weight==TARGET_WEIGHT and plan.get('execution_weight')==weight
               and number(limit) and limit>0 and type(days) is int and 20<=days<=120
               and isinstance(plan.get('generated_at'),str)
               and datetime.fromisoformat(plan['generated_at'])<=now<=datetime.fromisoformat(plan['valid_until']))
    except (KeyError,TypeError,ValueError):valid=False
    return weight if valid else None


def apply(book,plan,prices,now,journal,names):
    holdings=book.get('holdings') or {}
    if len(holdings)>1:
        book['status']='중기 보유 종목 수 불일치 · 수동 점검 필요';return
    if holdings:
        code,h=next(iter(holdings.items()))
        px=prices.get(code)
        if not number(px) or px<=0:
            book['status']='중기 보유 시세 누락 · 청산 대기';return
        position=book.get('medium_position') or {}
        avg=h['cost']/h['qty'] if h.get('qty') else 0
        net_exit=px*(1-SLIPPAGE)*(1-FEE-.002)
        ret=net_exit/avg-1 if avg>0 else None
        due=False
        try:
            entered=datetime.fromisoformat(position['entry_at'])
            due=now>=entered+timedelta(days=position['horizon_days'])
        except (KeyError,TypeError,ValueError,OverflowError):
            pass
        reason=('중기 개별 손실 12% 제한' if ret is not None and ret<=-POSITION_STOP else
                '중기 비용 차감 수익 18% 목표' if ret is not None and ret>=.18 else
                '중기 계획 보유기간 만료' if due else None)
        if reason:
            rebalance(book,None,0,prices,now,journal,'medium',reason,names=names)
            book['medium_position']=None
            book['status']=reason+' · 실제 가상 체결 손익 기록'
        else:
            book['status']='중기 보유 평가 · 계획 기간·손익 조건 감시'
            target=approved_target(plan,code,now,names)
            if (target is not None and position.get('plan_at')==plan.get('generated_at')
                    and position.get('sizing_policy')!=POLICY):
                if px>plan['buy_below']:
                    book['status']='중기 보유 평가 · 증액 기준가 도달 대기'
                else:
                    desired=int(book['equity']*target/(px*(1+SLIPPAGE)*(1+FEE)))
                    if desired>h['qty']:
                        before=len(journal)
                        rebalance(book,code,target,prices,now,journal,'medium',
                                  '중기 50% 집행 정책 · 기존 보유분 1회 증액',names=names)
                        if any(t['side']=='buy' and t['sleeve']=='medium' for t in journal[before:]):
                            position['sizing_policy']=POLICY
                            position['execution_weight']=target
                            position['analyst_weight']=plan['weight']
                            book['status']='중기 집행 목표 50% · 기존 보유분 1회 증액 완료'
                    else:
                        position['sizing_policy']=POLICY
                        position['execution_weight']=target
        return
    if not isinstance(plan,dict) or plan.get('status')!='approved' or not plan.get('selected_code'):
        book['status']='중기팀 승인 매수 계획 없음 · 현금 유지';return
    code=plan['selected_code'];weight=approved_target(plan,code,now,names);limit=plan.get('buy_below')
    days=plan.get('horizon_days');generated=plan.get('generated_at')
    try:
        plan_at=datetime.fromisoformat(generated)
    except (TypeError,ValueError):
        book['status']='중기 승인 계획 시각 오류 · 현금 유지';return
    if plan_at.tzinfo is None or now.tzinfo is None:
        book['status']='중기 승인 계획 시간대 확인 불가 · 현금 유지';return
    if plan_at.astimezone(now.tzinfo).date()!=now.date():
        book['status']='중기 당일 새 분석 계획 없음 · 현금 유지';return
    for trade in reversed(journal):
        if not isinstance(trade,dict):continue
        if (trade.get('sleeve')=='medium' and trade.get('ticker')==code and
                trade.get('side')=='sell' and trade.get('reason')=='중기 개별 손실 12% 제한'):
            try:
                stopped_at=datetime.fromisoformat(trade['at'])
                stale=plan_at<=stopped_at
            except (KeyError,TypeError,ValueError):
                book['status']='중기 이전 손절 시각 확인 불가 · 신규 매수 보류';return
            if stale:
                book['status']='중기 손절 이후 새 분석 계획 대기 · 현금 유지';return
            break
    try:
        valid=(weight is not None and book.get('last_medium_plan_at')!=generated)
    except (KeyError,TypeError,ValueError):valid=False
    if not valid:
        book['status']='중기 계획 만료·중복·형식 오류 · 현금 유지';return
    px=prices.get(code)
    if not number(px) or px<=0 or px>limit:
        book['status']='중기 매수 기준가·시세 확인 대기';return
    before=len(journal)
    rebalance(book,code,weight,prices,now,journal,'medium',
              '중기 단일 분석가 판단 + 결정적 자료·가격 검증',names=names)
    buys=[t for t in journal[before:] if t['sleeve']=='medium' and t['side']=='buy']
    if buys:
        book['last_medium_plan_at']=generated
        book['medium_position']={'code':code,'entry_at':now.isoformat(),
                                 'horizon_days':days,'plan_at':generated,
                                 'sizing_policy':POLICY,'execution_weight':weight,
                                 'analyst_weight':plan['weight']}
        book['status']='중기 가상 진입 · 20~120일 보유 계획'
    else:
        book['status']='중기 현금 또는 주문 가능 수량 부족 · 추가 매수 대기'
