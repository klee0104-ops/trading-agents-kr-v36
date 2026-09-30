"""Hourly all-session news; weekly bounded long team. No order APIs."""
from datetime import datetime, timedelta
from pathlib import Path
import argparse
from tools.paper_portfolio import locked,write
from memory.long_report import load

def cycle(path,portfolio_path,now,client_factory=None,expanded=False,short_names=()):
    from data.long_research import collect_news,collect_fundamentals
    from agents.long_team import run_team,research_ready
    state=load(path);portfolio=load(portfolio_path)
    held=set(t for b in portfolio.get('sleeves',{}).values() for t in b.get('holdings',{}))
    old_news=state.get('news',{})
    universe=None
    if expanded:
        from data.long_universe import update_catalog,choose_batch
        from data.long_research import UNIVERSE
        catalog=update_catalog(state.get('catalog'),now,held,short_names)
        instruments={**UNIVERSE,**catalog.get('instruments',{})}
        long_held=set(portfolio.get('sleeves',{}).get('long',{}).get('holdings',{}))
        # Every holding must be reviewed before any portfolio replacement. Up to five companies per batch.
        extras=choose_batch(catalog.get('watchlist',[]),(),catalog.get('review_history',{}),3)
        catalog['next_batch']=list(dict.fromkeys(sorted(long_held)+extras))[:5]
        universe={c:instruments[c] for c in catalog['next_batch'] if c in instruments}
        state['catalog']=catalog
        active=set(universe)|held|{f['code'] for f in (state.get('report') or {}).get('fundamentals',[])}
        news_codes=list(dict.fromkeys(sorted(held)+[f['code'] for f in (state.get('report') or {}).get('fundamentals',[])]+sorted(active)))
        news_universe={c:instruments[c] for c in news_codes if c in instruments}
        write(path,state)
    news=collect_news(now,held,news_universe) if expanded else collect_news(now,held)
    if news['status']=='unavailable':news['events']=old_news.get('events',[])
    state['news']=news
    alerts={n['id']:n for n in state.get('alerts',[])}
    alerts.update({n['id']:n for n in news['events'] if n['risk_flag']})
    # Persist unresolved alerts even if their RSS entries age out.
    if len(alerts)>500:state['alerts_overflow']=True
    state['alerts']=list(alerts.values())[-500:]
    write(path,state)
    model_at=state.get('last_model_review_at')
    due=not model_at or now-datetime.fromisoformat(model_at)>=timedelta(days=7)
    data_gap=any(f.get('status') in ('insufficient','unavailable') for f in state.get('fundamentals',[]))
    if (due or data_gap) and state.get('fundamentals_checked_day')!=str(now.date()):
        state['fundamentals']=collect_fundamentals(now,universe) if expanded else collect_fundamentals(now);state['fundamentals_checked_day']=str(now.date());write(path,state)
    if due:
        facts=state.get('fundamentals',[])
        deferred=[]
        if expanded:
            deferred=[f for f in facts if not research_ready(f)]
            facts=[f for f in facts if research_ready(f)]
            universe={f['code']:universe[f['code']] for f in facts if f['code'] in universe}
        ready=bool(facts) and len(facts)<=5 and all(research_ready(f) for f in facts) and news['status']=='ok'
        if expanded:ready=ready and state['catalog'].get('status')=='ok' and bool(universe)
        llm=None
        if ready and client_factory:
            # Reserve the entire weekly attempt before constructing/calling the model.
            state['last_model_review_at']=now.isoformat()
            if expanded:
                state['catalog']['batch']=list(universe)
                state['catalog'].setdefault('review_history',{}).update({c:now.isoformat() for c in universe})
            write(path,state)
            try: llm=client_factory()
            except Exception: pass
        if not ready or llm is not None:
            reviewed=set((state.get('report') or {}).get('reviewed_news_ids',[]))
            pending=[n for n in state['alerts'] if n['id'] not in reviewed]
            review_news={**news,'events':list({n['id']:n for n in pending+news['events']+state['alerts']}.values())}
            state['report']=run_team(llm,facts,review_news,now,universe=universe)
            state['report']['deferred']=[{'code':f['code'],'name':f['name'],'missing':f.get('missing',[])} for f in deferred]
            if expanded and not facts:
                state['catalog'].setdefault('review_history',{}).update({c:now.isoformat() for c in state['catalog'].get('next_batch',[])})
        elif ready:
            state['report']={'status':'held','generated_at':now.isoformat(),'fundamentals':facts,'note':'모델 사용 불가 · 장기 매수 보류','calls':0}
        write(path,state)
    from memory.research_reports import archive_long
    archive_long(state,path.parent/'research_reports.json',now)
    return state

def real_client():
    from config import Config
    from agents.base import build_llm_client
    llm=build_llm_client(provider=Config.LLM_PROVIDER,anthropic_key=Config.api_key('anthropic') if Config.LLM_PROVIDER=='anthropic' else '',
        anthropic_model=Config.ANTHROPIC_MODEL,gemini_key=Config.api_key('gemini') if Config.LLM_PROVIDER=='gemini' else '',
        gemini_model=Config.GEMINI_MODEL,gemini_web_search_enabled=False)
    if hasattr(llm,'_call_with_retry'):
        original=llm._call_with_retry
        llm._call_with_retry=lambda config,user:original(config,user,max_retries=1)
    return llm

def main():
    from config import Config
    from data.sessions import kst_now
    p=argparse.ArgumentParser();p.add_argument('--cycle',action='store_true');args=p.parse_args()
    if not args.cycle:return 1
    path=Path(Config.LONG_RESEARCH_PATH);status='failed'
    try:
        with locked(path):
            from memory.outcome_store import load_last_outcome
            outcome=(load_last_outcome(Config.LAST_OUTCOME_PATH) or {}).get('outcome',{})
            picks=(outcome.get('final_result') or {})
            names={r.get('name') for k in ('core_stocks','watch_stocks') for r in picks.get(k,[])}
            state=cycle(path,Path(Config.PAPER_PORTFOLIO_PATH),kst_now(),real_client,expanded=True,short_names=names)
        status='updated';code=0
    except Exception:code=1
    log=path.parent/'logs';log.mkdir(exist_ok=True)
    write(log/'long_research_status.json',{'at':kst_now().isoformat(),'status':status})
    print('Long research: '+status)
    return code

if __name__=='__main__':raise SystemExit(main())
