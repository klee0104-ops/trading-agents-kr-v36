"""Two isolated cash/ETF simulation books. No broker or LLM dependencies."""
from copy import deepcopy
from datetime import datetime
import math
import re

LONG, INVERSE = '069500', '114800'
NAMES = {LONG: 'KODEX 200', INVERSE: 'KODEX 인버스', '005930':'삼성전자', '000660':'SK하이닉스'}
FEE, SLIPPAGE = 0.00015, 0.0005


def instrument_names(state,plan=None):
    names=dict(NAMES)
    for source in (state.get('instruments',{}),(plan or {}).get('instruments',{})):
        for code,spec in source.items():
            if re.fullmatch(r'[0-9A-Z]{6}',str(code)) and isinstance(spec,dict) and isinstance(spec.get('name'),str) and 0<len(spec['name'])<=80:
                names[code]=spec['name']
    return names


def number(x):
    return type(x) in (int, float) and math.isfinite(x)


def fresh(now):
    def sleeve(label, cash):
        return dict(label=label, initial_cash=cash, cash=float(cash), holdings={},
                    equity=float(cash), realized_pnl=0., fees=0., peak=float(cash),
                    session_start=float(cash), session='', paused=False, stop_day='',
                    decision_key='', status='신규 계좌 · 판단 대기')
    return dict(schema=1, enabled=True, created_at=now.isoformat(), initial_cash=20_000_000,
                sleeves={'long': sleeve('장기',8_000_000), 'short': sleeve('단기·현금',12_000_000)},
                trades=[], equity_history=[], last_tick=None, quote_at=None)


def valid_signal(envelope, now):
    if not isinstance(envelope,dict) or envelope.get('mode') != 'real':
        return None
    outcome = envelope.get('outcome') or {}
    try:
        saved = datetime.fromisoformat(envelope['saved_at'])
        if saved.tzinfo is None: saved=saved.replace(tzinfo=now.tzinfo)
        if not 0 <= (now-saved).total_seconds() <= 48*3600:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    if outcome.get('target_date') != str(now.date()): return None
    if (outcome.get('final_qa') or {}).get('consistent') is not True: return None
    if (outcome.get('verification') or {}).get('verification_passed') is False: return None
    return outcome


def mark(book, prices):
    book['equity'] = book['cash'] + sum(h['qty']*prices[t] for t,h in book['holdings'].items())
    book['peak'] = max(book['peak'],book['equity'])


def rebalance(book, target_code, weight, prices, now, journal, sleeve, reason, targets=None, names=None):
    """Sell before buying; average cost includes entry fees, exits realize net P/L."""
    names=names or NAMES
    equity=book['equity']
    desired={t:0 for t in prices}
    if target_code:
        desired[target_code]=int(equity*weight/(prices[target_code]*(1+SLIPPAGE)*(1+FEE)))
    if targets is not None:
        for code,weight in targets.items():
            desired[code]=int(equity*weight/(prices[code]*(1+SLIPPAGE)*(1+FEE)))
    for side in ('sell','buy'):
        for code in prices:
            h=book['holdings'].get(code,{'qty':0,'cost':0.})
            delta=desired[code]-h['qty']
            if side=='sell' and delta>=0 or side=='buy' and delta<=0: continue
            qty=abs(delta); fill=prices[code]*(1+SLIPPAGE if side=='buy' else 1-SLIPPAGE)
            if side=='buy': qty=min(qty,int(book['cash']/(fill*(1+FEE))))
            if qty<=0: continue
            fee=qty*fill*(FEE+(.002 if side=='sell' and code not in (LONG,INVERSE) else 0)); gross=qty*fill; realized=0.
            if side=='buy':
                cost=h['cost']+gross+fee
                book['cash']-=gross+fee
                book['holdings'][code]={'qty':h['qty']+qty,'cost':cost}
            else:
                cost=h['cost']*qty/h['qty']
                realized=gross-fee-cost
                book['realized_pnl']+=realized; book['cash']+=gross-fee
                if h['qty']==qty: book['holdings'].pop(code,None)
                else: book['holdings'][code]={'qty':h['qty']-qty,'cost':h['cost']-cost}
            book['fees']+=fee
            journal.append(dict(at=now.isoformat(timespec='seconds'),sleeve=sleeve,ticker=code,
                name=names[code],side=side,qty=qty,price=round(fill,4),fee=round(fee,4),
                realized_pnl=round(realized,4),reason=reason))
    mark(book,prices)


