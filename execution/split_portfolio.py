"""Two isolated cash/ETF simulation books. No broker or LLM dependencies."""
from copy import deepcopy
from datetime import datetime, timedelta
import math
import re

LONG, INVERSE, LEVERAGE = '069500', '114800', '122630'
NAMES = {LONG: 'KODEX 200', INVERSE: 'KODEX 인버스', LEVERAGE: 'KODEX 레버리지', '005930':'삼성전자', '000660':'SK하이닉스'}
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


def _sleeve(label,cash):
    return dict(label=label, initial_cash=cash, cash=float(cash), holdings={},
                equity=float(cash), realized_pnl=0., fees=0., peak=float(cash),
                session_start=float(cash), session='', paused=False, stop_day='',
                decision_key='', status='신규 계좌 · 판단 대기')


def fresh(now,with_medium=False):
    return dict(schema=1, enabled=True, created_at=now.isoformat(), initial_cash=20_000_000,
                sleeves=({'long':_sleeve('장기',8_000_000),'short':_sleeve('단기·현금',10_000_000),
                          'medium':_sleeve('중기',2_000_000)} if with_medium else
                         {'long':_sleeve('장기',8_000_000),'short':_sleeve('단기·현금',12_000_000)}),
                trades=[], equity_history=[], last_tick=None, quote_at=None)


def enable_medium(state,now,amount=2_000_000):
    """Idempotent internal cash transfer. No trade or historic P/L is rewritten."""
    d=deepcopy(state)
    if 'medium' in d.get('sleeves',{}):return d
    sleeves=d.get('sleeves') or {};short=sleeves.get('short') or {};long=sleeves.get('long') or {}
    if (d.get('schema')!=1 or d.get('initial_cash')!=20_000_000 or
        long.get('initial_cash')!=8_000_000 or short.get('initial_cash')!=12_000_000 or
        short.get('holdings') or d.get('active_short_cycle') or
        any(not number(short.get(k)) or short[k]<amount for k in ('cash','equity','peak','session_start'))):
        return d
    for key in ('initial_cash','cash','equity','peak','session_start'):
        short[key]-=amount
    sleeves['medium']=_sleeve('중기',amount)
    d.setdefault('capital_transfers',[]).append({'at':now.isoformat(),'from':'short',
                                                  'to':'medium','amount':amount})
    return d


def enable_long_core(state, now, amount=2_400_000):
    """Split 30% of the original long capital into a separately audited ETF book.

    Refuse to migrate an invested or malformed long book; never rewrite its
    holdings, fills, realized P/L, or the portfolio's total starting capital.
    """
    d=deepcopy(state)
    sleeves=d.get('sleeves') or {}
    if 'long_core' in sleeves:return d
    long=sleeves.get('long') or {}
    if (d.get('schema')!=1 or d.get('initial_cash')!=20_000_000 or
        long.get('initial_cash')!=8_000_000 or 'medium' not in sleeves or
        long.get('holdings') or
        any(not number(long.get(k)) or long[k]<amount for k in ('cash','equity','peak','session_start'))):
        return d
    for key in ('initial_cash','cash','equity','peak','session_start'):
        long[key]-=amount
    long['label']='장기 기업분석'
    sleeves['long_core']=_sleeve('장기 기준 ETF',amount)
    d.setdefault('capital_transfers',[]).append({'at':now.isoformat(),'from':'long',
                                                  'to':'long_core','amount':amount})
    return d


def enable_leverage_capital(state, now, amount=10_000_000):
    """Record a new virtual contribution, preserving every old fill and equity point."""
    d=deepcopy(state)
    sleeves=d.get('sleeves') or {}
    if 'short_leverage' in sleeves:return d
    if (d.get('schema')!=1 or d.get('initial_cash')!=20_000_000 or amount!=10_000_000
            or not {'short','medium','long','long_core'}<=set(sleeves)
            or sum(b.get('initial_cash',0) for b in sleeves.values())!=20_000_000):
        return d
    sleeves['short_leverage']=_sleeve('단기 레버리지 · 추가 입금',amount)
    if d.get('equity_history') and d['equity_history'][-1].get('date')==str(now.date()):
        d['equity_history'][-1]['contributed_capital']=20_000_000
    d['initial_cash']+=amount
    d.setdefault('capital_events',[]).append({'at':now.isoformat(),'type':'virtual_deposit',
        'to':'short_leverage','amount':amount,'capital_before':20_000_000,
        'capital_after':30_000_000})
    return d


