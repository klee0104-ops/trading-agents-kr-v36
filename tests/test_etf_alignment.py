from datetime import datetime,timedelta
from copy import deepcopy
import json
from data.sessions import KST
from data.etf_forecast import collect,validate,NEUTRAL_PROBE_WEIGHT
from execution.split_portfolio import fresh,tick,LONG,INVERSE
NOW=datetime(2026,9,30,9,5,tzinfo=KST)
def evidence():
 def fetch(code,kind):return [{'date':f'2026-09-{d:02}','close':100+d} for d in (21,22,23,24,25,28,29)]
 return collect(NOW,str(NOW.date()),fetch)
def neutral_evidence(index=101,long=101,inverse=99):
 final={'KPI200':index,LONG:long,INVERSE:inverse}
 def fetch(code,kind):
  return [{'date':f'2026-09-{d:02}','close':final[code] if d==29 else 100} for d in (21,22,23,24,25,28,29)]
 return collect(NOW,str(NOW.date()),fetch)
def neutral_raw():
 r=raw()
 for row in r['forecasts']:row['up_probability']=50
 return r
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
 assert not d['sleeves']['short']['holdings'] and c['net_pnl']>0 and c['costs']>0 and c['net_return_pct']<1
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
 conflict=validate(r,evidence(),NOW,'2026-09-30')
 assert conflict['status']=='held' and conflict['etf_qa']['reason_code']=='opposed_etfs_positive'
 r=raw();r['forecasts'][2]['reasoning']=['KOSPI 방향 복사']
 uncited=validate(r,evidence(),NOW,'2026-09-30')
 assert uncited['status']=='held' and uncited['etf_qa']['reason_code']=='citation_invalid'
 assert uncited['etf_qa']['instrument']==INVERSE
 missing=validate(raw(),{'status':'held','reason_code':'history_incomplete','instrument':LONG},NOW,'2026-09-30')
 assert missing['etf_qa']['reason_code']=='evidence_unavailable'
 assert missing['etf_qa']['evidence_reason_code']=='history_incomplete'


def test_etf_qa_records_precise_safe_failure_without_raw_provider_error():
 valid_evidence=evidence()
 absent=validate(None,valid_evidence,NOW,'2026-09-30')
 assert absent['etf_qa']['reason_code']=='model_plan_missing'
 assert absent['etf_qa']['source_count']==3
 too_few=validate({'forecasts':raw()['forecasts'][:2]},valid_evidence,NOW,'2026-09-30')
 assert too_few['etf_qa']['reason_code']=='forecast_count'
 invalid=raw();invalid['forecasts'][1]['up_probability']='55'
 assert validate(invalid,valid_evidence,NOW,'2026-09-30')['etf_qa']['reason_code']=='probability_invalid'
 def broken_fetch(code,kind):raise RuntimeError('private-provider-token')
 failed=collect(NOW,'2026-09-30',broken_fetch)
 assert failed['reason_code']=='source_fetch_failed'
 assert 'private-provider-token' not in json.dumps(failed)


def test_missing_etf_forecast_reuses_stock_moderator_call_and_keeps_qa():
 from agents.stock_moderator import run_stock_moderator_with_etf
 class Stub:
  def __init__(self, response):self.response=response;self.calls=[]
  def complete_json(self,system,user,**kwargs):
   self.calls.append((system,user,kwargs));return self.response
 initial={'core_stocks':[{'name':'삼성전자','directional_signal':'up','reason':'근거 확인'}], 'watch_stocks':[]}
 client=Stub({'etf_execution':raw(),'reviews':[{'name':'삼성전자','action':'flat','reason':'종목 근거 부족'}]})
 moderated,recovered=run_stock_moderator_with_etf(client,initial,'종목 사실','중립',evidence())
 assert len(client.calls)==1 and client.calls[0][2]['use_web_search'] is False
 assert moderated['core_stocks'][0]['directional_signal']=='flat'
 approved=validate(recovered,evidence(),NOW.replace(hour=8),str(NOW.date()))
 assert approved['etf_qa']['passed'] is True and approved['selected_code']==INVERSE
 missing=Stub({'reviews':[]})
 _,no_plan=run_stock_moderator_with_etf(missing,initial,'종목 사실','중립',evidence())
 assert no_plan is None  # No fabricated fallback or order on another omission.


