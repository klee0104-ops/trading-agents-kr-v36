"""ETF-specific paper execution and same-window attribution; preserves legacy ledger."""
from datetime import datetime,time,timedelta


def _approved_plan(signal,now,*,shadow_v2=False):
    from execution.split_portfolio import number
    from data.etf_forecast import WEIGHTS_BY_SCHEMA,NEUTRAL_PROBE_WEIGHT,NEUTRAL_TREND_PCT
    plan=((signal or {}).get('final_result') or {}).get('etf_execution') or {}
    try:
        at=datetime.fromisoformat(plan['generated_at'])
        schema=plan.get('schema');today=str(now.date())
        valid=(schema in (2,3,4) and plan.get('status')=='approved'
               and plan.get('target_date')==today and at<=now and at.date()==now.date()
               and at.time().replace(tzinfo=None)<time(9,15))
        if schema in (3,4):valid=valid and (plan.get('etf_qa') or {}).get('passed') is True
        code=plan.get('selected_code');weight=plan.get('weight')
        cap=.50 if shadow_v2 and schema==4 else .40 if schema==4 else .25
        valid=valid and code in (None,'069500','114800') and number(weight) and 0<=weight<=cap and (code is not None or weight==0)
        rows=plan.get('forecasts',[])
        valid=valid and len(rows)==3 and {r['code'] for r in rows}=={'KPI200','069500','114800'}
        valid=valid and all(number(r['up_probability']) and 0<=r['up_probability']<=100 for r in rows)
        if code:
            probability=next(r['up_probability'] for r in rows if r['code']==code)
            if plan.get('selection_basis')=='neutral_probe':
                probe=plan.get('neutral_probe') or {}
                changes=probe.get('five_session_return_pct') or {}
                trend=NEUTRAL_TREND_PCT
                consistent=(changes.get('KPI200',0)>=trend and changes.get('069500',0)>=trend and changes.get('114800',0)<=-trend) if code=='069500' else (changes.get('KPI200',0)<=-trend and changes.get('114800',0)>=trend and changes.get('069500',0)<=-trend)
                valid=valid and schema==4 and all(r['up_probability']==50 for r in rows)
                probe_cap=.10 if shadow_v2 else NEUTRAL_PROBE_WEIGHT
                valid=valid and 0<weight<=probe_cap and consistent
                valid=valid and probe.get('code')==code and probe.get('source_ids')==['M901','M902','M903']
                valid=valid and probe.get('basis_date')<today and probe.get('threshold_pct')==trend
                valid=valid and all(number(v) for v in changes.values()) and set(changes)=={'KPI200','069500','114800'}
            elif schema in WEIGHTS_BY_SCHEMA:
                weak,strong=WEIGHTS_BY_SCHEMA[schema]
                plan_cap=cap if shadow_v2 and schema==4 else (weak if probability<60 else strong)
                valid=valid and probability>50 and weight>0 and weight<=plan_cap
                kpi=next(r['up_probability'] for r in rows if r['code']=='KPI200')  # (V3.31) 지수 방향 일치 확인
                valid=valid and ((code=='069500' and kpi>50) or (code=='114800' and kpi<50))
            else:
                valid=valid and probability>=60
    except (KeyError,TypeError,ValueError,StopIteration):valid=False
    return plan if valid else None


MAX_INTRADAY_ENTRIES = 1  # (V3.39) 아침 진입 외 장중 신규·재진입은 하루 1회


def _intraday_entries(state,today):
    """오늘 단기 계좌의 장중 진입(재진입·장중 AI·탐색) 횟수 — 아침 진입은 제외."""
    return sum(1 for t in state.get('trades',[])
               if t.get('sleeve')=='short' and t.get('side')=='buy' and str(t.get('at',''))[:10]==today
               and any(w in str(t.get('reason','')) for w in ('장중 재진입','장중 AI','가상 탐색')))


def _adverse_and_losing(review,active,book):
    move=review.get('index_move_pct')
    if not isinstance(move,(int,float)):return False
    adverse=move<=-.3 if active.get('selected_code')=='069500' else move>=.3
    return adverse and book['equity']<active.get('entry_equity',book['equity'])


