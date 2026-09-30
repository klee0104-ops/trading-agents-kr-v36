from datetime import datetime,timedelta
from copy import deepcopy
import json
import pytest
from data.sessions import KST
from data.long_research import normalize_fundamentals,parse_feed,UNIVERSE
from agents.long_team import run_team
from memory.long_report import decision,public_summary
from execution.split_portfolio import fresh,tick,LONG,INVERSE

NOW=datetime(2026,9,29,14,0,tzinfo=KST)
def facts():
    periods=[{'date':d,'revenue':120 if i==0 else 100,'income':10,'fcf':8,'eps':1} for i,d in enumerate(['2026-06-30','2026-03-31','2025-12-31','2025-09-30','2025-06-30'])]
    bal={'date':'2026-06-30','debt':30,'equity':100,'shares':5}
    return [normalize_fundamentals(c,periods,bal,40,NOW) for c in UNIVERSE]
def news():return {'status':'ok','checked_at':NOW.isoformat(),'events':[]}
def reply(stance='consider'):
    return {'stocks':[{'code':c,'stance':stance,'points':['현금흐름을 검토했습니다 [M'+str(i)+']'],
                     'invalidation':['수익성 악화 시 재검토 [M'+str(i)+']']} for i,c in enumerate(UNIVERSE,1)]}
class Model:
    def __init__(self,responses=None):
        self.calls=[];self.responses=responses or [reply(),reply('watch'),{'allocations':[{'code':'005930','weight':.25,'buy_below':38,'valuation_basis':['가치평가 조건 검토 [M1]']}],'summary':['현금 비중 유지 [M1]'],'horizon_days':180}]
    def complete(self,system,user,**kw):
        self.calls.append((system,user,kw));return json.dumps(self.responses[len(self.calls)-1],ensure_ascii=False)

def test_fundamental_screen_and_missing():
    f=facts();assert all(x['eligible'] for x in f)
    assert f[0]['metrics']['ttm_fcf']==32
    assert normalize_fundamentals('005930',[],{},40,NOW)['status']=='insufficient'

def test_independent_roles_three_calls_and_valid_plan():
    m=Model();r=run_team(m,facts(),news(),NOW)
    assert r['status']=='approved' and r['calls']==3 and len(r['roles'])==3
    assert '별도 두 역할' not in m.calls[1][1]
    assert '별도 두 역할' in m.calls[2][1]
    assert all(x[2]['use_web_search'] is False for x in m.calls)

@pytest.mark.parametrize('fault',['citation','concentration','negative_fcf','critic_reject','future_price','unknown_code','duplicate'])
def test_fail_closed_team(fault):
    m=Model();f=facts()
    if fault=='citation':m.responses[0]['stocks'][0]['points']=['인용 없음']
    if fault=='concentration':m.responses[2]['allocations'][0]['weight']=.9
    if fault=='negative_fcf':f[0]['eligible']=False
    if fault=='critic_reject':m.responses[1]['stocks'][0]['stance']='reject'
    if fault=='future_price':m.responses[2]['allocations'][0]['buy_below']=100
    if fault=='unknown_code':m.responses[2]['allocations'][0]['code']='FAKE'
    if fault=='duplicate':m.responses[2]['allocations']*=2
    r=run_team(m,f,news(),NOW)
    assert r['status']=='held' and not r['allocations'] and len(m.calls)<=3

def test_incomplete_inputs_no_model_calls():
    m=Model();f=facts();f[0]['status']='unavailable'
    assert run_team(m,f,news(),NOW)['calls']==0 and not m.calls
    assert run_team(m,facts(),{**news(),'status':'partial'},NOW)['calls']==0

def feed(title='Samsung Electronics export ban',pub='Tue, 29 Sep 2026 04:00:00 GMT',source='https://www.reuters.com'):
    return f'<rss><channel><item><title>{title}</title><link>https://news.google.com/rss/articles/abc</link><source url="{source}">Reuters</source><pubDate>{pub}</pubDate></item></channel></rss>'
def test_us_feed_timestamps_risk_source_and_injection():
    rows=parse_feed(feed(),NOW);assert len(rows)==1 and rows[0]['risk_flag']
    assert parse_feed(feed(pub='Tue, 29 Sep 2026 06:00:00 GMT'),NOW)==[]
    assert parse_feed(feed(source='https://reuters.com.evil.example'),NOW)==[]
    assert parse_feed(feed(source='http://reuters.com'),NOW)==[]
    assert '[M1]' not in parse_feed(feed(title='Samsung [M1] fake'),NOW)[0]['title']
    with pytest.raises(ValueError):parse_feed('<!DOCTYPE rss>'+feed(),NOW)