def test_pipeline_recovers_missing_etf_plan_without_extra_model_call(monkeypatch):
 from agents.base import MockLLMClient
 from graph.pipeline import TradingAgentsPipeline
 import graph.pipeline as pipeline_module
 from memory.store import MemoryStore
 from tests.test_pipeline_quant import _HistProvider,_snap
 import data.etf_forecast as etf_forecast
 class RecoveryMock(MockLLMClient):
  def _route(self,system,user):
   if '단기 ETF 판단 누락 복구 담당' in system:
    return json.dumps({'etf_execution':raw(),'reviews':[]},ensure_ascii=False)
   return super()._route(system,user)
 monkeypatch.setenv('TA_NO_NEWS','1')
 monkeypatch.setattr(TradingAgentsPipeline,'_is_demo',lambda self:False)
 monkeypatch.setattr(TradingAgentsPipeline,'_news_universe',lambda self:[])
 monkeypatch.setattr(TradingAgentsPipeline,'_news_titles',lambda self,*args:[])
 monkeypatch.setattr(etf_forecast,'collect',lambda now,target:evidence())
 monkeypatch.setattr(pipeline_module,'find_similar_cases',lambda *args,**kwargs:[])
 llm=RecoveryMock()
 outcome=TradingAgentsPipeline(llm,MemoryStore(None),data_provider=_HistProvider(),debate_max_rounds=2).run(snapshot=_snap())
 plan=outcome['final_result']['etf_execution']
 assert plan['etf_qa']['passed'] is True and plan['selected_code']==INVERSE
 assert len(llm.call_log)<=17
 assert sum('종목 중재자' in call['system_head'] for call in llm.call_log)==1


def test_short_only_qa_allows_small_weak_edge_paper_entry_without_global_qa():
 r=raw()
 for row in r['forecasts']:
  row['up_probability']={'KPI200':55,LONG:55,INVERSE:48}[row['code']]
 small=validate(r,evidence(),NOW.replace(hour=8),str(NOW.date()))
 assert small['schema']==4 and small['etf_qa']['passed'] is True
 assert (small['selected_code'],small['weight'])==(LONG,.20)
 e=envelope(small)
 e['outcome']['final_qa']['consistent']=False
 e['outcome']['verification']={'verification_passed':False}
 s=tick(state(),e,quotes(),NOW)
 assert s['sleeves']['short']['holdings'][LONG]['qty']>0
 assert not s['sleeves']['long']['holdings']
 assert s['active_short_cycle']['selected_code']==LONG
 assert s['trades'][0]['sleeve']=='short'


def test_fifty_percent_and_opposed_etfs_stay_cash():
 r=neutral_raw()
 cash=validate(r,evidence(),NOW.replace(hour=8),str(NOW.date()))
 assert cash['status']=='approved' and cash['selected_code'] is None and cash['weight']==0
 empty=tick(state(),envelope(cash),quotes(),NOW)
 assert not empty['trades'] and not empty.get('active_short_cycle')
 assert '현금 유지' in empty['sleeves']['short']['status']
 for row in r['forecasts']:
  row['up_probability']={LONG:51,INVERSE:51,'KPI200':50}[row['code']]
 assert validate(r,evidence(),NOW.replace(hour=8),str(NOW.date()))['status']=='held'