def _intraday_review(state,quotes,now,code):
    """One review per hour from stored real observations, never a fabricated fill."""
    from execution.split_portfolio import number
    today=str(now.date());ledger=state.get('short_intraday') or {}
    if ledger.get('date')!=today:
        ledger={'date':today,'observations':[],'review_hour':None,'last_review':None}
    current=(benchmark(quotes,now).get('KPI200')
             if (quotes.get('KPI200') or {}).get('market_status')=='OPEN' else None)
    if current is not None and now.time().replace(tzinfo=None)>=time(9,5):  # (V3.39) 장 시작 전 시세 제외
        observations=ledger['observations']
        observations.append({'at':now.isoformat(),'value':current})
        ledger['observations']=observations[-100:]
    state['short_intraday']=ledger  # Additive migration; the original journal is untouched.
    clock=now.time().replace(tzinfo=None)
    if not time(10)<=clock<time(15) or now.minute>10 or current is None:
        return None
    hour=now.strftime('%Y-%m-%dT%H')
    if ledger['review_hour']==hour:return None
    lower=now-timedelta(minutes=65);upper=now-timedelta(minutes=20)
    prior=[row for row in ledger['observations'][:-1]
           if number(row.get('value')) and row['value']>0
           and lower<=datetime.fromisoformat(row['at'])<=upper]
    ledger['review_hour']=hour
    if not prior:
        verdict='대기';move=None;reason='20~65분 전 지수 관측값 부족'
    else:
        move=100*(current/prior[-1]['value']-1)
        agrees=move>=.15 if code=='069500' else move<=-.15
        verdict='보유/재진입' if agrees else '현금'
        reason='아침 ETF 방향과 최근 KOSPI200 흐름 일치' if agrees else '최근 지수 흐름 불일치·불분명'
    review={'at':now.isoformat(timespec='seconds'),'verdict':verdict,
            'index_move_pct':round(move,4) if move is not None else None,'reason':reason}
    ledger['last_review']=review
    state.setdefault('short_reviews',[]).append(review)
    state['short_reviews']=state['short_reviews'][-50:]
    return review


def _execution_weight(state,code,weight):
    from execution.aggressive_shadow import MARKER, valid_shadow
    return .40 if code and weight==.25 and state.get('experiment')==MARKER and valid_shadow(state) else weight

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
    matched=(cycle['entry_at'][:10]==str(now.date())
             and now.time().replace(tzinfo=None)<=time(15,25) and len(last)==3)
    for f in cycle['forecasts']:
        code=f['code'];entry=cycle['reference_prices'].get(code);end=last.get(code)
        pct=(end/entry-1)*100 if entry and end else None
        p=f['up_probability'];pred='up' if p>50 else 'down' if p<50 else 'flat'
        actual='up' if pct is not None and pct>0 else 'down' if pct is not None and pct<0 else 'flat' if pct==0 else None
        scores.append({'code':code,'up_probability':p,'return_pct':pct,'predicted':pred,'actual':actual,'correct':pred==actual if actual is not None and matched and reason=='scheduled_exit' and cycle.get('selection_basis') not in ('neutral_probe','intraday_ai_exploratory') else None})
    cycle.update(horizon_matched=matched and reason=='scheduled_exit' and cycle.get('selection_basis') not in ('manual','intraday_ai_exploratory'),exit_at=now.isoformat(),status='closed',exit_reason=reason,net_pnl=book['equity']-cycle['entry_equity'],
                 net_return_pct=100*(book['equity']/cycle['entry_equity']-1),costs=book['fees']-cycle['entry_fees'],scores=scores)
    state.setdefault('short_cycles',[]).append(cycle);state['active_short_cycle']=None

