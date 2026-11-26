"""Private research storage and strictly selected display/decision summaries."""
from datetime import datetime, timedelta
import json
from pathlib import Path
from data.long_research import UNIVERSE, finite

def load(path):
    p=Path(path)
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}

def decision(state,now):
    report=state.get('report') or {};news=state.get('news') or {}
    universe={**UNIVERSE,**(state.get('catalog') or {}).get('instruments',{})}
    blocked=set();reasons=[];block_by_code={}
    try:
        age=now-datetime.fromisoformat(news['checked_at'])
        if news.get('status')!='ok' or not timedelta(0)<=age<=timedelta(hours=2):
            blocked.update(universe);reasons.append('뉴스 수집 불완전/2시간 이상 경과')
    except (KeyError,ValueError,TypeError):
        blocked.update(universe);reasons.append('뉴스 미수집')
    if state.get('alerts_overflow'):
        blocked.update(universe);reasons.append('위험 알림 누적 한도 도달 · 수동 점검 필요')
    reviewed=set(report.get('reviewed_news_ids',[])) if report.get('status')=='approved' else set()
    for n in state.get('alerts',[]):
        if n['id'] not in reviewed:
            for relation in n.get('relations',[]):
                code=relation.get('code')
                if code in universe:
                    blocked.add(code)
                    block_by_code.setdefault(code,[]).append('중요 뉴스 재검토 대기')
    if blocked and not reasons: reasons.append('중요 뉴스 재검토 대기')
    try:
        valid=(report.get('status')=='approved' and datetime.fromisoformat(report['generated_at'])<=now<=datetime.fromisoformat(report['valid_until']))
    except (KeyError,ValueError,TypeError):valid=False
    catalog=state.get('catalog')
    if catalog is not None:
        try:
            current=catalog.get('status')=='ok' and timedelta(0)<=now-datetime.fromisoformat(catalog['checked_at'])<=timedelta(days=3)
        except (KeyError,TypeError,ValueError):current=False
        if not current:valid=False;reasons.append('후보군 정보 갱신 필요')
    if state.get('dart_financial_required'):
        checked={f.get('code'):f for f in report.get('fundamentals',[])}
        for allocation in report.get('allocations',[]):
            if allocation.get('weight',0)<=0:continue
            code=allocation.get('code')
            f=checked.get(code) or {}
            verification=f.get('dart_verification') or {}
            if verification.get('status') not in ('verified','verified_derived_eps') or verification.get('period_end')!=f.get('period_end'):
                blocked.add(code)
                block_by_code.setdefault(code,[]).append('OpenDART 재무·EPS 대조 미완료')
        if blocked and not reasons:reasons.append('OpenDART 재무·EPS 대조 미완료')
    return {'reviewed_codes':[f['code'] for f in report.get('fundamentals',[])],'instruments':{c:{'name':u['name'],'sector':u.get('sector','Unknown')} for c,u in universe.items()},'status':'approved' if valid else 'held','generated_at':report.get('generated_at'),
            'valid_until':report.get('valid_until'),'allocations':report.get('allocations',[]) if valid else [],
            'blocked_codes':sorted(blocked),'block_reasons':reasons,
            'block_by_code':{code:list(dict.fromkeys(items)) for code,items in block_by_code.items()},
            'horizon_days':report.get('horizon_days',180)}


def candidate_gate(f,report,gate):
    """Explain the current policy without turning an incomplete report into approval."""
    from data.long_research import finite
    code=f.get('code');m=f.get('metrics') or {}
    checks=[('최근 4분기 순이익',m.get('ttm_income'),'> 0'),
            ('최근 4분기 잉여현금흐름',m.get('ttm_fcf'),'> 0'),
            ('최근 4분기 EPS',m.get('ttm_eps'),'> 0'),
            ('매출 전년동기 대비',m.get('revenue_yoy_pct'),'>= 0%'),
            ('부채/자본',m.get('debt_equity'),'0~1'),
            ('PER',m.get('pe'),'0~20'),('PBR',m.get('pb'),'0~3')]
    predicates=[lambda x:x>0,lambda x:x>0,lambda x:x>0,lambda x:x>=0,
                lambda x:0<=x<=1,lambda x:0<x<=20,lambda x:0<x<=3]
    details=[{'label':label,'value':value if finite(value) else None,'required':required,
              'result':'unknown' if not finite(value) else 'pass' if predicate(value) else 'fail'}
             for (label,value,required),predicate in zip(checks,predicates)]
    missing=list(f.get('missing') or [])
    sector_gap=f.get('sector') in ('Financial Services','Unknown') and missing==['업종별 추가 자료 검증 필요 · 자동 매수 제외']
    verified=(f.get('dart_verification') or {}).get('status')
    if verified and verified not in ('verified','verified_derived_eps'):
        phase='data_wait';reason='OpenDART 재무·EPS 대조 실패 또는 불완전'
    elif f.get('status') not in ('ok','insufficient') or (missing and not sector_gap) or any(c['result']=='unknown' for c in details):
        phase='data_wait';reason='필수 재무자료 확인 대기'
    elif f.get('sector') in ('Financial Services','Unknown'):
        phase='sector_review';reason='업종별 추가 검증 대기'
    elif any(c['result']=='fail' for c in details[:5]):
        phase='fundamental_fail';reason='수익성·성장·부채 기준 미충족'
    elif any(c['result']=='fail' for c in details[5:]):
        phase='valuation_wait';reason='현재 가치평가 기준 초과 · 가격 또는 재무 변화 재검토'
    elif gate['status']!='approved':
        phase='report_wait';reason='장기팀 보고서 승인 또는 갱신 대기'
    else:
        allocation=next((a for a in gate['allocations'] if a.get('code')==code and a.get('weight',0)>0),None)
        if allocation is None:
            phase='thesis_wait';reason='장기팀의 종목별 매수 배분 없음'
        elif code in gate['blocked_codes']:
            phase='news_wait';reason=' / '.join(gate.get('block_by_code',{}).get(code) or gate.get('block_reasons') or ['뉴스 수집 상태 확인 대기'])
        elif finite(m.get('price')) and m['price']>allocation['buy_below']:
            phase='price_wait';reason='매수 기준가 도달 대기'
        else:
            phase='eligible';reason='보고서·가격 기준 충족 (현재 시세는 모의 주문 시 재확인)'
    return {'phase':phase,'reason':reason,'criteria':details,
            'next_review':'승인된 매수 배분이 없으면 다음 날 최대 3회 모델 조사, 배분이 있으면 7일 뒤 재검토. 재무 누락은 매일 다시 수집',
            'buy_below':next((a['buy_below'] for a in gate['allocations'] if a.get('code')==code and a.get('weight',0)>0),None)}

