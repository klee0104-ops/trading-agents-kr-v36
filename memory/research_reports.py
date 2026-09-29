"""Immutable dated team reports: fixed public fields, bounded archive, no model calls."""
from pathlib import Path
from datetime import datetime
import hashlib,json,re
from security.secret_store import redact

def clean(value,titles=()):
    text=' '.join(str(value or '').split())[:1800]
    flat=re.sub(r'\s+','',text)
    if any(len(t)>10 and re.sub(r'\s+','',t) in flat for t in titles):return '[기사 제목 인용 생략]'
    return redact(text)

def pick(source,keys,titles=()):
    return {k:clean(source[k],titles) if isinstance(source[k],str) else source[k] for k in keys if k in source and isinstance(source[k],(str,int,float,bool,type(None)))}

def short_report(envelope):
    if not envelope or envelope.get('mode')!='real':return None
    o=envelope.get('outcome') or {};stamp=envelope.get('saved_at')
    if not stamp or not o.get('final_result'):return None
    datetime.fromisoformat(stamp)
    titles=[re.sub(r'^(\[[^\]]*\]\s*)+','',str(t)) for t in o.get('news_titles',[])]
    fr=o['final_result'];pred=fr.get('kospi_prediction') or {}
    roles=[]
    for name,r in (o.get('analyst_reports') or {}).items():
        roles.append({'name':clean(name),'summary':clean(r.get('summary'),titles),
                      'direction':clean(r.get('direction')),'points':[clean(p,titles) for p in r.get('notable_points',[])[:6]],
                      'citation_ok':r.get('citation_ok')})
    plan=fr.get('etf_execution') or {}
    execution={**pick(plan,('status','note','target_date','entry_window','exit_time','selected_code','weight','futures_note')),'forecasts':[{**pick(f,('code','name','up_probability','horizon')),'reasoning':[clean(v,titles) for v in f.get('reasoning',[])[:3]]} for f in plan.get('forecasts',[])[:3]]}
    return {'execution':execution,'team':'short','at':stamp,'target_date':clean(o.get('target_date')),'title':'단기투자팀 · 시장 판단 보고서',
            'prediction':{**pick(pred,('direction','up_probability','confidence','position','outlook_label')),
                          'reasoning':[clean(p,titles) for p in pred.get('reasoning',[])[:6]]},
            'roles':roles[:12],
            'debate':[dict(pick(t,('round','stance','argument','confidence'),titles),evidence=[clean(p,titles) for p in t.get('evidence',[])[:4]]) for t in o.get('debate_transcript',[])[:10]],
            'moderation':pick(o.get('moderation') or {},('stronger_side','summary','debate_quality'),titles),
            'risk':pick(o.get('risk_assessment') or {},('risk_level','summary'),titles),
            'qa':{'consistent':(o.get('final_qa') or {}).get('consistent'),
                  'risk_passed':(o.get('verification') or {}).get('verification_passed')},
            'sources':[pick(r,('id','kind','source','date','stock','name')) for r in sorted(o.get('source_registry',[]),key=lambda r:r.get('id') not in ('M901','M902','M903'))[:60]],
            'stocks':[pick(s,('name','directional_signal','direction_bias','reason'),titles) for k in ('core_stocks','watch_stocks') for s in fr.get(k,[])][:16]}

def append_report(path,report):
    if not report:return
    from security.public_report import check_data,configured_secrets
    from tools.paper_portfolio import locked,write
    # Fail closed rather than publishing suspicious or unexpectedly structured content.
    check_data(report,configured_secrets())
    body=json.dumps(report,ensure_ascii=False,sort_keys=True,allow_nan=False)
    report={**report,'id':hashlib.sha256(body.encode()).hexdigest()[:20]}
    path=Path(path)
    with locked(path):
        data=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'schema':1,'reports':[]}
        if any(r['id']==report['id'] for r in data['reports']):return
        data['reports'].append(report)
        data['reports']=sorted(data['reports'],key=lambda r:r['at'],reverse=True)[:120]
        write(path,data)

def archive_short(envelope,path):
    p=Path(path)
    if p.exists() and envelope:
        old=json.loads(p.read_text(encoding='utf-8'))
        if any(r.get('team')=='short' and r.get('at')==envelope.get('saved_at') for r in old.get('reports',[])):return
    append_report(path,short_report(envelope))

def archive_long(state,path,now):
    from memory.long_report import public_summary
    report=state.get('report') or {}
    if not report.get('generated_at'):return
    # Freeze evidence at the report timestamp; hourly news must not create report revisions.
    stamp=report['generated_at'];path=Path(path)
    if path.exists():
        old=json.loads(path.read_text(encoding='utf-8'))
        if any(r['team']=='long' and r['at']==stamp for r in old.get('reports',[])):return
    summary=public_summary(state,now)
    summary['status']=report.get('status','held')
    summary['allocations']=report.get('allocations',[])
    summary['sources']=[pick(s,('id','kind','date','source','stock','name')) for s in report.get('sources',[])[:40]]
    # Live news is a separate monitoring view. Historical references come only from the team's registry.
    summary.update(events=[],news_count=0,news_status='archived',news_checked_at=stamp,block_reasons=[],blocked_codes=[])
    append_report(path,{'team':'long','at':stamp,'target_date':stamp[:10],'title':'장기투자팀 · 기업가치 검토 보고서','research':summary})

def read_public(path):
    from security.public_report import check_data,configured_secrets
    path=Path(path)
    data=json.loads(path.read_text(encoding='utf-8')) if path.exists() else {'reports':[]}
    selected=[];size=0
    for row in data.get('reports',[]):
        if len(selected)>=24:break
        n=len(json.dumps(row,ensure_ascii=False).encode('utf-8'))
        if size+n>230000:break
        selected.append(row);size+=n
    check_data(selected,configured_secrets())
    return {'reports':selected,'total':len(data.get('reports',[])),'note':'생성 당시 판단 기록 · 현재 매수 지시가 아닙니다. 모델 해석과 수집 자료를 구분해 확인하세요.'}

def sync_latest(path,outcome_path,long_path,now):
    from memory.outcome_store import load_last_outcome
    from memory.long_report import load
    archive_short(load_last_outcome(outcome_path),path)
    archive_long(load(long_path),path,now)
    return read_public(path)
