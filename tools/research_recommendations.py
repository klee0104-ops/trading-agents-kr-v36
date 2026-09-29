"""Explicit local batch for today's analysis candidates; no portfolio updates."""
from pathlib import Path
from tools.company_research import directory,research_one
from tools.paper_portfolio import locked,write
from memory.long_report import load

def run_batch(path,long_path,outcome_path,now,client_factory,progress=lambda x:None):
    universe=directory(long_path);by_name={u['name']:c for c,u in universe.items()}
    envelope=load(outcome_path);outcome=envelope.get('outcome',{})
    if str(envelope.get('saved_at',''))[:10]!=str(now.date()):raise ValueError('Today analysis required')
    fr=outcome.get('final_result') or {}
    names=list(dict.fromkeys(s['name'] for k in ('core_stocks','watch_stocks') for s in fr.get(k,[])))
    if not 1<=len(names)<=12 or any(n not in by_name for n in names):raise ValueError('Candidate mapping invalid')
    results=[]
    with locked(Path(path)):
        state=load(path);budget=state.setdefault('batch_budget',{})
        if budget.get('day')!=str(now.date()):budget={'day':str(now.date()),'codes':[]};state['batch_budget']=budget
        for name in names:
            code=by_name[name]
            if code in budget['codes']:
                progress({'code':code,'status':'already_attempted'});continue
            if len(budget['codes'])>=12:raise ValueError('Daily batch budget reached')
            budget['codes'].append(code);write(Path(path),state)
            item=research_one(universe,code,now,client_factory)
            item['origin']='explicit_today_batch';item['prediction_saved_at']=envelope['saved_at']
            state['reports']=(state.get('reports',[])+[item])[-20:];write(Path(path),state)
            results.append(item);progress({'code':code,'status':item['status'],'calls':item.get('calls')})
    return results

def main():
    import argparse,json
    from config import Config
    from data.sessions import kst_now
    from cli import _real_llm
    parser=argparse.ArgumentParser();parser.add_argument('--today-recommendations',action='store_true');a=parser.parse_args()
    if not a.today_recommendations:parser.error('Explicit batch flag required')
    if not Config.api_key():raise SystemExit('Local API key missing')
    run_batch(Path(Config.COMPANY_RESEARCH_PATH),Config.LONG_RESEARCH_PATH,Config.LAST_OUTCOME_PATH,kst_now(),_real_llm,
              lambda status:print(json.dumps(status),flush=True))

if __name__=='__main__':main()