def test_neutral_probe_requires_three_consistent_prior_trends_and_uses_five_percent():
 for index,long,inverse,selected in ((101,101,99,LONG),(99,99,101,INVERSE)):
  p=validate(neutral_raw(),neutral_evidence(index,long,inverse),NOW.replace(hour=8),str(NOW.date()))
  assert p['etf_qa']['passed'] is True
  assert (p['selected_code'],p['weight'],p['selection_basis'])==(selected,NEUTRAL_PROBE_WEIGHT,'neutral_probe')
  assert p['neutral_probe']['basis_date']=='2026-09-29'
  opened=tick(state(),envelope(p),quotes(),NOW)
  assert opened['trades'][0]['ticker']==selected
  assert opened['active_short_cycle']['selection_basis']=='neutral_probe'
  assert 500_000<opened['trades'][0]['qty']*100<=600_000
  close=NOW.replace(hour=15,minute=20)
  closed=tick(opened,envelope(p),quotes(close,102),close)
  assert closed['short_cycles'][0]['selection_basis']=='neutral_probe'
  assert all(score['correct'] is None for score in closed['short_cycles'][0]['scores'])
  from execution.etf_contract import public_summary
  assert public_summary(closed)['neutral_probe_closed_cycles']==1
  assert public_summary(closed)['neutral_probe_net_pnl']==closed['short_cycles'][0]['net_pnl']

def test_neutral_probe_stays_cash_without_trend_or_valid_evidence():
 for facts in (neutral_evidence(100.1,100.1,99.9),neutral_evidence(101,101,101),neutral_evidence(101,99,101)):
  p=validate(neutral_raw(),facts,NOW.replace(hour=8),str(NOW.date()))
  assert p['status']=='approved' and p['selected_code'] is None and p['weight']==0
  assert not tick(state(),envelope(p),quotes(),NOW)['trades']
 missing=validate(neutral_raw(),{'status':'held','reason_code':'history_incomplete'},NOW.replace(hour=8),str(NOW.date()))
 assert missing['status']=='held' and not tick(state(),envelope(missing),quotes(),NOW)['trades']

def test_neutral_probe_rejects_oversize_or_forged_plan_and_cannot_reenter():
 p=validate(neutral_raw(),neutral_evidence(),NOW.replace(hour=8),str(NOW.date()))
 for mutation in ('oversize','missing_marker','tampered_trend','changed_probability'):
  fake=deepcopy(p)
  if mutation=='oversize':fake['weight']=.20
  elif mutation=='missing_marker':fake.pop('selection_basis')
  elif mutation=='tampered_trend':fake['neutral_probe']['five_session_return_pct']['114800']=1
  else:fake['forecasts'][1]['up_probability']=51
  assert not tick(state(),envelope(fake),quotes(),NOW)['trades']
 opened=tick(state(),envelope(p),quotes(),NOW)
 at=NOW+timedelta(minutes=5)
 closed=tick(opened,envelope(p),quotes(at,inv=101.4),at) if p['selected_code']==INVERSE else tick(opened,envelope(p),{**quotes(at),LONG:{**quotes(at)[LONG],'value':101.4}},at)
 assert closed['trades'][-1]['side']=='sell'
 next_hour=NOW.replace(hour=10,minute=0)
 assert len(tick(closed,envelope(p),quotes(next_hour),next_hour)['trades'])==2


def test_new_short_plan_rejects_fake_qa_or_oversized_low_probability():
 r=raw()
 for row in r['forecasts']:
  row['up_probability']={LONG:55,INVERSE:45,'KPI200':55}[row['code']]
 p=validate(r,evidence(),NOW.replace(hour=8),str(NOW.date()))
 p['weight']=.25
 assert not tick(state(),envelope(p),quotes(),NOW)['trades']
 p['weight']=.20
 p['etf_qa']['passed']=False
 assert not tick(state(),envelope(p),quotes(),NOW)['trades']

def test_strong_signal_uses_40_percent_and_legacy_schema3_keeps_old_cap():
 p=plan()
 assert p['schema']==4 and p['weight']==.40
 out=tick(state(),envelope(p),quotes(),NOW)
 trade=out['trades'][0]
 assert trade['ticker']==INVERSE
 assert 4_700_000<trade['qty']*100<4_800_000
 oversized=deepcopy(p);oversized['weight']=.41
 assert not tick(state(),envelope(oversized),quotes(),NOW)['trades']
 legacy=deepcopy(p);legacy['schema']=3;legacy['weight']=.25
 assert tick(state(),envelope(legacy),quotes(),NOW)['trades']
 legacy['weight']=.40
 assert not tick(state(),envelope(legacy),quotes(),NOW)['trades']

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


