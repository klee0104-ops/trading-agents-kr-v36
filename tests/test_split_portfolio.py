from datetime import datetime, timedelta
import json
import pytest
from data.sessions import KST
from execution.split_portfolio import fresh,tick,summary,summary_at,LONG,INVERSE
from tools.paper_portfolio import initialize

NOW=datetime(2026,9,29,14,0,tzinfo=KST)
def signal(now=NOW,pos=-.11,plan=None):
    return {'mode':'real','saved_at':now.isoformat(),'outcome':{'target_date':str(now.date()),'final_qa':{'consistent':True},'risk_assessment':{'risk_level':'높음'},'final_result':{'kospi_prediction':{'position':pos},'long_term_plan':plan}}}
def quotes(now=NOW,long=40000,inv=4000):
    return {c:{'date':str(now.date()),'value':p,'market_status':'OPEN','observed_at':now.isoformat(),'traded_at':now.isoformat()} for c,p in ((LONG,long),(INVERSE,inv))}
def test_separate_and_inverse_once():
    s=fresh(NOW);d=tick(s,signal(),quotes(),NOW)
    assert s['trades']==[] and len(d['trades'])==1
    assert d['sleeves']['long']['cash']==8_000_000
    assert d['trades'][0]['ticker']==INVERSE and d['trades'][0]['side']=='buy'
    assert d['trades'][0]['at']==NOW.isoformat()
    assert d['sleeves']['short']['cash']>10_680_000
    later=NOW+timedelta(minutes=5)
    e=tick(d,signal(),quotes(later),later)
    assert len(e['trades'])==1
    sm=summary(e);assert sm['initial_cash']==20_000_000 and sm['equity']<20_000_000
    assert sm['realized_pnl']==0 and sm['unrealized_pnl']<0

def test_long_monthly_plan_only():
    plan={'equity_weight':.8,'horizon_days':90,'reason':'synthetic long view'}
    d=tick(fresh(NOW),signal(pos=0,plan=plan),quotes(),NOW)
    assert len(d['trades'])==1 and d['trades'][0]['sleeve']=='long' and d['trades'][0]['ticker']==LONG
    tomorrow=NOW+timedelta(days=1)
    e=tick(d,signal(tomorrow,pos=0,plan={**plan,'equity_weight':0}),quotes(tomorrow),tomorrow)
    assert len(e['trades'])==1

@pytest.mark.parametrize('field,value',[('mode','demo'),('saved_at','2026-09-26T00:00:00+09:00'),('saved_at','2026-09-30T00:00:00+09:00')])
def test_invalid_signal_cash(field,value):
    e=signal();e[field]=value
    assert tick(fresh(NOW),e,quotes(),NOW)['trades']==[]

def test_failed_qa_exits_and_realizes():
    d=tick(fresh(NOW),signal(),quotes(),NOW);later=NOW+timedelta(minutes=5)
    e=signal();e['outcome']['final_qa']['consistent']=False
    d=tick(d,e,quotes(later,inv=4100),later)
    assert len(d['trades'])==2 and d['trades'][-1]['side']=='sell'
    sm=summary(d);assert sm['realized_pnl']>0 and sm['unrealized_pnl']==0
    assert sm['pnl']==pytest.approx(sm['realized_pnl'],abs=.01)

@pytest.mark.parametrize('field,value',[('value',0),('value',float('nan')),('date','2026-09-28'),('observed_at','2026-09-29T13:58:00+09:00'),('traded_at','2026-09-29T13:00:00+09:00'),('traded_at',None)])
def test_bad_quotes_fail_closed(field,value):
    s=fresh(NOW);q=quotes();q[LONG][field]=value
    assert tick(s,signal(),q,NOW)==s

def test_after_close_no_backdated_orders():
    close=NOW.replace(hour=15,minute=30)
    d=tick(fresh(close),signal(close),quotes(close),close)
    assert not d['trades'] and d['last_tick']==close.isoformat()

def test_daily_stop_latched():
    d=tick(fresh(NOW),signal(pos=-1),quotes(),NOW)
    later=NOW+timedelta(minutes=5)
    d=tick(d,signal(),quotes(later,inv=3500),later)
    assert not d['sleeves']['short']['holdings'] and d['sleeves']['short']['stop_day']=='2026-09-29'
    later+=timedelta(minutes=5);e=tick(d,signal(pos=1),quotes(later),later)
    assert len(e['trades'])==2

def test_initialization_retires_not_adds_and_idempotent(tmp_path):
    old=tmp_path/'paper_account.json';old.write_text('{"initial_cash":10000000}',encoding='utf-8');new=tmp_path/'paper_portfolio.json'
    assert initialize(new,old,NOW)
    assert not old.exists() and list((tmp_path/'.review-backups').rglob('paper_account.json'))
    assert json.loads(new.read_text(encoding='utf-8'))['initial_cash']==20_000_000
    assert not initialize(new,old,NOW+timedelta(minutes=1))

def test_stock_bias_no_fabrication():
    from agents.trader import normalize_prediction
    d=normalize_prediction({'core_stocks':[{'directional_signal':'flat'}]})
    assert d['core_stocks'][0]['direction_bias']=='unknown'

def test_public_portfolio_whitelist():
    from web.export_site import _PAPER_FIELDS
    assert set(('portfolio_mode','sleeves','realized_pnl','unrealized_pnl','day_pnl','day_return_pct','valuation_audit'))<=set(_PAPER_FIELDS)