STOPPABLE_SLEEVES = ('long', 'short', 'medium', 'long_core', 'short_leverage')
CORE_TRANCHES = 4
CORE_TRANCHE_GAP = timedelta(days=7)


def review_sleeve_stop(state, now, sleeve, reviewer, reason, *, resume=False):
    """(V3.32) 손실 한도로 멈춘 계좌의 수동 검토·재개 (모의 전용). 모든 계좌에 같은 절차를 적용한다.
    ① 검토 기록(review) → ② 같은 날 재개(resume). 보유 종목이 남아 있으면 재개 불가.
    재개 시 그 시점 평가금액을 새 손실 기준점(risk_restart_peak)으로 잡는다 —
    예전 최고점 기준이면 재개 즉시 다시 정지되어 사실상 영구 정지였다."""
    if sleeve not in STOPPABLE_SLEEVES:
        raise ValueError('Unknown sleeve')
    if not isinstance(reviewer, str) or not 1 <= len(reviewer.strip()) <= 80:
        raise ValueError('Reviewer required')
    if not isinstance(reason, str) or not 10 <= len(reason.strip()) <= 500:
        raise ValueError('Specific review reason required')
    book = (state.get('sleeves') or {}).get(sleeve)
    if not isinstance(book, dict) or book.get('paused') is not True:
        raise ValueError(f'{sleeve} is not stopped')
    if book.get('holdings'):
        raise ValueError('Cannot resume with an unreviewed holding')
    d = deepcopy(state)
    events = d.setdefault('capital_events', [])
    review_type, resume_type = f'{sleeve}_stop_review', f'{sleeve}_stop_resume'
    if resume:
        reviews = [(i, e) for i, e in enumerate(events)
                   if e.get('type') == review_type and e.get('sleeve') == sleeve]
        if not reviews:
            raise ValueError('Stop review required before resume')
        last_resume = max((i for i, e in enumerate(events)
                           if e.get('type') == resume_type and e.get('sleeve') == sleeve),
                          default=-1)
        if reviews[-1][0] <= last_resume:
            raise ValueError('New stop review required after previous resume')
        latest = reviews[-1][1]
        if latest.get('at', '')[:10] != str(now.date()):
            raise ValueError('Stop review must be from this day')
        d['sleeves'][sleeve]['paused'] = False
        d['sleeves'][sleeve]['risk_restart_peak'] = book['equity']
        d['sleeves'][sleeve]['decision_key'] = ''
        d['sleeves'][sleeve]['dca'] = {'done': 0, 'last_at': None, 'started': None} if sleeve == 'long_core' else None
    events.append({'at': now.isoformat(), 'type': resume_type if resume else review_type,
                   'sleeve': sleeve, 'reviewer': reviewer.strip(), 'reason': reason.strip(),
                   'equity_at_review': book['equity']})
    return d


def review_long_core_stop(state, now, reviewer, reason, *, resume=False):
    """Manual paper-only stop review; resumption requires a prior review event."""
    return review_sleeve_stop(state, now, 'long_core', reviewer, reason, resume=resume)


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


def valid_short_signal(envelope, now):
    """New ETF plans use ETF-only QA; legacy plans keep their original gate."""
    if not isinstance(envelope, dict) or envelope.get('mode') != 'real':
        return None
    outcome = envelope.get('outcome') or {}
    plan = (outcome.get('final_result') or {}).get('etf_execution') or {}
    if plan.get('schema') not in (3,4):
        return valid_signal(envelope, now)
    try:
        saved = datetime.fromisoformat(envelope['saved_at'])
        if saved.tzinfo is None: saved = saved.replace(tzinfo=now.tzinfo)
        if not 0 <= (now-saved).total_seconds() <= 48*3600:
            return None
    except (KeyError, TypeError, ValueError):
        return None
    if outcome.get('target_date') != str(now.date()): return None
    if plan.get('status') != 'approved' or (plan.get('etf_qa') or {}).get('passed') is not True:
        return None
    return outcome


