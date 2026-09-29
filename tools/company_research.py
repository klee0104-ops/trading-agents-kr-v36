"""Explicit local-only research, isolated from all portfolio and signal stores."""
from pathlib import Path
from datetime import datetime
import re
from memory.long_report import load, public_summary
from data.long_research import UNIVERSE
from tools.paper_portfolio import locked, write

def directory(long_path):
    catalog=load(long_path).get('catalog') or {}
    instruments={**UNIVERSE,**catalog.get('instruments',{})}
    return {c:{'name':str(v['name'])[:80],'symbol':c+'.KS','sector':v.get('sector','Unknown'),'aliases':v.get('aliases',[v['name']]),'related':v.get('related',[])}
            for c,v in instruments.items() if re.fullmatch(r'[0-9A-Z]{6}',c) and isinstance(v,dict) and v.get('name')}

def read_public(path,long_path):
    universe=directory(long_path)
    records=load(path).get('reports',[])[-20:]
    return {'companies':[{'code':c,'name':v['name']} for c,v in universe.items()],
            'reports':records,'scope':'현재 관리 중인 코스피 시가총액 상위 100개 기업 · 목록 확보 전에는 기본 2개 기업',
            'note':'별도 기업 조사 자료입니다. 예측 적중률·장기 운용 계획·모의계좌 주문에 반영하지 않습니다.'}

def run(path,long_path,code,now,client_factory):
    from data.long_research import collect_fundamentals,collect_news
    from agents.long_team import run_team,research_ready
    from security.public_report import check_data,configured_secrets
    universe=directory(long_path)
    if not isinstance(code,str) or code not in universe:raise ValueError('지원 목록에서 기업을 선택하세요.')
    path=Path(path)
    with locked(path):
        state=load(path)
        if state.get('attempt_day')==str(now.date()):raise ValueError('오늘의 기업 심층분석 한도 1건을 사용했습니다. 저장된 보고서를 확인하세요.')
        # Reserve even failed attempts before network/model work; restart cannot bypass budget.
        state['attempt_day']=str(now.date());write(path,state)
        item=research_one(universe,code,now,client_factory)
        state['reports']=(state.get('reports',[])+[item])[-20:];write(path,state)
        return item


def research_one(universe,code,now,client_factory):
    from data.long_research import collect_fundamentals,collect_news
    from agents.long_team import run_team,research_ready
    from security.public_report import check_data,configured_secrets
    selected={code:universe[code]}
    try:
        facts=collect_fundamentals(now,selected)
        news=collect_news(now,set(),selected)
        ready=bool(facts) and all(research_ready(f) for f in facts) and news.get('status')=='ok'
        report=run_team(client_factory() if ready else None,facts,news,now,universe=selected,max_calls=3)
        # Reuse only the sanitized display summary; never store model drafts/news titles.
        clean=public_summary({'report':report,'news':news,'catalog':{'instruments':selected}},now)
        item={'code':code,'name':universe[code]['name'],'generated_at':now.isoformat(),
              'status':'reviewed' if report.get('status')=='approved' else 'held',
              'note':clean['note'],'candidates':clean['candidates'],'roles':clean['roles'],
              'news_count':clean['news_count'],'news_status':clean['news_status'],
              'calls':report.get('calls',0),'sources':[{k:s.get(k,'') for k in ('id','source','date')} for s in report.get('sources',[])]}
        check_data(item,configured_secrets())
    except Exception:
        item={'code':code,'name':universe[code]['name'],'generated_at':now.isoformat(),'status':'failed',
              'note':'자료 수집 또는 검증 실패 · 분석 완료로 표시하지 않습니다.','candidates':[],'roles':[],'calls':None}
    return item