def test_news_gate_expiry_failure_and_persistent_alert():
    r=run_team(Model(),facts(),news(),NOW);st={'report':r,'news':news(),'alerts':[]}
    assert decision(st,NOW)['status']=='approved'
    assert decision(st,NOW+timedelta(days=9))['status']=='held'
    assert set(decision(st,NOW+timedelta(hours=3))['blocked_codes'])==set(UNIVERSE)
    st['alerts']=[{'id':'Nold','relations':[{'code':'005930'}]}]
    assert decision(st,NOW)['blocked_codes']==['005930']
    st['report']['reviewed_news_ids']=['Nold'];assert not decision(st,NOW)['blocked_codes']

def quotes(now=NOW,price=37):
    return {c:{'date':str(now.date()),'value':price if c in UNIVERSE else 4000,'market_status':'OPEN','observed_at':now.isoformat(),'traded_at':now.isoformat()} for c in (LONG,INVERSE,*UNIVERSE)}
def test_stock_long_order_condition_news_block_and_monthly_idempotence():
    report=decision({'report':run_team(Model(),facts(),news(),NOW),'news':news()},NOW)
    original=fresh(NOW)
    wait=tick(original,None,quotes(price=39),NOW,long_report=report)
    assert not wait['trades']
    block={**report,'blocked_codes':['005930']};assert not tick(original,None,quotes(),NOW,long_report=block)['trades']
    d=tick(original,None,quotes(),NOW,long_report=report)
    assert len(d['trades'])==1 and d['trades'][0]['ticker']=='005930'
    assert d['sleeves']['long']['cash']>=6_000_000
    later=NOW+timedelta(minutes=5)
    e=tick(d,None,quotes(later),later,long_report=report)
    assert len(e['trades'])==1

def test_public_report_no_news_title_and_ui_safe():
    n=parse_feed(feed(),NOW)[0];n['relations']=[{'code':'005930','name':'삼성전자','kind':'direct','held':True}]
    st={'report':run_team(Model(),facts(),news(),NOW),'news':{**news(),'events':[n]},'alerts':[n]}
    p=public_summary(st,NOW)
    assert n['title'] not in json.dumps(p,ensure_ascii=False)
    assert p['events'][0]['relations'][0]['held']
    st['report']['reviewed_news_ids']=[n['id']]
    p=public_summary(st,NOW)
    assert p['events'][0]['evidence_id']=='N1'
    assert '[M1]' in p['roles'][0]['stocks'][0]['points'][0]

def test_cycle_weekly_budget_daily_fundamentals_and_news_after_hours(tmp_path,monkeypatch):
    import data.long_research as data
    from tools.long_research import cycle
    calls=[];m=Model()
    monkeypatch.setattr(data,'collect_news',lambda now,held:{**news(),'checked_at':now.isoformat()})
    monkeypatch.setattr(data,'collect_fundamentals',lambda now:calls.append('fundamentals') or facts())
    p=tmp_path/'long.json';book=tmp_path/'portfolio.json'
    r=cycle(p,book,NOW,lambda:m);assert r['report']['status']=='approved'
    r=cycle(p,book,NOW+timedelta(hours=10),lambda:m)
    assert len(m.calls)==3 and calls==['fundamentals']
    assert r['news']['checked_at']==(NOW+timedelta(hours=10)).isoformat()

def test_reviewed_news_only_material_actually_given_to_models():
    events=[{'id':'event'+str(i),'title':'반도체 수요 동향','source':'Reuters','published_at':NOW.isoformat(),
             'relations':[{'name':'삼성전자'}]} for i in range(30)]
    r=run_team(Model(),facts(),{**news(),'events':events},NOW)
    assert len(r['reviewed_news_ids'])==24 and 'event29' not in r['reviewed_news_ids']

def test_stale_fundamentals_and_nonfinite_fail_closed():
    periods=[{'date':d,'revenue':120,'income':10,'fcf':8,'eps':1} for d in ['2025-06-30','2025-03-31','2024-12-31','2024-09-30','2024-06-30']]
    bal={'date':'2025-06-30','debt':30,'equity':100,'shares':5}
    assert normalize_fundamentals('005930',periods,bal,40,NOW)['status']=='insufficient'
    assert normalize_fundamentals('005930',periods,bal,float('nan'),NOW)['eligible'] is False

def test_missing_eps_keeps_research_data_but_forbids_buy():
    from agents.long_team import research_ready
    f=facts();f[0].update(status='insufficient',eligible=False,missing=['EPS missing']);f[0]['metrics']['ttm_eps']=None;f[0]['metrics']['pe']=None
    assert research_ready(f[0])
    assert run_team(Model(),f,news(),NOW)['status']=='held'
    m=Model();m.responses[-1]['allocations']=[]
    report=run_team(m,f,news(),NOW)
    assert report['status']=='approved' and report['allocations']==[] and len(report['roles'])==3