def test_profit_audit_reconciles_costs_and_today_return():
    d=tick(fresh(NOW),signal(),quotes(),NOW)
    sm=summary_at(d,NOW+timedelta(minutes=5))
    assert sm['valuation_audit']['status']=='ok'
    assert sm['pnl']==pytest.approx(sm['realized_pnl']+sm['unrealized_pnl'],abs=.01)
    assert sm['day_pnl']==pytest.approx(sm['pnl'],abs=.01)
    assert sm['day_return_pct']==pytest.approx(sm['total_return_pct'],abs=.0001)
    assert sm['fees']>0 and sm['unrealized_pnl']<0


def test_profit_audit_hides_stale_or_mismatched_returns():
    d=tick(fresh(NOW),signal(),quotes(),NOW)
    stale=summary_at(d,NOW+timedelta(minutes=11))
    assert stale['valuation_audit']['status']=='stale'
    assert stale['pnl'] is None and stale['day_pnl'] is None and stale['total_return_pct'] is None
    assert stale['realized_pnl']==0
    d['sleeves']['short']['equity']+=100
    bad=summary_at(d,NOW+timedelta(minutes=5))
    assert bad['valuation_audit']['status']=='mismatch'
    assert bad['pnl'] is None and bad['unrealized_pnl'] is None and bad['realized_pnl'] is None
    d=tick(fresh(NOW),signal(),quotes(),NOW)
    d['last_prices'][INVERSE]='invalid'
    bad_quote=summary_at(d,NOW+timedelta(minutes=5))
    assert bad_quote['valuation_audit']['status']=='mismatch'
    assert bad_quote['pnl'] is None


def test_today_and_cumulative_profit_have_separate_baselines():
    first=tick(fresh(NOW),signal(),quotes(),NOW)
    tomorrow=NOW+timedelta(days=1)
    second=tick(first,signal(tomorrow),quotes(tomorrow,inv=4050),tomorrow)
    sm=summary_at(second,tomorrow+timedelta(minutes=5))
    assert sm['valuation_audit']['status']=='ok'
    assert sm['day_pnl']==pytest.approx(sm['equity']-sum(b['equity'] for b in first['sleeves'].values()),abs=.01)
    assert sm['pnl']==pytest.approx(sm['equity']-20_000_000,abs=.01)
    assert sm['day_pnl']!=sm['pnl']


def test_provider_quote_parsing():
    from tools.paper_portfolio import parse_quote
    q=parse_quote({'localTradedAt':NOW.isoformat(),'closePrice':'40,000','marketStatus':'OPEN'},NOW)
    assert q['value']==40000 and q['date']=='2026-09-29'
    with pytest.raises(ValueError):
        parse_quote({'localTradedAt':'2026-09-29','closePrice':'40,000'},NOW)

def test_provider_closed_blocks_orders():
    q=quotes();q[LONG]['market_status']='CLOSED'
    assert tick(fresh(NOW),signal(),q,NOW)['trades']==[]


def test_split_renderer_and_private_ledger_exclusion():
    import shutil,subprocess
    from web.export_site import _JS
    from security.scan import _excluded
    from pathlib import Path
    for name in ('paper_portfolio.json','paper_portfolio.json.lock','paper_portfolio.pending'):
        assert _excluded(Path(name))
    source="""const vm=require('node:vm'),assert=require('node:assert/strict');
const nodes={},data=DATA;
const document={getElementById(id){return nodes[id]||(nodes[id]={textContent:id==='ta-data'?JSON.stringify(data):'',innerHTML:''});}};
vm.runInNewContext(SOURCE,{document});
assert.ok(nodes.paper.innerHTML.includes('20,000,000'));
assert.ok(nodes.paper.innerHTML.includes('8,000,000'));
assert.ok(nodes.paper.innerHTML.includes('12,000,000'));
assert.ok(!/NaN|undefined|null%/.test(nodes.paper.innerHTML));
""".replace('DATA',json.dumps({'generated_at':NOW.isoformat(),'paper':summary(fresh(NOW))})).replace('SOURCE',json.dumps(_JS))
    r=subprocess.run([shutil.which('node'),'-'],input=source,text=True,encoding='utf-8',capture_output=True)
    assert r.returncode==0,r.stderr


def test_shared_page_masks_expired_intraday_profit():
    import shutil,subprocess
    from web.export_site import _JS
    d=tick(fresh(NOW),signal(),quotes(),NOW)
    data={'generated_at':NOW.isoformat(),'paper':summary_at(d,NOW+timedelta(minutes=5))}
    assert data['paper']['valuation_audit']['status']=='ok'
    source="""const vm=require('node:vm'),assert=require('node:assert/strict');
const nodes={},data=DATA;
const document={getElementById(id){return nodes[id]||(nodes[id]={textContent:id==='ta-data'?JSON.stringify(data):'',innerHTML:''});}};
const fixed=Date.parse('2026-09-29T14:12:00+09:00');
class FakeDate extends Date {constructor(...args){super(...(args.length?args:[fixed]));}}
vm.runInNewContext(SOURCE,{document,Date:FakeDate,Intl});
assert.match(nodes.paper.innerHTML,/장중 시세 평가가 10분 이상 지연/);
assert.match(nodes.paper.innerHTML,/오늘 손익 <span class="pnl-neutral">미확인/);
assert.match(nodes.paper.innerHTML,/누적 손익 <span class="pnl-neutral">미확인/);
""".replace('DATA',json.dumps(data)).replace('SOURCE',json.dumps(_JS))
    r=subprocess.run([shutil.which('node'),'-'],input=source,text=True,encoding='utf-8',capture_output=True)
    assert r.returncode==0,r.stderr