def apply(state,book,signal,prices,quotes,now,names,intraday_plan=None):
    from execution.split_portfolio import rebalance
    from execution.short_profit_exit import POLICY, evaluate
    from execution.short_intraday_ai import approved_plan as approved_intraday
    nowtime=now.time().replace(tzinfo=None);today=str(now.date());active=state.get('active_short_cycle')
    plan=_approved_plan(signal,now,shadow_v2=state.get('experiment')=='activation_shadow_v2')
    review=approved_intraday(intraday_plan,now) if isinstance(intraday_plan,dict) else None
    if active:
        active['peak_equity']=max(active.get('peak_equity',active['entry_equity']),book['equity'])
        active['max_drawdown_pct']=min(active.get('max_drawdown_pct',0),100*(book['equity']/active['peak_equity']-1))
        if active['entry_at'][:10]!=today or nowtime>=time(15,20):
            rebalance(book,None,0,prices,now,state['trades'],'short','ETF 별도 운용 · 예정 청산 (시세 장애 시 다음 유효 시세)',names=names)
            close_cycle(state,book,quotes,now,'scheduled_exit');book['status']='ETF 운용 청산 완료 · 비용 차감 손익 기록';return
        profit_exit=evaluate(active,book,prices)
        if profit_exit:
            reason,observed_net_pct=profit_exit
            label='비용 차감 목표수익 도달' if reason=='profit_target_exit' else '최고 수익률 대비 되돌림'
            active['exit_observed_net_pct']=observed_net_pct
            rebalance(book,None,0,prices,now,state['trades'],'short',label,names=names)
            close_cycle(state,book,quotes,now,reason)
            book['status']=label+' · 실현손익 기록 완료 · 다음 시간별 재진입 조건 대기';return
        if (review and active.get('selection_basis')!='manual'
                and datetime.fromisoformat(review['generated_at'])>datetime.fromisoformat(active['entry_at'])
                and state.get('last_intraday_plan_at')!=review['generated_at']):
            selected=review['selected_code']
            # (V3.39) 장중 AI 가 '방향 근거 부족(현금)'이라고만 할 때는 손실 중일 때만 청산. 반대 ETF 를 고르면 기존대로 교체.
            if selected is None and book['equity']>=active['entry_equity']:
                state['last_intraday_plan_at']=review['generated_at']
                book['status']='장중 AI 재판단 현금 의견 · 이익 구간이라 보유 유지(예정 청산·수익 보호 규칙 적용)';return
            if selected!=active['selected_code']:
                rebalance(book,None,0,prices,now,state['trades'],'short',
                          '장중 AI 재판단 · 기존 ETF 청산 · AI 방향 적중률 제외',names=names)
                close_cycle(state,book,quotes,now,'intraday_ai_switch')
                if selected is None:
                    state['last_intraday_plan_at']=review['generated_at']
                    book['status']='장중 AI 재판단 · 현금화 · 실현손익 기록';return
                active=None  # The new direction may enter on this same fresh quote.
            else:
                state['last_intraday_plan_at']=review['generated_at']
                book['status']='장중 AI 재판단 · 기존 ETF 방향 유지';return
        if active is None:
            pass
        elif active.get('version')==2 and plan and plan.get('schema') in (3,4) and plan.get('selected_code')==active.get('selected_code'):
            review_result=_intraday_review(state,quotes,now,active['selected_code'])
            # (V3.39) 예전엔 지수가 '충분히 오르지 않았다'(+0.15% 미만)만으로도 청산해 사고팔기가 잦았다(7회 −7.4만).
            # 이제 지수가 보유 방향과 반대로 0.3% 이상 움직였고 이 매매가 손실 중일 때만 청산한다.
            if (review_result and review_result['verdict']=='현금'
                    and not _adverse_and_losing(review_result,active,book)):
                book['status']='시간별 재검토 · 흐름 약하지만 손실·역방향 아님 · 보유 유지';return
            if review_result and review_result['verdict']=='현금':
                rebalance(book,None,0,prices,now,state['trades'],'short','시간별 지수 재검토 · 역방향 흐름 + 손실로 청산',names=names)
                close_cycle(state,book,quotes,now,'intraday_exit')
                book['status']='장중 청산 · 실현손익 기록 완료 · 다음 시간 재진입 조건 대기';return
            if review_result:book['status']='시간별 재검토 '+review_result['verdict']+' · '+review_result['reason'];return
        if active is not None:
            book['status']='수동 모의체결 보유 · AI 적중률 제외 · 시세로 손익 평가' if active.get('selection_basis')=='manual' else 'ETF 별도 판단으로 보유 중 · 실제 진입 이후 손익 평가';return
    if book['holdings']:
        # A legacy or untracked holding has no scored v2 cycle. Preserve its
        # original entry, but never carry an intraday short ETF indefinitely.
        held=set(book['holdings'])
        same_day=all(any(t.get('sleeve')=='short' and t.get('ticker')==code and
                             t.get('side')=='buy' and str(t.get('at',''))[:10]==today
                             for t in state.get('trades',[])) for code in held)
        if same_day and nowtime<time(15,20):
            book['status']='기존 단기 보유 · 15:20 이후 첫 유효 시세 청산 대기';return
        before=len(state['trades'])
        rebalance(book,None,0,prices,now,state['trades'],'short',
                  '기존 단기 보유 · 15:20 이후 첫 유효 시세 청산 (과거 체결 유지)',names=names)
        state['legacy_trade_count']=state.get('legacy_trade_count',before)+len(state['trades'])-before
        book['status']='기존 단기 보유 청산 완료 · 실제 시세와 비용 반영';return
    if review and state.get('last_intraday_plan_at')!=review['generated_at']:
        state['last_intraday_plan_at']=review['generated_at']
        code=review['selected_code']
        if code is not None and _intraday_entries(state,today)>=MAX_INTRADAY_ENTRIES:
            book['status']='장중 신규 진입 하루 1회 한도 사용 · 현금 유지';return
        if code is None:
            state['short_v2_decision_day']=today;book['decision_key']=today
            book['status']='장중 AI 재판단 · 방향 근거 부족 · 현금 유지';return
        references=benchmark(quotes,now)
        if len(references)!=3 or code not in prices:
            book['status']='장중 ETF·지수 시세 부족 · 신규 매수 보류';return
        before=len(state['trades']);entry_equity=book['equity'];entry_fees=book['fees']
        exploratory=review['status']=='experimental'
        rebalance(book,code,review['weight'],prices,now,state['trades'],'short',
                  '트레이더 근거 확인 · QA 제한적 5% 가상 탐색' if exploratory else
                  '장중 AI 2역할·ETF 근거 QA 승인 · 단기자금 10% 모의진입',names=names)
        if len(state['trades'])==before:
            book['status']='장중 모의매수 가능 수량 부족';return
        state['active_short_cycle']={'version':3,'selection_basis':review['selection_basis'],
            'entry_at':now.isoformat(),'plan_at':review['generated_at'],'target_date':today,
            'selected_code':code,'entry_equity':entry_equity,'entry_fees':entry_fees,
            'reference_prices':references,'forecasts':[{k:r[k] for k in ('code','up_probability')}
                for r in review['forecasts']],'status':'open','transition_from_legacy':False,
            'exit_policy':dict(POLICY),'trigger':review.get('trigger'),
            'qa_tier':'exploratory' if exploratory else 'approved'}
        state['short_v2_decision_day']=today;book['decision_key']=today
        book['status']=('장중 트레이더 판단 5% 탐색 · 공식 AI 적중률 제외' if exploratory else
                        '장중 AI 재판단 10% 모의진입 · 아침 예측과 별도 평가');return
    if str(state.get('last_intraday_plan_at') or '')[:10]==today:
        book['status']='장중 AI 재판단 계획 종료 · 다음 유효 판단까지 현금 유지';return
    if not signal:
        book['status']='ETF 별도 판단 미확보 · 현금 유지/신규 매매 보류';return
    if not plan:
        book['status']='ETF 별도 판단 검증 미통과 · 현금 유지/신규 매매 보류';return
    code=plan.get('selected_code');weight=plan['weight'];rows=plan['forecasts']
    if code is None:
        state['short_v2_decision_day']=today;book['decision_key']=today
        book['status']='ETF 전용 판단 · 방향 우위 없어 현금 유지';return
    reentry=(plan.get('selection_basis')!='neutral_probe' and state.get('short_v2_decision_day')==today and plan.get('schema') in (3,4)
             and any(c.get('target_date')==today and c.get('exit_reason') in ('intraday_exit','profit_target_exit','profit_trailing_exit')
                     and c.get('selected_code')==code for c in state.get('short_cycles',[])))
    if state.get('short_v2_decision_day')==today and not reentry:
        book['status']='ETF 당일 운용 완료/현금 계획 · 재진입 없음';return
    if reentry and _intraday_entries(state,today)>=MAX_INTRADAY_ENTRIES:
        book['status']='장중 재진입 하루 1회 한도 사용 · 현금 유지';return
    if reentry:
        review=_intraday_review(state,quotes,now,code)
        if not review or review['verdict']!='보유/재진입':
            book['status']='장중 재진입은 다음 시간별 방향 확인을 기다림';return
    elif not time(9,0)<=nowtime<=time(9,15):
        book['status']='신규 진입 시간(09:00–09:15) 외 · 늦은 추격 진입 보류';return
    references=benchmark(quotes,now)
    if len(references)!=3:
        book['status']='KOSPI200/ETF 동일 시점 시세 부족 · 신규 매매 보류';return
    before=len(state['trades']);entry_equity=book['equity'];entry_fees=book['fees']
    rebalance(book,code,_execution_weight(state,code,weight),prices,now,state['trades'],'short',
              'ETF 별도 판단 승인 · 장중 재진입' if reentry else '중립 탐색 · 전일 5거래일 추세 근거 · 단기자금 5% · 15:20 청산 계획' if plan.get('selection_basis')=='neutral_probe' else 'ETF 별도 판단 승인 · 아침 진입 · 15:20 청산 계획',names=names)
    if len(state['trades'])==before:
        book['status']='주문 가능 수량 부족 · 신규 매수 보류';return
    state['active_short_cycle']={'version':2,'entry_at':now.isoformat(),'plan_at':plan['generated_at'],'target_date':today,
         'selected_code':code,'entry_equity':entry_equity,'entry_fees':entry_fees,'reference_prices':references,
         'selection_basis':plan.get('selection_basis','model_probability'),
         'forecasts':[{k:r[k] for k in ('code','up_probability')} for r in rows],'status':'open',
         'transition_from_legacy':False}
    if plan.get('schema')==4:
        state['active_short_cycle']['exit_policy']=dict(POLICY)
    state['short_v2_decision_day']=today
    book['decision_key']=today;book['status']='ETF 장중 재진입 · KOSPI 적중률과 별도 평가' if reentry else f"중립 탐색 {weight:.0%} 모의진입 · 손익 기록/AI 방향 적중률 제외" if plan.get('selection_basis')=='neutral_probe' else 'ETF 별도 운용 시작 · KOSPI 적중률과 별도 평가'