def tick(state, envelope, quotes, now, long_report=None):
    """Pure transition, quotes observed immediately before calling. No backdated fills."""
    from data.sessions import market_status
    d=deepcopy(state)
    if d.get('schema')!=1 or not d.get('enabled'): return d
    if d.get('last_tick') and datetime.fromisoformat(d['last_tick']) >= now: return d
    names=instrument_names(d,long_report)
    prices={}
    required={LONG,INVERSE}|{t for b in d['sleeves'].values() for t in b['holdings']}
    if long_report is not None:
        required.update(a.get('code') for a in long_report.get('allocations',[]) if isinstance(a,dict))
    if not required <= set(names): return d
    for code in required:
        q=quotes.get(code) or {}
        try:
            observed=datetime.fromisoformat(q['observed_at'])
            if not 0 <= (now-observed).total_seconds() <= 60: return d
            if q['date'] != str(now.date()): return d
            traded=datetime.fromisoformat(q['traded_at'])
            if traded.tzinfo is None: traded=traded.replace(tzinfo=now.tzinfo)
            if not 0 <= (now-traded).total_seconds() <= 900: return d
        except (KeyError,TypeError,ValueError): return d
        if not number(q.get('value')) or q['value']<=0: return d
        prices[code]=q['value']
    d['instruments']={c:{'name':n} for c,n in names.items()}
    today=str(now.date()); month=today[:7]
    signal=valid_signal(envelope,now)
    open_market=market_status(now)['code']=='open' and all(quotes[c].get('market_status')=='OPEN' for c in required)
    for key,book in d['sleeves'].items():
        if any(t not in prices for t in book['holdings']): raise ValueError('Unknown holding')
        if book['session']!=today:
            book['session_start']=book['equity']; book['session']=today
        mark(book,prices)
        dd=book['equity']/book['peak']-1 if book['peak'] else 0
        day_loss=book['equity']/book['session_start']-1 if book['session_start'] else 0
        if dd<=(-.15 if key=='long' else -.10): book['paused']=True
        if key=='short' and day_loss<=-.02: book['stop_day']=today
        if not open_market:
            book['status']='장외 · 평가만 갱신'; continue
        if book['paused'] or book['stop_day']==today:
            reason='손실 한도 도달 · 현금화 및 신규 매수 중지'
            rebalance(book,None,0,prices,now,d['trades'],key,reason,names=names)
            if key=='short' and d.get('short_policy')=='etf_v2':
                from execution.etf_contract import close_cycle
                close_cycle(d,book,quotes,now,'risk_exit')
            book['status']=reason; continue
        if key=='short':
            if d.get('short_policy')=='etf_v2':
                from execution.etf_contract import apply
                apply(d,book,signal,prices,quotes,now,names)
                continue
            if not signal:
                reason='당일 유효 분석 없음/QA 미승인 · 현금 유지'
                rebalance(book,None,0,prices,now,d['trades'],key,reason,names=names)
                book['status']=reason; continue
            if book['decision_key']==today:
                book['status']='당일 목표 반영 완료 · 보유 평가'; continue
            pred=(signal.get('final_result') or {}).get('kospi_prediction') or {}
            pos=pred.get('position')
            if not number(pos) or not -1<=pos<=1: pos=0.
            cap=.25 if (signal.get('risk_assessment') or {}).get('risk_level')=='높음' else .5
            weight=min(abs(pos),cap)
            code=LONG if pos>0 else INVERSE if pos<0 else None
            reason=f'당일 확률 포지션 {pos:+.2f} · 단기 투자 상한 {cap:.0%}'
            rebalance(book,code,weight,prices,now,d['trades'],key,reason,names=names)
            book['decision_key']=today; book['status']=reason
        else:
            if long_report is not None:
                from datetime import timedelta
                try:
                    valid=(long_report.get('status')=='approved' and datetime.fromisoformat(long_report['generated_at'])<=now<=datetime.fromisoformat(long_report['valid_until'])
                           and now-datetime.fromisoformat(long_report['generated_at'])<=timedelta(days=8))
                    valid=valid and number(long_report.get('horizon_days')) and long_report['horizon_days']>=180
                    allocations=long_report.get('allocations',[])
                    valid=valid and isinstance(allocations,list) and len(allocations)<=4
                    targets={};seen=set()
                    for a in allocations:
                        c=a['code'];w=a['weight'];limit=a['buy_below']
                        if c not in names or c in (LONG,INVERSE) or c in seen or not number(w) or not 0<=w<=.25 or not number(limit) or limit<=0: valid=False;break
                        seen.add(c);targets[c]=w
                    if sum(targets.values())>.5:valid=False
                    if 'reviewed_codes' in long_report and not set(book['holdings'])<=set(long_report['reviewed_codes']):valid=False
                    if long_report.get('instruments'):
                        sectors={}
                        for c,w in targets.items():
                            sec=long_report['instruments'].get(c,{}).get('sector','Unknown')
                            sectors[sec]=sectors.get(sec,0)+w
                        if any(w>.3 for w in sectors.values()):valid=False
                except (KeyError,TypeError,ValueError):valid=False
                if not valid:
                    book['status']='장기 보고서 미승인/만료 · 추가 매수 보류';continue
                if long_report.get('blocked_codes'):
                    book['status']='미국 뉴스 재검토/수집 상태 확인 필요 · 추가 매수 보류';continue
                if book['decision_key']==month:
                    book['status']='장기팀 월간 목표 유지 · 뉴스 위험 감시';continue
                if any(a['weight']>0 and prices[a['code']]>a['buy_below'] for a in allocations):
                    book['status']='장기 매수 기준가 도달 대기 · 추격/자동 물타기 금지';continue
                rebalance(book,None,0,prices,now,d['trades'],key,'독립 장기팀 승인 · 월간 분할 계좌 조정',targets=targets,names=names)
                book['decision_key']=month;book['status']='독립 장기팀 계획 반영 완료';continue
            if book['decision_key']==month:
                book['status']='월간 장기 목표 유지 · 보유 평가'; continue
            plan=((signal or {}).get('final_result') or {}).get('long_term_plan') or {}
            weight=plan.get('equity_weight'); horizon=plan.get('horizon_days')
            if not (number(weight) and 0<=weight<=1 and number(horizon) and horizon>=60
                    and isinstance(plan.get('reason'),str) and plan['reason'].strip()):
                book['status']='장기 계획 미기록 · 기존 현금/보유 유지'; continue
            reason='월간 장기 계획 · KODEX 200 '+str(round(weight*100))+'% / 나머지 현금'
            rebalance(book,LONG if weight else None,weight,prices,now,d['trades'],key,reason,names=names)
            book['decision_key']=month; book['status']=reason
    d['last_tick']=now.isoformat(timespec='seconds'); d['quote_at']=d['last_tick']
    d['last_prices']=prices
    equity=sum(b['equity'] for b in d['sleeves'].values())
    entry={'date':today,'equity':round(equity,2)}
    if d['equity_history'] and d['equity_history'][-1]['date']==today: d['equity_history'][-1]=entry
    else: d['equity_history'].append(entry)
    return d


