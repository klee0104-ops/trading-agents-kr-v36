from datetime import datetime,timedelta
from copy import deepcopy
import json
from data.sessions import KST
from data.etf_forecast import collect,validate
from execution.split_portfolio import fresh,tick,LONG,INVERSE
NOW=datetime(2026,9,30,9,5,tzinfo=KST)
def evidence():
 def fetch(code,kind):return [{'date':f'2026-09-{d:02}','close':100+d} for d in (21,22,23,24,25,28,29)]
 return collect(NOW,str(NOW.date()),fetch)
def raw():
 return {'forecasts':[{'code':c,'up_probability':p,'reasoning':['대상 자산 흐름을 검토 [M'+str(i)+']']} for i,c,p in ((901,'KPI200',40),(902,LONG,40),(903,INVERSE,65))]}
def plan():return validate(raw(),evidence(),NOW.replace(hour=8),str(NOW.date()))
def envelope(p=None):return {'mode':'real','saved_at':NOW.replace(hour=8).isoformat(),'outcome':{'target_date':str(NOW.date()),'final_qa':{'consistent':True},'final_result':{'kospi_prediction':{'position':.5},'etf_execution':plan() if p is None else p}}}
def quotes(now=NOW,inv=100):return {c:{'date':str(now.date()),'value':inv if c==INVERSE else 100,'market_status':'OPEN','observed_at':now.isoformat(),'traded_at':now.isoformat()} for c in (LONG,INVERSE,'KPI200')}
def state():
 d=fresh(NOW);d['short_policy']='etf_v2';return d

def test_independent_instrument_direction_and_same_window_net_score():
 s=tick(state(),envelope(),quotes(),NOW)
 assert s['trades'][0]['ticker']==INVERSE # Contrary to KOSPI position +.5.
 assert s['active_short_cycle']['entry_at']==NOW.isoformat()
 close=NOW.replace(hour=15,minute=20)
 d=tick(s,envelope(),quotes(close,inv=102),close)
 c=d['short_cycles'][0]
 assert not d['sleeves']['short']['holdings'] and c['net_pnl']>0 and c['costs']>0 and c['net_return_pct']<.5
 assert next(x for x in c['scores'] if x['code']==INVERSE)['correct'] is True
 assert c['entry_at']!=c['exit_at']
 assert len(tick(d,envelope(),quotes(close+timedelta(minutes=5)),close+timedelta(minutes=5))['trades'])==2

def test_prior_day_legacy_holding_closes_at_current_quote_without_rewriting_entry():
 d=state();b=d['sleeves']['short'];b['holdings']={INVERSE:{'qty':10,'cost':1000}};b['cash']-=1000
 d['trades']=[{'at':'2026-09-29T15:15:29+09:00','sleeve':'short','ticker':INVERSE,'side':'buy','qty':10}]
 old=deepcopy(d['trades'])
 out=tick(d,envelope({}),quotes(),NOW)
 assert out['trades'][:1]==old and len(out['trades'])==2
 assert out['trades'][-1]['at']==NOW.isoformat() and out['trades'][-1]['side']=='sell'
 assert not out['sleeves']['short']['holdings'] and out['sleeves']['short']['realized_pnl']<0
 assert out['legacy_trade_count']==2 and not out.get('short_cycles')

def test_same_day_untracked_holding_waits_until_planned_exit():
 d=state();b=d['sleeves']['short'];b['holdings']={INVERSE:{'qty':10,'cost':1000}};b['cash']-=1000
 d['trades']=[{'at':'2026-09-30T15:15:00+09:00','sleeve':'short','ticker':INVERSE,'side':'buy','qty':10}]
 before=NOW.replace(hour=15,minute=16)
 held=tick(d,envelope({}),quotes(before),before)
 assert len(held['trades'])==1 and INVERSE in held['sleeves']['short']['holdings']
 due=NOW.replace(hour=15,minute=20)
 sold=tick(held,envelope({}),quotes(due),due)
 assert sold['trades'][-1]['at']==due.isoformat() and sold['trades'][-1]['side']=='sell'
 assert not sold['sleeves']['short']['holdings']

def test_late_entry_and_missing_benchmark_blocked():
 late=NOW.replace(hour=14)
 assert tick(state(),envelope(),quotes(late),late)['trades']==[]
 q=quotes();q.pop('KPI200')
 assert tick(state(),envelope(),q,NOW)['trades']==[]

def test_late_exit_keeps_real_pnl_but_excludes_direction_grade():
 d=tick(state(),envelope(),quotes(),NOW)
 late=NOW.replace(hour=15,minute=29)
 d=tick(d,envelope(),quotes(late,103),late)
 c=d['short_cycles'][0]
 assert c['net_pnl']>0 and not c['horizon_matched'] and all(s['correct'] is None for s in c['scores'])

def test_contradictory_or_uncited_etf_plan_held():
 r=raw();r['forecasts'][1]['up_probability']=70
 assert validate(r,evidence(),NOW,'2026-09-30')['status']=='held'
 r=raw();r['forecasts'][2]['reasoning']=['KOSPI 방향 복사']
 assert validate(r,evidence(),NOW,'2026-09-30')['status']=='held'
 assert validate(raw(),{'status':'held'},NOW,'2026-09-30')['status']=='held'

def test_data_lookahead_stale_and_mismatch_rejected():
 def stale(c,k):return [{'date':f'2026-08-{d:02}','close':100} for d in range(10,20)]
 assert collect(NOW,'2026-09-30',stale)['status']=='held'
 r=evidence();assert r['status']=='ok' and all(s['date']<'2026-09-30' for s in r['sources'])

def test_risk_exit_not_counted_as_planned_horizon_hit():
 d=tick(state(),envelope(),quotes(),NOW)
 later=NOW+timedelta(minutes=5)
 d=tick(d,envelope(),quotes(later,70),later)
 assert not d['active_short_cycle'] and d['short_cycles'][0]['exit_reason']=='risk_exit'
 assert all(s['correct'] is None for s in d['short_cycles'][0]['scores'])


def test_etf_evidence_not_lost_in_large_public_report():
 from memory.research_reports import short_report
 e=envelope();e['outcome']['source_registry']=[{'id':'N'+str(i),'source':'synthetic','kind':'N'} for i in range(80)]+evidence()['sources']
 report=short_report(e)
 assert {'M901','M902','M903'}<={s['id'] for s in report['sources']}
 assert len(report['execution']['forecasts'])==3