def test_new_profit_target_realizes_net_gain_without_rewriting_legacy_cycles():
 opened=tick(state(),envelope(),quotes(),NOW)
 assert opened['active_short_cycle']['exit_policy']['id']=='net_profit_v1'
 at=NOW+timedelta(minutes=5)
 closed=tick(opened,envelope(),quotes(at,inv=101.4),at)
 assert closed['trades'][-1]['side']=='sell'
 assert closed['short_cycles'][0]['exit_reason']=='profit_target_exit'
 assert closed['short_cycles'][0]['exit_observed_net_pct']>=1.2
 assert closed['short_cycles'][0]['net_pnl']>0
 assert all(s['correct'] is None for s in closed['short_cycles'][0]['scores'])
 assert len(tick(closed,envelope(),quotes(at+timedelta(minutes=5)),at+timedelta(minutes=5))['trades'])==2


def test_trailing_exit_only_after_cost_adjusted_profit_and_giveback():
 opened=tick(state(),envelope(),quotes(),NOW)
 early=NOW+timedelta(minutes=5)
 below=tick(opened,envelope(),quotes(early,inv=100.4),early)
 assert len(below['trades'])==1
 high=NOW+timedelta(minutes=10)
 armed=tick(below,envelope(),quotes(high,inv=100.8),high)
 assert armed['active_short_cycle']['profit_peak_net_pct']>=.6
 assert len(armed['trades'])==1
 small=NOW+timedelta(minutes=15)
 held=tick(armed,envelope(),quotes(small,inv=100.6),small)
 assert len(held['trades'])==1
 lower=NOW+timedelta(minutes=20)
 closed=tick(held,envelope(),quotes(lower,inv=100.35),lower)
 assert closed['short_cycles'][0]['exit_reason']=='profit_trailing_exit'
 assert closed['short_cycles'][0]['net_pnl']>0
 assert closed['short_cycles'][0]['profit_peak_net_pct']-closed['short_cycles'][0]['exit_observed_net_pct']>=.35


def test_profit_exit_waits_for_next_hourly_review_before_reentry():
 opened=tick(state(),envelope(),quotes(),NOW)
 at=NOW+timedelta(minutes=5)
 closed=tick(opened,envelope(),quotes(at,inv=101.4),at)
 before=NOW.replace(minute=30)
 waiting=tick(closed,envelope(),quotes(before),before)
 assert len(waiting['trades'])==2
 next_hour=NOW.replace(hour=10,minute=0)
 q=quotes(next_hour);q['KPI200']['value']=99.5
 entered=tick(waiting,envelope(),q,next_hour)
 assert entered['trades'][-1]['side']=='buy'
 assert entered['active_short_cycle']['exit_policy']['id']=='net_profit_v1'
 assert len(entered['trades'])==3


def test_legacy_schema3_has_no_retroactive_profit_exit():
 old=deepcopy(plan());old['schema']=3;old['weight']=.25
 opened=tick(state(),envelope(old),quotes(),NOW)
 at=NOW+timedelta(minutes=5)
 held=tick(opened,envelope(old),quotes(at,inv=101.4),at)
 assert 'exit_policy' not in held['active_short_cycle']
 assert len(held['trades'])==1


def test_stale_etf_quote_cannot_trigger_profit_exit():
 opened=tick(state(),envelope(),quotes(),NOW)
 at=NOW+timedelta(minutes=5)
 stale=quotes(at,inv=101.4)
 stale[INVERSE]['observed_at']=NOW.isoformat()
 unchanged=tick(opened,envelope(),stale,at)
 assert len(unchanged['trades'])==1
 assert unchanged['active_short_cycle'].get('profit_peak_net_pct') is None


def test_etf_evidence_not_lost_in_large_public_report():
 from memory.research_reports import short_report
 e=envelope();e['outcome']['source_registry']=[{'id':'N'+str(i),'source':'synthetic','kind':'N'} for i in range(80)]+evidence()['sources']
 report=short_report(e)
 assert {'M901','M902','M903'}<={s['id'] for s in report['sources']}
 assert len(report['execution']['forecasts'])==3


