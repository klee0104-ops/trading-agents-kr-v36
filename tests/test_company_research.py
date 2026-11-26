from datetime import datetime
import json
import pytest
from data.sessions import KST
from tools import company_research as cr

def test_budget_reserved_failure_isolated_and_no_paid_call_without_data(tmp_path,monkeypatch):
    from data import long_research as data
    path=tmp_path/'company_research.json';long=tmp_path/'long.json'
    long.write_text('{}');before=long.read_bytes()
    monkeypatch.setattr(data,'collect_fundamentals',lambda *a,**kw:[])
    monkeypatch.setattr(data,'collect_news',lambda *a:{'status':'unavailable','events':[]})
    def forbidden():raise AssertionError('must not create model without data')
    now=datetime(2026,9,29,10,tzinfo=KST)
    out=cr.run(path,long,'005930',now,forbidden)
    assert out['status']=='held' and out['calls']==0
    assert long.read_bytes()==before
    with pytest.raises(ValueError):cr.run(path,long,'005930',now,forbidden)
    assert len(cr.read_public(path,long)['reports'])==1
    with pytest.raises(ValueError):cr.run(path,long,'../../bad',now,forbidden)

def test_company_api_security(monkeypatch,tmp_path):
    from web import server
    from config import Config
    monkeypatch.setattr(Config,'COMPANY_RESEARCH_PATH',str(tmp_path/'company.json'))
    monkeypatch.setattr(Config,'LONG_RESEARCH_PATH',str(tmp_path/'long.json'))
    monkeypatch.setattr(Config,'WEB_READONLY',False)
    client=server.app.test_client();host='127.0.0.1:'+str(Config.WEB_PORT)
    headers={'Host':host,'X-TA-Token':server.SESSION_TOKEN,'Origin':'http://'+host}
    assert client.post('/api/company-research/run',json={'code':'005930'},headers={'Host':host}).status_code==403
    assert client.post('/api/company-research/run',json={'code':'005930'},headers={**headers,'Origin':'https://evil.invalid'}).status_code==403
    monkeypatch.setattr(Config,'WEB_READONLY',True)
    assert client.post('/api/company-research/run',json={'code':'005930'},headers=headers).status_code==403
    assert client.get('/api/company-research',headers={'Host':host}).status_code==200
    monkeypatch.setattr(Config,'WEB_READONLY',False)
    monkeypatch.setattr(server,'_has_real_api_key',lambda:True)
    seen=[]
    monkeypatch.setattr(cr,'run',lambda *args:seen.append(args[2]) or {'status':'held'})
    assert client.post('/api/company-research/run',json={'code':'005930'},headers=headers).status_code==200
    assert seen==['005930']
    assert client.post('/api/company-research/run',json={'code':'https://evil.invalid'},headers=headers).status_code==400

def test_public_search_has_no_execution_route_and_private_files_excluded():
    from web.export_site import _JS,_BODY
    from security.scan import _excluded
    from pathlib import Path
    assert '/api/company-research/run' not in _JS
    assert _BODY.index('data-tab="company"')<_BODY.index('data-tab="records"')
    assert _excluded(Path('company_research.json'))


def test_public_company_list_uses_current_prediction_codes_without_running_analysis(tmp_path):
    company=tmp_path/'company.json';long=tmp_path/'long.json';outcome=tmp_path/'outcome.json'
    company.write_text(json.dumps({'reports':[]}),encoding='utf-8')
    long.write_text('{}',encoding='utf-8')
    outcome.write_text(json.dumps({'mode':'real','outcome':{'target_date':'2026-10-02',
        'news_universe':[{'code':'000660','name':'SK하이닉스'},
                         {'code':'005930','name':'삼성전자'},
                         {'code':'999999','name':'목록 외 종목'}]}}),encoding='utf-8')
    listing=cr.read_public(company,long,outcome)
    assert listing['featured_codes']==['000660','005930']
    assert listing['featured_date']=='2026-10-02'
    assert listing['reports']==[]

def test_success_uses_three_calls_and_publishes_no_allocations_or_drafts(tmp_path,monkeypatch):
    from data import long_research as data
    now=datetime(2026,9,29,10,tzinfo=KST)
    periods=[{'date':d,'revenue':100,'income':10,'fcf':8,'eps':1} for d in ['2026-06-30','2026-03-31','2025-12-31','2025-09-30','2025-06-30']]
    f=data.normalize_fundamentals('005930',periods,{'date':'2026-06-30','debt':30,'equity':100,'shares':5},40,now)
    monkeypatch.setattr(data,'collect_fundamentals',lambda *a,**kw:[f])
    monkeypatch.setattr(data,'collect_news',lambda *a:{'status':'ok','checked_at':now.isoformat(),'events':[]})
    class Model:
        calls=0
        def complete(self,*args,**kwargs):
            self.calls+=1
            reply={'stocks':[{'code':'005930','stance':'watch','points':['현금흐름 검토 [M1]'],'invalidation':['수익성 악화 시 재검토 [M1]']}]}
            if self.calls==3:reply={'allocations':[],'horizon_days':180,'summary':['추가 관찰 필요 [M1]']}
            return json.dumps(reply,ensure_ascii=False)
    model=Model();p=tmp_path/'company.json'
    item=cr.run(p,tmp_path/'long.json','005930',now,lambda:model)
    assert item['status']=='reviewed' and model.calls==3
    assert item['sources'][0]['id']=='M1'
    assert 'allocations' not in item and '_draft_texts' not in p.read_text(encoding='utf-8')