def public_summary(state,now):
    from rules.citations import sanitize_external
    import re
    report=state.get('report') or {};news=state.get('news') or {}
    dart=state.get('dart') or {}
    universe={**UNIVERSE,**(state.get('catalog') or {}).get('instruments',{})};gate=decision(state,now)
    # Never include raw news titles, private prompts or full provider responses.
    titles=[n.get('title','') for n in news.get('events',[])]+[n.get('title','') for n in state.get('alerts',[])]
    def clean(value):
        s=' '.join(str(value or '').split())[:600]
        for title in titles:
            if len(title)>10: s=s.replace(title,'[기사 제목 생략]')
        return s
    dart_financial_checks=[]
    for f in state.get('fundamentals',[])[:5]:
        code=f.get('code')
        if code not in universe:continue
        validation=f.get('dart_verification') or {}
        status=validation.get('status')
        dart_financial_checks.append({'code':code,'name':universe[code]['name'],
                                      'status':status if status in ('verified','verified_derived_eps','partial_eps','partial_debt','partial_debt_eps','mismatch','unavailable') else 'waiting',
                                      'period_end':validation.get('period_end') or f.get('period_end'),
                                      'checked_at':validation.get('checked_at'),
                                      'reason':clean(validation.get('reason')) if validation.get('reason') else None})
    candidates=[]
    for f in (report.get('fundamentals') or state.get('fundamentals',[])):
        code=f.get('code')
        if code not in universe:continue
        validation=f.get('dart_verification') or {}
        safe_validation={'status':validation.get('status','waiting') if validation.get('status') in ('verified','verified_derived_eps','partial_eps','partial_debt','partial_debt_eps','mismatch','unavailable') else 'waiting',
                         'period_end':validation.get('period_end'),'eps_basis':validation.get('eps_basis'),
                         'reason':clean(validation.get('reason')) if validation.get('reason') else None,
                         'checks':[{'metric':c.get('metric'),
                                    'yahoo':c.get('yahoo') if finite(c.get('yahoo')) else None,
                                    'dart':c.get('dart') if finite(c.get('dart')) else None,
                                    'difference_pct':c.get('difference_pct') if finite(c.get('difference_pct')) else None,
                                    'match':c.get('match') is True}
                                   for c in validation.get('checks',[])[:8] if isinstance(c,dict) and c.get('metric') in ('ttm_income','ttm_fcf','equity','debt','latest_quarter_revenue','prior_year_quarter_revenue','ttm_eps')],
                         'receipts':[{'id':str(r),'url':'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+str(r)}
                                     for r in validation.get('receipts',[])[:3] if re.fullmatch(r'[0-9]{14}',str(r))]}
        candidates.append({'code':code,'name':universe[code]['name'],'status':f.get('status'),
                           'eligible':f.get('eligible',False),'period_end':f.get('period_end'),
                           'collected_at':f.get('collected_at'),'metrics':f.get('metrics',{}),
                           'missing':f.get('missing',[]),'eps_gap_details':f.get('eps_gap_details',[]),
                           'dart_verification':safe_validation,
                           'source':f.get('source','자료 미확보'),
                           'gate':candidate_gate(f,report,gate)})
    gate_labels={'data_wait':'자료·EPS·공시 확인 대기','sector_review':'업종별 검증 대기',
                 'fundamental_fail':'수익성·부채 기준 미충족','valuation_wait':'PER·PBR 가격 기준 대기',
                 'report_wait':'장기팀 보고서 미승인·만료','thesis_wait':'종목별 매수 배분 없음',
                 'news_wait':'뉴스 위험 검토 대기','price_wait':'매수 기준가 대기',
                 'eligible':'매수 조건 충족'}
    gate_counts=[{'code':key,'label':label,
                  'count':sum(c['gate']['phase']==key for c in candidates)}
                 for key,label in gate_labels.items()]
    roles=[]
    for r in report.get('roles',[]):
        rows=[{'name':universe.get(s['code'],{'name':s['code']})['name'],'stance':s['stance'],
               'points':[clean(p) for p in s['points']], 'invalidation':[clean(p) for p in s['invalidation']]} for s in r.get('stocks',[])]
        roles.append({'name':r['name'],'stocks':rows,'summary':[clean(p) for p in r.get('summary',[])]})
    events=[]
    reviewed=set(report.get('reviewed_news_ids',[])) if report.get('status')=='approved' else set()
    for n in news.get('events',[])[:30]:
        events.append({'evidence_id':('N'+str(report.get('reviewed_news_ids',[]).index(n['id'])+1)) if n['id'] in reviewed else None,'source':n['source'],'published_at':n['published_at'],'url':n['url'],
                       'risk_flag':n['risk_flag'],'reviewed':n['id'] in reviewed,'relations':n['relations']})
    dart_events=[]
    for event in dart.get('events',[])[:12]:
        if not isinstance(event,dict):continue
        receipt=str(event.get('id',''));code=str(event.get('code',''))
        if not re.fullmatch(r'[0-9]{14}',receipt) or not re.fullmatch(r'[0-9]{6}',code):continue
        dart_events.append({'code':code,'name':sanitize_external(str(event.get('name','')),80),
                            'title':sanitize_external(str(event.get('title','')),160),
                            'filed_date':str(event.get('filed_date','')) if re.fullmatch(r'[0-9]{8}',str(event.get('filed_date',''))) else '',
                            'url':'https://dart.fss.or.kr/dsaf001/main.do?rcpNo='+receipt})
    catalog=state.get('catalog') or {}
    watch={'label':(catalog.get('universe') or {}).get('label','조사 후보군 대기'),'status':catalog.get('status','waiting'),
           'checked_at':catalog.get('checked_at'),'note':(catalog.get('universe') or {}).get('note',''),
           'count':len((catalog.get('universe') or {}).get('members',[])),
           'rows':[{k:s.get(k) for k in ('code','name','sector','status','score','reasons','missing','rank','period_end','metrics','source')} for s in catalog.get('screens',[]) if s['code'] in catalog.get('watchlist',[])],
           'screen_counts':{key:sum(s.get('status')==key for s in catalog.get('screens',[])) for key in ('research','watch','needs_data')},
           'batch':catalog.get('batch',[]),'next_batch':catalog.get('next_batch',[]),'changes':catalog.get('changes',[])[-8:]}
    note=report.get('note','장기팀 조사 대기')
    if report.get('status')=='approved' and not gate['allocations']:
        note='장기 조사 완료 · 승인된 종목별 매수 배분 없음 (각 종목의 자료·가치평가 조건 확인)'
    issues={'missing company evidence':'종목별 재무 근거 ID 연결 실패',
            'citation validation':'근거 ID 또는 수치 검증 실패',
            'coverage':'조사 대상 전체 검토 누락'}
    review_issue=issues.get(report.get('failure_reason'),
                            '모델 응답 또는 보고서 검증 실패' if report.get('failure_reason') else None)
    adjustments=[{k:a.get(k) for k in ('code','reason','requested_weight','approved_weight')}
                 for a in report.get('allocation_adjustments',[])[:4]
                 if isinstance(a,dict) and a.get('code') in universe and
                 a.get('reason') in ('fundamental_or_critic_block','price_above_starter_limit','starter_valuation')]
    return {'deferred':[{'name':clean(f['name']),'missing':[clean(m) for m in f.get('missing',[])]} for f in report.get('deferred',[])],'watchlist':watch,'status':gate['status'],'generated_at':report.get('generated_at'),'valid_until':report.get('valid_until'),
            'note':clean(note),'review_issue':review_issue,'candidates':candidates,'gate_counts':gate_counts,'roles':roles,
            'allocations':gate['allocations'],'allocation_adjustments':adjustments,
            'blocked_codes':gate['blocked_codes'],'block_reasons':gate['block_reasons'],
            'block_by_code':gate['block_by_code'],
            'news_status':news.get('status','waiting'),'news_checked_at':news.get('checked_at'),
            'news_count':len(news.get('events',[])),'events':events,
            'dart_financial_checks':dart_financial_checks,
            'dart_status':dart.get('status','waiting'),'dart_checked_at':dart.get('checked_at'),
            'dart_last_success_at':dart.get('last_success_at'),
            'dart_partial_reason':dart.get('partial_reason'),
            'dart_tracked_count':dart.get('tracked_count',0),'dart_events':dart_events,
            'calls':report.get('calls',0)}

def read_public(path):
    from data.sessions import kst_now
    return public_summary(load(path),kst_now())