def public_summary(state):
    from execution.split_portfolio import number
    cycles=state.get('short_cycles',[]);curve=[1.];peak=1.;mdd=0.
    neutral=[c for c in cycles if c.get('selection_basis')=='neutral_probe']
    manual=[c for c in cycles if c.get('selection_basis')=='manual']
    intraday_ai=[c for c in cycles if c.get('selection_basis')=='intraday_ai']
    exploratory=[c for c in cycles if c.get('selection_basis')=='intraday_ai_exploratory']
    for c in cycles:
        curve.append(curve[-1]*(1+c['net_return_pct']/100));peak=max(peak,curve[-1]);mdd=min(mdd,(curve[-1]/peak-1)*100)
    return {'policy':state.get('short_policy','legacy'),'started_at':state.get('short_policy_started_at'),
        'note':'KOSPI 시장 전망과 별도 평가 · 실제 진입–청산 구간 · 비용 차감. 과거 체결은 변경하지 않습니다.',
        'legacy_trade_count':state.get('legacy_trade_count',len(state.get('trades',[]))),
        'closed_cycles':len(cycles),'net_pnl':sum(c['net_pnl'] for c in cycles),'net_return_pct':100*(curve[-1]-1),
        'neutral_probe_closed_cycles':len(neutral),'neutral_probe_net_pnl':sum(c['net_pnl'] for c in neutral),
        'manual_closed_cycles':len(manual),'manual_net_pnl':sum(c['net_pnl'] for c in manual),
        'intraday_ai_closed_cycles':len(intraday_ai),'intraday_ai_net_pnl':sum(c['net_pnl'] for c in intraday_ai),
        'exploratory_closed_cycles':len(exploratory),'exploratory_net_pnl':sum(c['net_pnl'] for c in exploratory),
        'closed_cycle_max_drawdown_pct':mdd,'observed_cycle_max_drawdown_pct':min([0]+[c.get('max_drawdown_pct',0) for c in cycles]+[(state.get('active_short_cycle') or {}).get('max_drawdown_pct',0)]),'active':state.get('active_short_cycle'), 'recent_cycles':cycles[-20:][::-1],
        'last_review':(state.get('short_intraday') or {}).get('last_review')}