def mark(book, prices):
    book['equity'] = book['cash'] + sum(h['qty']*prices[t] for t,h in book['holdings'].items())
    book['peak'] = max(book['peak'],book['equity'])


def rebalance(book, target_code, weight, prices, now, journal, sleeve, reason, targets=None, names=None, preserve_codes=()):
    """Sell before buying; average cost includes entry fees, exits realize net P/L."""
    names=names or NAMES
    equity=book['equity']
    desired={t:0 for t in prices}
    if target_code:
        desired[target_code]=int(equity*weight/(prices[target_code]*(1+SLIPPAGE)*(1+FEE)))
    if targets is not None:
        for code,weight in targets.items():
            desired[code]=int(equity*weight/(prices[code]*(1+SLIPPAGE)*(1+FEE)))
    for code in preserve_codes:
        if code in book['holdings']:
            desired[code]=book['holdings'][code]['qty']
    for side in ('sell','buy'):
        for code in prices:
            h=book['holdings'].get(code,{'qty':0,'cost':0.})
            delta=desired[code]-h['qty']
            if side=='sell' and delta>=0 or side=='buy' and delta<=0: continue
            qty=abs(delta); fill=prices[code]*(1+SLIPPAGE if side=='buy' else 1-SLIPPAGE)
            if side=='buy': qty=min(qty,int(book['cash']/(fill*(1+FEE))))
            if qty<=0: continue
            fee=qty*fill*(FEE+(.002 if side=='sell' and code not in (LONG,INVERSE,LEVERAGE) else 0)); gross=qty*fill; realized=0.
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


