from datetime import datetime
import json
from data.sessions import KST
from tools import research_recommendations as batch

def test_batch_has_independent_once_per_code_budget_and_keeps_outcome(tmp_path,monkeypatch):
    p=tmp_path/'company.json';o=tmp_path/'outcome.json';long=tmp_path/'long.json'
    o.write_text(json.dumps({'saved_at':'2026-09-30T07:30:00','outcome':{'final_result':{'core_stocks':[{'name':'삼성전자'},{'name':'SK하이닉스'}]}}}),encoding='utf-8')
    before=o.read_bytes();calls=[]
    monkeypatch.setattr(batch,'research_one',lambda u,c,*a:calls.append(c) or {'code':c,'status':'held','calls':0})
    args=(p,long,o,datetime(2026,9,30,8,tzinfo=KST),lambda:None)
    assert len(batch.run_batch(*args))==2
    assert batch.run_batch(*args)==[] and len(calls)==2
    assert o.read_bytes()==before and not long.exists()

def test_batch_preserves_older_report_history(tmp_path,monkeypatch):
    p=tmp_path/'company.json';o=tmp_path/'outcome.json';long=tmp_path/'long.json'
    older=[{'code':str(n).zfill(6),'generated_at':'2026-09-01T08:00:00'} for n in range(21)]
    p.write_text(json.dumps({'reports':older}),encoding='utf-8')
    o.write_text(json.dumps({'saved_at':'2026-09-30T07:30:00','outcome':{'final_result':{'core_stocks':[{'name':'삼성전자'}]}}}),encoding='utf-8')
    monkeypatch.setattr(batch,'research_one',lambda u,c,*a:{'code':c,'status':'reviewed','calls':0})
    batch.run_batch(p,long,o,datetime(2026,9,30,8,tzinfo=KST),lambda:None)
    reports=json.loads(p.read_text(encoding='utf-8'))['reports']
    assert len(reports)==22
    assert reports[:21]==older
    assert reports[-1]['code']=='005930'

def test_scheduled_batch_waits_for_today_without_model_calls(tmp_path):
    p=tmp_path/'company.json';o=tmp_path/'outcome.json';long=tmp_path/'long.json'
    o.write_text(json.dumps({'saved_at':'2026-09-29T07:30:00','outcome':{}}),encoding='utf-8')
    progress=[]
    def forbidden():raise AssertionError('no model client on stale outcome')
    result=batch.run_scheduled(p,long,o,datetime(2026,9,30,10,tzinfo=KST),forbidden,progress.append)
    assert result==[] and progress==[{'status':'waiting_for_today_analysis'}]
    assert not p.exists()
