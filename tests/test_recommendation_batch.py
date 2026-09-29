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