def tick(state, envelope, quotes, now, long_report=None, intraday_plan=None, medium_report=None):
    """Pure transition, quotes observed immediately before calling. No backdated fills."""
    from data.sessions import market_status
    d=deepcopy(state)
    if d.get('schema')!=1 or not d.get('enabled'): return d
    # No new exchange prices exist on a full market holiday. Keep the last
    # complete mark instead of recording a fresh-looking partial valuation.
    if market_status(now)['code'] in ('weekend', 'holiday'): return d
    if d.get('last_tick') and datetime.fromisoformat(d['last_tick']) >= now: return d
    names=instrument_names(d,long_report)
    if medium_report is not None:names.update(instrument_names(d,medium_report))
    short=d['sleeves'].get('short',{});long=d['sleeves'].get('long',{})
    medium=d['sleeves'].get('medium') or {}
    long_core=d['sleeves'].get('long_core') or {}
    leverage=d['sleeves'].get('short_leverage') or {}
    short_required=set(short.get('holdings',{}))
    if isinstance(intraday_plan,dict) and intraday_plan.get('selected_code') in (LONG,INVERSE):
        short_required.add(intraday_plan['selected_code'])
    if not short_required or d.get('short_policy')!='etf_v2':
        short_required.update((LONG,INVERSE))
    long_required=set(long.get('holdings',{}))
    if long_report is None:
        long_required.add(LONG)
    long_candidates={a.get('code') for a in (long_report or {}).get('allocations',[])
                     if isinstance(a,dict) and number(a.get('weight')) and a['weight']>0}
    medium_required=set(medium.get('holdings',{}))
    if medium_report and medium_report.get('status')=='approved' and medium_report.get('selected_code'):
        medium_required.add(medium_report['selected_code'])
    core_required={LONG} if long_core else set()
    leverage_required=set(leverage.get('holdings',{}))
    if leverage and LEVERAGE in quotes:leverage_required.add(LEVERAGE)
    required=short_required|long_required|long_candidates|medium_required|core_required|leverage_required
    prices={};unavailable=set()
    for code in required:
        if code not in names:
            unavailable.add(code);continue
        q=quotes.get(code) or {}
        try:
            observed=datetime.fromisoformat(q['observed_at'])
            if not 0 <= (now-observed).total_seconds() <= 60: raise ValueError('stale quote')
            if q['date'] != str(now.date()): raise ValueError('wrong session')
            traded=datetime.fromisoformat(q['traded_at'])
            if traded.tzinfo is None: traded=traded.replace(tzinfo=now.tzinfo)
            if not 0 <= (now-traded).total_seconds() <= 900: raise ValueError('stale trade')
            if not number(q.get('value')) or q['value']<=0: raise ValueError('bad price')
            if q.get('market_status')!='OPEN' and market_status(now)['code']=='open':
                raise ValueError('not open')
        except (KeyError,TypeError,ValueError):
            unavailable.add(code);continue
        prices[code]=q['value']
    sleeve_ready={'short':not (short_required & unavailable),
                  'long':not (long_required & unavailable),
                  'medium':not (medium_required & unavailable),
                  'long_core':not (core_required & unavailable),
                  'short_leverage':not (leverage_required & unavailable)}
    if not any(sleeve_ready.get(key,False) for key in d['sleeves']):
        return d
    d['instruments']={c:{'name':n} for c,n in names.items()}
    today=str(now.date()); month=today[:7]
    signal=valid_signal(envelope,now)
    short_signal=valid_short_signal(envelope,now) if d.get('short_policy')=='etf_v2' else signal
    open_market=market_status(now)['code']=='open'
    for key,book in d['sleeves'].items():
        if not sleeve_ready.get(key,False):
            book['status']='필수 보유 시세 누락·지연 · 해당 계좌 점검 보류'
            continue
        if book['session']!=today:
            book['session_start']=book['equity']; book['session']=today
        mark(book,prices)
        if number(book.get('risk_restart_peak')) and book['risk_restart_peak']>0:  # (V3.32) 재개한 모든 계좌
            book['risk_restart_peak']=max(book['risk_restart_peak'], book['equity'])
            risk_peak=book['risk_restart_peak']
        else:
            risk_peak=book['peak']
        dd=book['equity']/risk_peak-1 if risk_peak else 0
        day_loss=book['equity']/book['session_start']-1 if book['session_start'] else 0
        if dd<=(-.15 if key in ('long','long_core') else -.12 if key=='medium' else -.06 if key=='short_leverage' else -.10): book['paused']=True
        if key=='short' and day_loss<=-.02: book['stop_day']=today
        # (V3.38) 레버리지 계좌 일손실 한도 −0.6% → −0.9%: 30% 비중 × ETF 손절 −2.5% = 계좌 −0.75% 이므로
        # 전용 손절이 먼저 작동하고, 이 한도는 시세 공백·급변 때의 안전판으로만 쓴다(예전엔 이 한도가 먼저 걸렸다).
        if key=='short_leverage' and day_loss<=-.009: book['stop_day']=today
        if not open_market:
            book['status']='장외 · 평가만 갱신'; continue
        if book['paused'] or book['stop_day']==today:
            reason='손실 한도 도달 · 현금화 및 신규 매수 중지'
            rebalance(book,None,0,prices,now,d['trades'],key,reason,names=names)
            if key=='short_leverage':
                from execution.leverage_contract import close_cycle as close_leverage_cycle
                close_leverage_cycle(d,book,now,'risk_exit')
            if key=='short' and d.get('short_policy')=='etf_v2':
                from execution.etf_contract import close_cycle
                close_cycle(d,book,quotes,now,'risk_exit')
            book['status']=reason; continue
        if key=='short_leverage':
            from execution.leverage_contract import apply as apply_leverage
            apply_leverage(d,book,short_signal,intraday_plan,prices,quotes,now,names)
        elif key=='short':
            if d.get('short_policy')=='etf_v2':
                from execution.etf_contract import apply
                apply(d,book,short_signal,prices,quotes,now,names,intraday_plan=intraday_plan)
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
        elif key=='medium':
            from execution.medium_contract import apply as apply_medium
            apply_medium(book,medium_report,prices,now,d['trades'],names)
        elif key=='long_core':
            if set(book['holdings'])-{LONG}:
                book['status']='기준 ETF 외 보유 자산 확인 필요 · 신규 주문 보류';continue
            # (V3.39) 한 번에 전액 매수 → 4회 분할(25%씩, 회차 사이 최소 7일). 10/8 일괄 매수 뒤 시장 −17%로 −40.9만 손절된 사례
            dca=book.get('dca') or {}
            if book['holdings'].get(LONG) and not dca:
                book['decision_key']='core-initial-entry'
                book['status']='장기 기준 ETF 보유(분할 도입 전 매수분) · 기업 분석 계정과 손익 분리';continue
            if not book['holdings'].get(LONG) and book.get('decision_key')=='core-initial-entry' and not dca:
                book['status']='기준 ETF 청산 뒤 자동 재진입 보류(검토·재개 후 분할 매수)';continue
            done=int(dca.get('done',0))
            if done>=CORE_TRANCHES:
                book['status']=f'장기 기준 ETF 분할 매수 완료({CORE_TRANCHES}회) · 보유 평가';continue
            try:
                last_at=datetime.fromisoformat(dca['last_at']) if dca.get('last_at') else None
            except (TypeError,ValueError):
                last_at=None
            if last_at and now-last_at<CORE_TRANCHE_GAP:
                book['status']=f"장기 기준 ETF 분할 매수 {done}/{CORE_TRANCHES} · 다음 회차 {(last_at+CORE_TRANCHE_GAP).date()} 이후";continue
            before=len(d['trades'])
            rebalance(book,LONG,(done+1)/CORE_TRANCHES,prices,now,d['trades'],key,
                      f'장기 기준 ETF 분할 매수 {done+1}/{CORE_TRANCHES}회 · 기업 선별과 별도',names=names)
            if any(t['side']=='buy' and t['sleeve']==key for t in d['trades'][before:]):
                book['dca']={'done':done+1,'last_at':now.isoformat(),'started':dca.get('started') or now.isoformat()}
                book['decision_key']='core-initial-entry'
                book['status']=f'장기 기준 ETF 분할 매수 {done+1}/{CORE_TRANCHES} 완료 · 다음 회차 7일 뒤'
            else:
                book['status']='유효 시세 확인 · 정수 주문 가능 수량 부족'
        else:
            if long_report is not None:
                if long_candidates-unavailable != long_candidates:
                    book['status']='장기 신규 매수 후보 시세 누락·지연 · 추가 매수 보류'
                    continue
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
                positive=[a for a in allocations if a['weight']>0]
                if not positive:
                    book['status']='승인된 종목별 매수 배분 없음 · 현금/기존 보유 유지';continue
                ledger=book.get('long_execution') or {}
                if ledger.get('month')!=month:
                    # Preserve a month already processed by the old engine on upgrade.
                    old_done=book.get('decision_key')==month
                    ledger={'month':month,'filled_codes':[a['code'] for a in positive] if old_done else []}
                filled=set(ledger['filled_codes'])
                blocked=set(long_report.get('blocked_codes') or [])
                ready=[a for a in positive if a['code'] not in filled and a['code'] not in blocked
                       and prices[a['code']]<=a['buy_below']]
                if not ready:
                    book['long_execution']=ledger
                    if all(a['code'] in filled for a in positive):
                        book['status']='장기팀 월간 목표 유지 · 보유 평가'
                    elif all(a['code'] in blocked for a in positive if a['code'] not in filled):
                        book['status']='승인 종목의 뉴스 재검토/수집 상태 확인 필요 · 추가 매수 보류'
                    else:
                        book['status']='장기 매수 기준가 도달 대기 · 종목별 확인'
                    continue
                preserve=set(book['holdings']) & (filled|blocked|{a['code'] for a in positive if a not in ready})
                preserved_value=sum(book['holdings'][c]['qty']*prices[c] for c in preserve)
                if preserved_value+sum(a['weight']*book['equity'] for a in ready) > book['equity']*.5+1:
                    book['status']='장기 총 비중 50% 한도 대기';continue
                before=len(d['trades'])
                rebalance(book,None,0,prices,now,d['trades'],key,'독립 장기팀 승인 · 종목별 월간 모의 배분',
                          targets={a['code']:a['weight'] for a in ready},names=names,preserve_codes=preserve)
                bought={t['ticker'] for t in d['trades'][before:] if t['side']=='buy' and t['sleeve']==key}
                ledger['filled_codes']=sorted(filled|bought)
                book['long_execution']=ledger
                if bought: book['decision_key']=month
                book['status']='장기팀 종목별 계획 반영 완료' if bought else '현금 또는 주문 가능 수량 부족 · 추가 매수 대기'
                continue
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
    held_codes={code for book in d['sleeves'].values() for code in book['holdings']}
    valuation_missing=unavailable & held_codes
    d['quote_health']={'status':'partial' if valuation_missing else 'ok',
                       'missing_codes':sorted(c for c in unavailable if isinstance(c,str)),
                       'missing_held_codes':sorted(c for c in valuation_missing if isinstance(c,str)),
                       'at':d['last_tick']}
    d['last_prices']={**(d.get('last_prices') or {}),**prices}
    equity=sum(b['equity'] for b in d['sleeves'].values())
    if not valuation_missing:
        exposure_by_sleeve={}
        for key, book in d['sleeves'].items():
            invested=book['equity']-book['cash']
            exposure_by_sleeve[key]={'invested_won':round(invested,2),
                'cash_won':round(book['cash'],2),
                'exposure_pct':round(100*invested/book['equity'],2) if book['equity']>0 else None,
                'cash_pct':round(100*book['cash']/book['equity'],2) if book['equity']>0 else None}
        entry={'date':today,'equity':round(equity,2),'contributed_capital':d['initial_cash'],
               'exposure_pct':round(100*sum(v['invested_won'] for v in exposure_by_sleeve.values())/equity,2) if equity>0 else None,
               'cash_pct':round(100*sum(v['cash_won'] for v in exposure_by_sleeve.values())/equity,2) if equity>0 else None,
               'exposure_by_sleeve':exposure_by_sleeve}
        if d['equity_history'] and d['equity_history'][-1]['date']==today: d['equity_history'][-1]=entry
        else: d['equity_history'].append(entry)
    return d


