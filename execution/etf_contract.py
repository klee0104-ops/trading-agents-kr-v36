"""ETF-specific paper execution and same-window attribution; preserves legacy ledger."""
from datetime import datetime,time

def benchmark(quotes,now):
    from execution.split_portfolio import number
    out={}
    for code in ('069500','114800','KPI200'):
        q=quotes.get(code,{})
        try:
            at=datetime.fromisoformat(q['observed_at']);traded=datetime.fromisoformat(q['traded_at'])
            if traded.tzinfo is None:traded=traded.replace(tzinfo=now.tzinfo)
            if q.get('date')==str(now.date()) and 0<=(now-at).total_seconds()<=60 and 0<=(now-traded).total_seconds()<=900 and number(q.get('value')) and q['value']>0:out[code]=q['value']
        except (KeyError,TypeError,ValueError):pass
    return out

def close_cycle(state,book,quotes,now,reason):
    cycle=state.get('active_short_cycle')
    if not cycle:return
    cycle['peak_equity']=max(cycle.get('peak_equity',cycle['entry_equity']),book['equity'])
    cycle['max_drawdown_pct']=min(cycle.get('max_drawdown_pct',0),100*(book['equity']/cycle['peak_equity']-1))
    last=benchmark(quotes,now);scores=[]
    matched=cycle['entry_at'][:10]==str(now.date()) and now.time().replace(tzinfo=None)<=time(15,25)
    for f in cycle['forecasts']:
        code=f['code'];entry=cycle['reference_prices'].get(code);end=last.get(code)
        pct=(end/entry-1)*100 if entry and end else None
        p=f['up_probability'];pred='up' if p>50 else 'down' if p<50 else 'flat'
        actual='up' if pct is not None and pct>0 else 'down' if pct is not None and pct<0 else 'flat' if pct==0 else None
        scores.append({'code':code,'up_probability':p,'return_pct':pct,'predicted':pred,'actual':actual,'correct':pred==actual if actual is not None and matched and reason=='scheduled_exit' else None})
    cycle.update(horizon_matched=matched and reason=='scheduled_exit',exit_at=now.isoformat(),status='closed',exit_reason=reason,net_pnl=book['equity']-cycle['entry_equity'],
                 net_return_pct=100*(book['equity']/cycle['entry_equity']-1),costs=book['fees']-cycle['entry_fees'],scores=scores)
    state.setdefault('short_cycles',[]).append(cycle);state['active_short_cycle']=None

def apply(state,book,signal,prices,quotes,now,names):
    from execution.split_portfolio import rebalance,number
    nowtime=now.time().replace(tzinfo=None);today=str(now.date());active=state.get('active_short_cycle')
    if active:
        active['peak_equity']=max(active.get('peak_equity',active['entry_equity']),book['equity'])
        active['max_drawdown_pct']=min(active.get('max_drawdown_pct',0),100*(book['equity']/active['peak_equity']-1))
        if active['entry_at'][:10]!=today or nowtime>=time(15,20):
            rebalance(book,None,0,prices,now,state['trades'],'short','ETF 별도 운용 · 예정 청산 (시세 장애 시 다음 유효 시세)',names=names)
            close_cycle(state,book,quotes,now,'scheduled_exit');book['status']='ETF 운용 청산 완료 · 비용 차감 손익 기록';return
        book['status']='ETF 별도 판단으로 보유 중 · 실제 진입 이후 손익 평가';return
    if state.get('short_v2_decision_day')==today:
        book['status']='ETF 당일 운용 완료/현금 계획 · 재진입 없음';return
    if not signal:
        book['status']='ETF 별도 판단 미확보 · 기존 보유 유지/신규 매매 보류';return
    plan=(signal.get('final_result') or {}).get('etf_execution') or {}
    try:
        at=datetime.fromisoformat(plan['generated_at'])
        valid=plan.get('schema')==2 and plan.get('status')=='approved' and plan.get('target_date')==today and at<=now and at.date()==now.date() and at.time().replace(tzinfo=None)<time(9,15)
        code=plan.get('selected_code');weight=plan.get('weight')
        valid=valid and code in (None,'069500','114800') and number(weight) and 0<=weight<=.25 and (code is not None or weight==0)
        rows=plan.get('forecasts',[])
        valid=valid and len(rows)==3 and {r['code'] for r in rows}=={'KPI200','069500','114800'}
        valid=valid and all(number(r['up_probability']) and 0<=r['up_probability']<=100 for r in rows)
        if code:valid=valid and next(r['up_probability'] for r in rows if r['code']==code)>=60
    except (KeyError,TypeError,ValueError,StopIteration):valid=False
    if not valid:
        book['status']='ETF 별도 판단 검증 미통과 · 기존 보유 유지/신규 매매 보류';return
    if not time(9,0)<=nowtime<=time(9,15):
        book['status']='신규 진입 시간(09:00–09:15) 외 · 늦은 추격 진입 보류';return
    references=benchmark(quotes,now)
    if len(references)!=3:
        book['status']='KOSPI200/ETF 동일 시점 시세 부족 · 신규 매매 보류';return
    state['active_short_cycle']={'version':2,'entry_at':now.isoformat(),'plan_at':plan['generated_at'],'target_date':today,
         'selected_code':code,'entry_equity':book['equity'],'entry_fees':book['fees'],'reference_prices':references,
         'forecasts':[{k:r[k] for k in ('code','up_probability')} for r in rows],'status':'open',
         'transition_from_legacy':bool(book['holdings'])}
    rebalance(book,code,weight,prices,now,state['trades'],'short','ETF 별도 판단 승인 · 아침 진입 · 15:20 청산 계획',names=names)
    state['short_v2_decision_day']=today
    book['decision_key']=today;book['status']='ETF 별도 운용 시작 · KOSPI 적중률과 별도 평가'

def public_summary(state):
    from execution.split_portfolio import number
    cycles=state.get('short_cycles',[]);curve=[1.];peak=1.;mdd=0.
    for c in cycles:
        curve.append(curve[-1]*(1+c['net_return_pct']/100));peak=max(peak,curve[-1]);mdd=min(mdd,(curve[-1]/peak-1)*100)
    return {'policy':state.get('short_policy','legacy'),'started_at':state.get('short_policy_started_at'),
        'note':'KOSPI 시장 전망과 별도 평가 · 실제 진입–청산 구간 · 비용 차감. 과거 체결은 변경하지 않습니다.',
        'legacy_trade_count':state.get('legacy_trade_count',len(state.get('trades',[]))),
        'closed_cycles':len(cycles),'net_pnl':sum(c['net_pnl'] for c in cycles),'net_return_pct':100*(curve[-1]-1),
        'closed_cycle_max_drawdown_pct':mdd,'observed_cycle_max_drawdown_pct':min([0]+[c.get('max_drawdown_pct',0) for c in cycles]+[(state.get('active_short_cycle') or {}).get('max_drawdown_pct',0)]),'active':state.get('active_short_cycle'), 'recent_cycles':cycles[-20:][::-1]}
