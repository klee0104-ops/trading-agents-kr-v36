"""Private research storage and strictly selected display/decision summaries."""
from datetime import datetime, timedelta
import json
from pathlib import Path
from data.long_research import UNIVERSE

def load(path):
    p=Path(path)
    return json.loads(p.read_text(encoding='utf-8')) if p.exists() else {}

def decision(state,now):
    report=state.get('report') or {};news=state.get('news') or {}
    universe={**UNIVERSE,**(state.get('catalog') or {}).get('instruments',{})}
    blocked=set();reasons=[]
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
            blocked.update(r['code'] for r in n['relations'] if r['code'] in universe)
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
    return {'reviewed_codes':[f['code'] for f in report.get('fundamentals',[])],'instruments':{c:{'name':u['name'],'sector':u.get('sector','Unknown')} for c,u in universe.items()},'status':'approved' if valid else 'held','generated_at':report.get('generated_at'),
            'valid_until':report.get('valid_until'),'allocations':report.get('allocations',[]) if valid else [],
            'blocked_codes':sorted(blocked),'block_reasons':reasons,'horizon_days':report.get('horizon_days',180)}

def public_summary(state,now):
    from rules.citations import sanitize_external
    report=state.get('report') or {};news=state.get('news') or {}
    universe={**UNIVERSE,**(state.get('catalog') or {}).get('instruments',{})};gate=decision(state,now)
    # Never include raw news titles, private prompts or full provider responses.
    titles=[n.get('title','') for n in news.get('events',[])]+[n.get('title','') for n in state.get('alerts',[])]
    def clean(value):
        s=' '.join(str(value or '').split())[:600]
        for title in titles:
            if len(title)>10: s=s.replace(title,'[기사 제목 생략]')
        return s
    candidates=[]
    for f in report.get('fundamentals',state.get('fundamentals',[])):
        code=f.get('code')
        if code not in universe:continue
        candidates.append({'code':code,'name':universe[code]['name'],'status':f.get('status'),
                           'eligible':f.get('eligible',False),'period_end':f.get('period_end'),
                           'collected_at':f.get('collected_at'),'metrics':f.get('metrics',{}),
                           'missing':f.get('missing',[]),'source':f.get('source','자료 미확보')})
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
    catalog=state.get('catalog') or {}
    watch={'label':(catalog.get('universe') or {}).get('label','조사 후보군 대기'),'status':catalog.get('status','waiting'),
           'checked_at':catalog.get('checked_at'),'note':(catalog.get('universe') or {}).get('note',''),
           'count':len((catalog.get('universe') or {}).get('members',[])),
           'rows':[{k:s.get(k) for k in ('code','name','sector','status','score','reasons','missing','rank','period_end','metrics','source')} for s in catalog.get('screens',[]) if s['code'] in catalog.get('watchlist',[])],
           'screen_counts':{key:sum(s.get('status')==key for s in catalog.get('screens',[])) for key in ('research','watch','needs_data')},
           'batch':catalog.get('batch',[]),'next_batch':catalog.get('next_batch',[]),'changes':catalog.get('changes',[])[-8:]}
    return {'deferred':[{'name':clean(f['name']),'missing':[clean(m) for m in f.get('missing',[])]} for f in report.get('deferred',[])],'watchlist':watch,'status':gate['status'],'generated_at':report.get('generated_at'),'valid_until':report.get('valid_until'),
            'note':clean(report.get('note','장기팀 조사 대기')),'candidates':candidates,'roles':roles,
            'allocations':gate['allocations'],'blocked_codes':gate['blocked_codes'],'block_reasons':gate['block_reasons'],
            'news_status':news.get('status','waiting'),'news_checked_at':news.get('checked_at'),
            'news_count':len(news.get('events',[])),'events':events,'calls':report.get('calls',0)}

def read_public(path):
    from data.sessions import kst_now
    return public_summary(load(path),kst_now())