def valuation_audit(d,now=None):
    """Reconcile paper cash, holdings, realized P/L and marked equity."""
    if not d.get('last_tick'):
        return {'status':'waiting','note':'첫 시세 평가 대기'}
    try:
        prices=d['last_prices']
        if not isinstance(prices,dict) or not number(d['initial_cash']) or d['initial_cash']<=0:
            raise ValueError('ledger')
        equity=realized=unrealized=0.
        for book in d['sleeves'].values():
            if not all(number(book[k]) for k in ('cash','equity','initial_cash','realized_pnl')):
                raise ValueError('book')
            marked=0.;open_pnl=0.
            for code,h in book['holdings'].items():
                price=prices.get(code)
                if not number(price) or price<=0 or type(h.get('qty')) is not int or h['qty']<=0 or not number(h.get('cost')) or h['cost']<0:
                    raise ValueError('holding')
                marked+=h['qty']*price
                open_pnl+=h['qty']*price-h['cost']
            if abs(book['equity']-(book['cash']+marked))>.05:
                raise ValueError('equity')
            if abs((book['equity']-book['initial_cash'])-(book['realized_pnl']+open_pnl))>.05:
                raise ValueError('pnl')
            equity+=book['equity'];realized+=book['realized_pnl'];unrealized+=open_pnl
        if abs((equity-d['initial_cash'])-(realized+unrealized))>.05:
            raise ValueError('total')
        health=d.get('quote_health') or {}
        if health.get('status')=='partial' and health.get('at')==d['last_tick']:
            return {'status':'partial','note':'일부 종목 시세 누락 · 해당 계좌 점검 및 전체 수익률 표시 보류',
                    'checked_at':d['last_tick']}
        stamp=datetime.fromisoformat(d['last_tick'])
        if stamp.tzinfo is None: raise ValueError('timestamp')
        if now is not None:
            from data.sessions import market_status
            age=(now-stamp).total_seconds()
            if age<0 or (market_status(now)['code']=='open' and age>600):
                return {'status':'stale','note':'장중 시세 평가가 10분 이상 지연 · 수익률 표시 보류',
                        'checked_at':d['last_tick']}
        return {'status':'ok','note':'모의 원장 계산 일치 · 거래소 독립 시세 검증은 아님',
                'checked_at':d['last_tick']}
    except (KeyError,TypeError,ValueError,OverflowError):
        return {'status':'mismatch','note':'원장·손익 계산 불일치 또는 시세 누락 · 수익률 표시 보류',
                'checked_at':d.get('last_tick')}