def summary(d):
    names=instrument_names(d)
    sleeves=[]
    for key,b in d['sleeves'].items():
        unrealized=sum(h['qty']*(d.get('last_prices') or {}).get(t, h['cost']/h['qty'])-h['cost']
                       for t,h in b['holdings'].items())
        sleeves.append(dict(key=key,label=b['label'],initial_cash=b['initial_cash'],
            cash=round(b['cash'],2),equity=round(b['equity'],2),pnl=round(b['equity']-b['initial_cash'],2),
            realized_pnl=round(b['realized_pnl'],2),unrealized_pnl=round(unrealized,2),fees=round(b['fees'],2),
            status=b['status'],paused=b['paused'],holdings=[dict(ticker=t,name=names.get(t,t),qty=h['qty'],
            average_cost=round(h['cost']/h['qty'],2)) for t,h in b['holdings'].items()]))
    equity=sum(b['equity'] for b in sleeves)
    gross=sum(b['equity']-b['cash'] for b in d['sleeves'].values())
    inverse=sum(b['holdings'].get(INVERSE,{}).get('qty',0)*(d.get('last_prices') or {}).get(INVERSE,0) for b in d['sleeves'].values())
    from execution.etf_contract import public_summary as execution_summary
    return dict(execution_alignment=execution_summary(d),gross_exposure=round(gross,2),net_direction_exposure=round(gross-2*inverse,2),hedged=bool(inverse and gross>inverse),portfolio_mode=True,initial_cash=d['initial_cash'],equity=equity,
        cash=sum(b['cash'] for b in sleeves),pnl=round(equity-d['initial_cash'],2),
        total_return_pct=round((equity/d['initial_cash']-1)*100,4),sleeves=sleeves,
        days=len(d['equity_history']),trades=len(d['trades']),recent_trades=d['trades'][-30:][::-1],
        equity_curve=d['equity_history'][-250:],last_tick=d['last_tick'],created_at=d['created_at'],
        fees=sum(b['fees'] for b in sleeves),realized_pnl=sum(b['realized_pnl'] for b in sleeves),
        unrealized_pnl=sum(b['unrealized_pnl'] for b in sleeves))


def read_summary(path):
    import json
    from pathlib import Path
    path=Path(path)
    return summary(json.loads(path.read_text(encoding='utf-8'))) if path.exists() else None