def test_rejected_etf_qa_reason_is_saved_in_short_report_without_raw_model_text():
 from memory.research_reports import short_report
 e=envelope(validate(None,evidence(),NOW.replace(hour=8),str(NOW.date())))
 report=short_report(e)
 qa=report['execution']['qa']
 assert qa['passed'] is False and qa['reason_code']=='model_plan_missing'
 assert qa['source_count']==3 and '모델이 ETF 별도 판단을 출력하지 않음'==qa['reason']


def test_schema4_intraday_exit_realizes_pnl_then_reenters_on_new_hour_signal():
 r=raw()
 for row in r['forecasts']:
  row['up_probability']={'KPI200':55,LONG:55,INVERSE:48}[row['code']]
 e=envelope(validate(r,evidence(),NOW.replace(hour=8),str(NOW.date())))
 first=tick(state(),e,quotes(NOW),NOW)
 assert first['active_short_cycle']['entry_equity']==12_000_000
 at_930=NOW.replace(minute=30)
 first=tick(first,e,quotes(at_930),at_930)
 # (V3.39) 지수가 보유 방향과 반대로 0.3% 이상 + 손실일 때만 시간별 청산
 at_1000=NOW.replace(hour=10,minute=0)
 adverse=quotes(at_1000);adverse['KPI200']['value']=99.6;adverse[LONG]['value']=99.6
 exited=tick(first,e,adverse,at_1000)
 assert exited['trades'][-1]['side']=='sell'
 assert exited['trades'][-1]['realized_pnl']==round(exited['sleeves']['short']['realized_pnl'],4)
 assert exited['short_cycles'][0]['exit_reason']=='intraday_exit'
 assert exited['short_cycles'][0]['net_pnl']<0
 assert exited['short_cycles'][0]['scores'][0]['correct'] is None
 assert exited['short_intraday']['last_review']['verdict']=='현금'
 at_1030=NOW.replace(hour=10,minute=30)
 waiting=tick(exited,e,quotes(at_1030),at_1030)
 at_1100=NOW.replace(hour=11,minute=0)
 rising=quotes(at_1100);rising['KPI200']['value']=101
 reentered=tick(waiting,e,rising,at_1100)
 assert reentered['trades'][-1]['side']=='buy'
 assert reentered['active_short_cycle']['entry_at']==at_1100.isoformat()
 assert len([x for x in reentered['trades'] if x['side']=='buy'])==2
 # 흐름이 약해졌지만(보합) 역방향·손실이 아니면 보유 유지 — 예전엔 여기서 청산했다
 at_1200=NOW.replace(hour=12,minute=0)
 sideways=quotes(at_1200);sideways['KPI200']['value']=101;sideways[LONG]['value']=101
 held=tick(reentered,e,sideways,at_1200)
 assert held['trades'][-1]['side']=='buy' and held['active_short_cycle']
 assert '보유 유지' in held['sleeves']['short']['status']
 # 장중 진입은 하루 1회 — 이후 청산되더라도 추가 재진입 없음
 at_1300=NOW.replace(hour=13,minute=0)
 down=quotes(at_1300);down['KPI200']['value']=100.5;down[LONG]['value']=100.4
 closed=tick(held,e,down,at_1300)
 assert closed['trades'][-1]['side']=='sell'
 at_1400=NOW.replace(hour=14,minute=0)
 strong=quotes(at_1400);strong['KPI200']['value']=102
 again=tick(closed,e,strong,at_1400)
 assert len([x for x in again['trades'] if x['side']=='buy'])==2


def test_schema4_missing_intraday_benchmark_does_not_fabricate_exit():
 r=raw()
 for row in r['forecasts']:
  row['up_probability']={'KPI200':55,LONG:55,INVERSE:48}[row['code']]
 e=envelope(validate(r,evidence(),NOW.replace(hour=8),str(NOW.date())))
 held=tick(state(),e,quotes(NOW),NOW)
 later=NOW.replace(hour=10,minute=0)
 q=quotes(later);q.pop('KPI200')
 out=tick(held,e,q,later)
 assert out['active_short_cycle'] and len(out['trades'])==1
 assert out['short_intraday']['last_review'] is None