def summary(d):
    return summary_at(d)


def last_complete_valuation(d):
    """Historical complete-quote mark, never a substitute for current equity."""
    for row in reversed(d.get('equity_history') or []):
        if not isinstance(row,dict): continue
        date=row.get('date')
        equity=row.get('equity')
        capital=row.get('contributed_capital')
        if (not isinstance(date,str) or not re.fullmatch(r'\d{4}-\d{2}-\d{2}',date)
                or not number(equity) or equity<0 or not number(capital) or capital<=0):
            continue
        try: datetime.fromisoformat(date)
        except ValueError: continue
        return {'date':date,'equity':round(equity,2),
                'contributed_capital':capital,
                'pnl':round(equity-capital,2),
                'total_return_pct':round((equity/capital-1)*100,4)}
    return None


def summary_at(d,now=None):
    audit=valuation_audit(d,now)
    hide=audit['status'] in ('mismatch','stale','partial')
    prices={} if audit['status']=='mismatch' else d.get('last_prices') or {}
    names=instrument_names(d)
    sleeves=[]
    for key,b in d['sleeves'].items():
        unrealized=0 if audit['status']=='mismatch' else sum(
            h['qty']*prices.get(t, h['cost']/h['qty'])-h['cost'] for t,h in b['holdings'].items())
        sleeve_exposure=(b['equity']-b['cash'])/b['equity']*100 if b['equity']>0 else None
        sleeves.append(dict(key=key,label=b['label'],initial_cash=b['initial_cash'],
            cash=round(b['cash'],2),equity=None if hide else round(b['equity'],2),pnl=None if hide else round(b['equity']-b['initial_cash'],2),
            exposure_pct=None if hide or sleeve_exposure is None else round(sleeve_exposure,2),
            cash_pct=None if hide or sleeve_exposure is None else round(100-sleeve_exposure,2),
            realized_pnl=None if audit['status']=='mismatch' else round(b['realized_pnl'],2),
            unrealized_pnl=None if hide else round(unrealized,2),fees=round(b['fees'],2),
            status=b['status'],paused=b['paused'],holdings=[dict(ticker=t,name=names.get(t,t),qty=h['qty'],
            average_cost=round(h['cost']/h['qty'],2)) for t,h in b['holdings'].items()]))
    equity=sum(b['equity'] for b in d['sleeves'].values())
    gross=sum(b['equity']-b['cash'] for b in d['sleeves'].values())
    leverage_exposure=0 if audit['status']=='mismatch' else sum(
        b['holdings'].get(LEVERAGE,{}).get('qty',0)*prices.get(LEVERAGE,0) for b in d['sleeves'].values())
    inverse=0 if audit['status']=='mismatch' else sum(
        b['holdings'].get(INVERSE,{}).get('qty',0)*prices.get(INVERSE,0) for b in d['sleeves'].values())
    from execution.etf_contract import public_summary as execution_summary
    day_base=sum(b['session_start'] for b in d['sleeves'].values())
    day_ready=(bool(d.get('last_tick')) and all(b.get('session')==d['last_tick'][:10] for b in d['sleeves'].values())
               and number(day_base) and day_base>0)
    events=d.get('capital_events') or []
    public_events=[{k:e.get(k) for k in ('at','type','to','amount','capital_before','capital_after','sleeve')}
                   for e in events if isinstance(e,dict)]
    base_capital=d['initial_cash']-sum(e.get('amount',0) for e in events)
    curve=[]
    for row in d['equity_history'][-250:]:
        capital=row.get('contributed_capital')
        if not number(capital):
            capital=base_capital+sum(e.get('amount',0) for e in events if str(e.get('at',''))[:10]<=row.get('date',''))
        curve.append({'date':row['date'],'equity':row['equity'],'contributed_capital':capital})
    exposure_history=[{'date':row.get('date'),'exposure_pct':row.get('exposure_pct'),
                       'cash_pct':row.get('cash_pct'),'sleeves':row.get('exposure_by_sleeve')}
                      for row in d.get('equity_history',[])[-30:]
                      if number(row.get('exposure_pct')) and number(row.get('cash_pct'))]
    leverage_cycles=d.get('leverage_cycles') or []
    leverage_waiting=None
    try:
        last=datetime.fromisoformat(d['last_tick'])
        leverage_start=next((e.get('at') for e in d.get('capital_events',[])
                             if isinstance(e,dict) and e.get('type')=='virtual_deposit' and e.get('to')=='short_leverage'),
                            d['created_at'])
        created=datetime.fromisoformat(leverage_start)
        leverage_buys=[t for t in d.get('trades',[]) if t.get('sleeve')=='short_leverage' and t.get('side')=='buy']
        if (last-created).days>=14 and not leverage_buys:
            from collections import Counter
            counts=Counter(r.get('reason') for r in d.get('leverage_reviews',[])
                           if isinstance(r,dict) and r.get('passed') is False and isinstance(r.get('reason'),str))
            leverage_waiting={'days':(last-created).days,'entry_count':0,
                              'top_reasons':[{'reason':reason,'count':count} for reason,count in counts.most_common(3)]}
    except (KeyError,ValueError,TypeError):
        pass
    reviews={r.get('at'):r for r in d.get('short_reviews',[]) if isinstance(r,dict) and isinstance(r.get('at'),str)}
    recent_trades=[]
    for trade in d['trades'][-30:][::-1]:
        public_trade=trade.copy()
        if trade.get('sleeve')=='short' and trade.get('side')=='sell':
            review=reviews.get(trade.get('at'))
            if review:
                public_trade['decision_review']={
                    'index_move_pct':review.get('index_move_pct') if number(review.get('index_move_pct')) else None,
                    'verdict':review.get('verdict') if isinstance(review.get('verdict'),str) else None,
                    'reason':review.get('reason') if isinstance(review.get('reason'),str) else None,
                }
        recent_trades.append(public_trade)
    return dict(execution_alignment=execution_summary(d),leverage_review=d.get('leverage_last_review'),
        leverage_waiting=leverage_waiting,
        leverage_summary={'completed_cycles':len(leverage_cycles),
                          'active':bool(d.get('active_leverage_cycle')),
                          'recent_cycles':leverage_cycles[-5:][::-1]},
        gross_exposure=None if hide else round(gross+leverage_exposure,2),net_direction_exposure=None if hide else round(gross+leverage_exposure-2*inverse,2),hedged=bool(inverse and gross>inverse) if not hide else False,portfolio_mode=True,initial_cash=d['initial_cash'],equity=None if hide else equity,
        cash=sum(b['cash'] for b in sleeves),pnl=None if hide else round(equity-d['initial_cash'],2),
        total_return_pct=None if hide else round((equity/d['initial_cash']-1)*100,4),
        day_pnl=None if hide or not day_ready else round(equity-day_base,2),
        day_return_pct=None if hide or not day_ready else round((equity/day_base-1)*100,4),
        valuation_audit=audit,last_complete_valuation=last_complete_valuation(d),sleeves=sleeves,
        days=len(d['equity_history']),trades=len(d['trades']),recent_trades=recent_trades,
        equity_curve=curve,exposure_history=exposure_history,capital_events=public_events,last_tick=d['last_tick'],created_at=d['created_at'],
        fees=sum(b['fees'] for b in sleeves),
        realized_pnl=None if audit['status']=='mismatch' else round(sum(b['realized_pnl'] for b in d['sleeves'].values()),2),
        unrealized_pnl=None if hide else round(sum(
            sum(h['qty']*prices.get(t,h['cost']/h['qty'])-h['cost'] for t,h in b['holdings'].items())
            for b in d['sleeves'].values()),2))


def read_summary(path):
    import json
    from pathlib import Path
    path=Path(path)
    if not path.exists(): return None
    from data.sessions import kst_now
    return summary_at(json.loads(path.read_text(encoding='utf-8')),kst_now())