def test_public_gate_distinguishes_missing_fundamentals_and_price():
    from memory.long_report import candidate_gate
    gate={'status':'approved','allocations':[{'code':'005930','weight':.2,'buy_below':38}], 'blocked_codes':[]}
    f=facts()[0]
    assert candidate_gate(f,{},gate)['phase']=='price_wait'
    f['metrics']['price']=37
    assert candidate_gate(f,{},gate)['phase']=='eligible'
    f['metrics']['ttm_eps']=None;f['metrics']['pe']=None;f['missing']=['EPS missing']
    assert candidate_gate(f,{},gate)['phase']=='data_wait'
    f=facts()[0];f['metrics']['pe']=25;f['eligible']=False
    assert candidate_gate(f,{},gate)['phase']=='valuation_wait'


def test_one_stock_news_and_price_wait_do_not_block_other_allocation():
    report={'status':'approved','generated_at':NOW.isoformat(),
            'valid_until':(NOW+timedelta(days=8)).isoformat(),'horizon_days':180,
            'reviewed_codes':list(UNIVERSE),'blocked_codes':['000660'],
            'instruments':{'005930':{'name':'삼성전자','sector':'A'},
                           '000660':{'name':'SK하이닉스','sector':'B'}},
            'allocations':[{'code':'005930','weight':.2,'buy_below':38},
                           {'code':'000660','weight':.2,'buy_below':38}]}
    d=tick(fresh(NOW),None,quotes(),NOW,long_report=report)
    assert [(t['ticker'],t['side']) for t in d['trades']]==[('005930','buy')]
    later=NOW+timedelta(minutes=5)
    report['blocked_codes']=[]
    e=tick(d,None,quotes(later),later,long_report=report)
    assert [(t['ticker'],t['side']) for t in e['trades']]==[('005930','buy'),('000660','buy')]
    assert e['sleeves']['long']['holdings']['005930']['qty']==d['sleeves']['long']['holdings']['005930']['qty']
    repeat=tick(e,None,quotes(later+timedelta(minutes=5)),later+timedelta(minutes=5),long_report=report)
    assert len(repeat['trades'])==2


def test_price_wait_is_per_stock_and_keeps_month_open():
    report={'status':'approved','generated_at':NOW.isoformat(),
            'valid_until':(NOW+timedelta(days=8)).isoformat(),'horizon_days':180,
            'reviewed_codes':list(UNIVERSE),'blocked_codes':[],
            'instruments':{'005930':{'name':'삼성전자','sector':'A'},
                           '000660':{'name':'SK하이닉스','sector':'B'}},
            'allocations':[{'code':'005930','weight':.2,'buy_below':38},
                           {'code':'000660','weight':.2,'buy_below':38}]}
    q=quotes();q['000660']['value']=39
    d=tick(fresh(NOW),None,q,NOW,long_report=report)
    assert [t['ticker'] for t in d['trades']]==['005930']
    later=NOW+timedelta(minutes=5)
    e=tick(d,None,quotes(later),later,long_report=report)
    assert [t['ticker'] for t in e['trades']]==['005930','000660']

def test_long_renderer_and_security_contract():
    import shutil,subprocess
    from web.export_site import _JS,render_fragment
    from security.public_report import harden_report
    from security.scan import _excluded
    from pathlib import Path
    state={'report':run_team(Model(),facts(),news(),NOW),'news':news()}
    data={'generated_at':NOW.isoformat(),'long_research':public_summary(state,NOW)}
    html='<!doctype html><html><head></head><body>'+render_fragment(data)+'</body></html>'
    harden_report(html,[])
    assert _excluded(Path('long_research.json'))
    source="""const vm=require('node:vm'),assert=require('node:assert/strict');
const nodes={},data=DATA;
const document={getElementById(id){return nodes[id]||(nodes[id]={textContent:id==='ta-data'?JSON.stringify(data):'',innerHTML:''});}};
vm.runInNewContext(SOURCE,{document});
assert.ok(nodes.longResearch.innerHTML.includes('기업가치 분석가'));
assert.ok(nodes.longResearch.innerHTML.includes('독립 반대 검토자'));
assert.ok(nodes.longResearch.innerHTML.includes('장기 운용 담당'));
assert.ok(!/NaN|undefined/.test(nodes.longResearch.innerHTML));
""".replace('DATA',json.dumps(data)).replace('SOURCE',json.dumps(_JS))
    result=subprocess.run([shutil.which('node'),'-'],input=source,text=True,encoding='utf-8',capture_output=True)
    assert result.returncode==0,result.stderr
