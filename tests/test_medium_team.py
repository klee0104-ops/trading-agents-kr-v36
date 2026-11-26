from datetime import datetime,timedelta
import json

from data.sessions import KST
from agents.medium_team import eligible,run_team
from memory.medium_report import candidate_facts,decision,public_summary
from execution.split_portfolio import fresh,enable_medium,tick,summary_at,LONG,INVERSE

NOW=datetime(2026,9,29,14,0,tzinfo=KST)
CODE='005930'


def fact():
    return {'code':CODE,'name':'삼성전자','status':'ok','period_end':'2026-06-30',
            'metrics':{'price':40000,'ttm_income':100,'ttm_fcf':80,'ttm_eps':1200,
                       'revenue_yoy_pct':-5,'debt_equity':.8,'pe':20,'pb':2},
            'dart_verification':{'status':'verified','period_end':'2026-06-30'}}


class Model:
    calls=0
    def complete(self,*args,**kwargs):
        self.calls+=1
        return json.dumps({'action':'buy','code':CODE,'weight':.2,'buy_below':40000,
                           'horizon_days':60,'thesis':['현금창출력 [M1]'],
                           'risk':['실적 감소 가능성 [M1]']},ensure_ascii=False)


def report():
    model=Model();r=run_team(model,[fact()],{'status':'ok','events':[]},NOW)
    assert model.calls==1 and r['calls']==1 and r['status']=='approved'
    return r


def state():
    return {'medium_report':report(),'fundamentals':[fact()],
            'catalog':{'status':'ok','checked_at':NOW.isoformat(),
                       'instruments':{CODE:{'name':'삼성전자','sector':'Technology'}}},
            'news':{'status':'ok','checked_at':NOW.isoformat(),'events':[]},'alerts':[],
            'dart_financial_required':True,
            # (V3.39) 두 번째 의견: 장기팀 기업가치 분석가가 같은 종목을 'consider'
            'report':{'roles':[{'name':'기업가치 분석가','stocks':[{'code':CODE,'stance':'consider'}]}]}}


def quote(now,price=40000):
    return {c:{'date':str(now.date()),'value':v,'market_status':'OPEN',
               'observed_at':now.isoformat(),'traded_at':now.isoformat()}
            for c,v in ((CODE,price),(LONG,40000),(INVERSE,4000))}


def test_medium_evidence_gate_and_public_allowlist():
    s=state();p=decision(s,NOW)
    assert p['status']=='approved' and p['selected_code']==CODE
    assert p['weight']==.2 and p['execution_weight']==.5
    assert public_summary(s,NOW)['execution_weight']==.5
    s['news']['checked_at']=(NOW-timedelta(hours=3)).isoformat()
    assert decision(s,NOW)['reason_code']=='news_stale'
    s['news']['checked_at']=NOW.isoformat()
    s['fundamentals'][0]['dart_verification']['status']='mismatch'
    assert decision(s,NOW)['reason_code']=='dart_unverified'
    s['medium_report']['raw_secret']='API_KEY_SHOULD_NOT_EXPORT'
    assert 'API_KEY_SHOULD_NOT_EXPORT' not in json.dumps(public_summary(s,NOW))
    bad=fact();bad['metrics']['ttm_fcf']=-1
    assert not eligible(bad)
    high=fact();high['metrics']['price']=1_773_000
    assert not eligible(high)  # 200만원 계좌의 50%로 정수 1주도 매수할 수 없음


def test_existing_one_share_gets_one_approved_top_up_without_rewriting_cost():
    s=state();s['medium_report']['weight']=.15
    s['medium_report']['buy_below']=272000
    s['fundamentals'][0]['metrics']['price']=271000
    p=decision(s,NOW)
    assert p['status']=='approved' and p['weight']==.15 and p['execution_weight']==.5
    ledger=fresh(NOW,with_medium=True)
    book=ledger['sleeves']['medium']
    original_cost=271176.17
    book['holdings']={CODE:{'qty':1,'cost':original_cost}}
    book['cash']=2_000_000-original_cost
    book['equity']=book['cash']+271000
    book['fees']=40.67
    book['medium_position']={'code':CODE,'entry_at':NOW.isoformat(),
                             'horizon_days':60,'plan_at':p['generated_at']}
    book['last_medium_plan_at']=p['generated_at']
    later=NOW+timedelta(minutes=5)
    first=tick(ledger,None,quote(later,price=271000),later,
               long_report={'status':'held','allocations':[]},medium_report=p)
    buys=[t for t in first['trades'] if t['sleeve']=='medium' and t['side']=='buy']
    assert len(buys)==1 and buys[0]['qty']==2
    assert first['sleeves']['medium']['holdings'][CODE]['qty']==3
    assert first['sleeves']['medium']['holdings'][CODE]['cost']>original_cost
    assert first['sleeves']['medium']['medium_position']['sizing_policy']=='medium_50pct_v2'
    assert not [t for t in first['trades'] if t['sleeve']=='medium' and t['side']=='sell']
    assert summary_at(first)['valuation_audit']['status']=='ok'
    twice=tick(first,None,quote(later+timedelta(minutes=5),price=271000),
               later+timedelta(minutes=5),long_report={'status':'held','allocations':[]},medium_report=p)
    assert len([t for t in twice['trades'] if t['sleeve']=='medium' and t['side']=='buy'])==1


def test_medium_top_up_waits_for_price_and_fresh_approval():
    s=state();s['medium_report']['buy_below']=40000
    p=decision(s,NOW)
    ledger=fresh(NOW,with_medium=True);book=ledger['sleeves']['medium']
    book['holdings']={CODE:{'qty':1,'cost':40026}}
    book['cash']=2_000_000-40026
    book['equity']=book['cash']+40000
    book['medium_position']={'code':CODE,'entry_at':NOW.isoformat(),
                             'horizon_days':60,'plan_at':p['generated_at']}
    waiting=tick(ledger,None,quote(NOW+timedelta(minutes=5),price=40001),
                 NOW+timedelta(minutes=5),long_report={'status':'held','allocations':[]},medium_report=p)
    assert not [t for t in waiting['trades'] if t['sleeve']=='medium']
    assert waiting['sleeves']['medium']['medium_position'].get('sizing_policy') is None
    stale=dict(p,valid_until=(NOW-timedelta(seconds=1)).isoformat())
    next_time=NOW+timedelta(minutes=10)
    rejected=tick(waiting,None,quote(next_time),next_time,
                  long_report={'status':'held','allocations':[]},medium_report=stale)
    assert not [t for t in rejected['trades'] if t['sleeve']=='medium']


def test_recent_research_batch_is_reused_but_stale_batch_is_not():
    s=state()
    s['fundamentals']=[]
    s['report']={'generated_at':NOW.isoformat(),'fundamentals':[fact()],'roles':[{'stocks':[{'code':CODE,'stance':'consider'}]}]}
    assert [f['code'] for f in candidate_facts(s,NOW)]==[CODE]
    assert decision(s,NOW)['status']=='approved'
    s['report']['generated_at']=(NOW-timedelta(days=3)).isoformat()
    assert candidate_facts(s,NOW)==[]
    assert decision(s,NOW)['reason_code']=='dart_unverified'


def test_missing_medium_quote_does_not_block_short_team():
    p=decision(state(),NOW)
    s=fresh(NOW,with_medium=True)
    envelope={'mode':'real','saved_at':NOW.isoformat(),
              'outcome':{'target_date':str(NOW.date()),'final_qa':{'consistent':True},
                         'final_result':{'kospi_prediction':{'position':-.2}}}}
    q=quote(NOW);q.pop(CODE)
    result=tick(s,envelope,q,NOW,long_report={'status':'held','allocations':[]},medium_report=p)
    assert any(t['sleeve']=='short' and t['side']=='buy' for t in result['trades'])
    assert all(t['sleeve']!='medium' for t in result['trades'])


def test_cash_transfer_is_idempotent_and_preserves_pnl():
    original=fresh(NOW)
    original['sleeves']['short']['cash']+=5000
    original['sleeves']['short']['equity']+=5000
    original['sleeves']['short']['realized_pnl']=5000
    migrated=enable_medium(original,NOW)
    assert original['sleeves']['short']['initial_cash']==12_000_000
    assert [migrated['sleeves'][x]['initial_cash'] for x in ('short','medium','long')]==[10_000_000,2_000_000,8_000_000]
    assert sum(b['equity'] for b in migrated['sleeves'].values())==20_005_000
    assert migrated['sleeves']['short']['realized_pnl']==5000
    assert enable_medium(migrated,NOW)==migrated
    original['sleeves']['short']['holdings'][LONG]={'qty':1,'cost':40000}
    assert enable_medium(original,NOW)==original


def test_medium_buy_profit_exit_and_same_plan_no_reentry():
    s=fresh(NOW,with_medium=True);p=decision(state(),NOW)
    entered=tick(s,None,quote(NOW),NOW,long_report={'status':'held','allocations':[]},medium_report=p)
    buys=[x for x in entered['trades'] if x['sleeve']=='medium' and x['side']=='buy']
    assert len(buys)==1 and buys[0]['qty']>0
    assert entered['sleeves']['short']['initial_cash']==10_000_000
    later=NOW+timedelta(minutes=5)
    exited=tick(entered,None,quote(later,price=50000),later,
                long_report={'status':'held','allocations':[]},medium_report=p)
    assert exited['trades'][-1]['side']=='sell' and exited['trades'][-1]['realized_pnl']>0
    assert summary_at(exited)['valuation_audit']['status']=='ok'
    later+=timedelta(minutes=5)
    assert len(tick(exited,None,quote(later),later,long_report={'status':'held','allocations':[]},medium_report=p)['trades'])==2


def test_medium_loss_requires_analysis_after_stop_before_reentry():
    p=decision(state(),NOW)
    ledger=fresh(NOW,with_medium=True);book=ledger['sleeves']['medium']
    book['holdings']={CODE:{'qty':1,'cost':40026}}
    book['cash']-=40026
    book['medium_position']={'code':CODE,'entry_at':(NOW-timedelta(days=1)).isoformat(),
                             'horizon_days':60,'plan_at':(NOW-timedelta(days=1)).isoformat()}
    book['last_medium_plan_at']=(NOW-timedelta(days=1)).isoformat()
    stopped=NOW+timedelta(minutes=5)
    exited=tick(ledger,None,quote(stopped,price=34000),stopped,
                long_report={'status':'held','allocations':[]},medium_report=p)
    assert exited['trades'][-1]['side']=='sell'
    assert exited['trades'][-1]['reason']=='중기 개별 손실 12% 제한'
    checked=tick(exited,None,quote(stopped+timedelta(minutes=5),price=34000),
                 stopped+timedelta(minutes=5),long_report={'status':'held','allocations':[]},medium_report=p)
    assert len(checked['trades'])==1
    assert checked['sleeves']['medium']['status']=='중기 손절 이후 새 분석 계획 대기 · 현금 유지'
    next_day=NOW+timedelta(days=1)
    fresh_plan=dict(p,generated_at=(next_day-timedelta(minutes=10)).isoformat(),
                    valid_until=(next_day+timedelta(days=1)).isoformat())
    reopened=tick(checked,None,quote(next_day,price=34000),next_day,
                  long_report={'status':'held','allocations':[]},medium_report=fresh_plan)
    assert len(reopened['trades'])==2
    assert reopened['trades'][-1]['side']=='buy'


def test_medium_new_entry_needs_todays_plan_but_existing_holding_remains():
    p=decision(state(),NOW)
    next_day=NOW+timedelta(days=1)
    empty=fresh(NOW,with_medium=True)
    waiting=tick(empty,None,quote(next_day),next_day,
                 long_report={'status':'held','allocations':[]},medium_report=p)
    assert not [t for t in waiting['trades'] if t['sleeve']=='medium']
    assert waiting['sleeves']['medium']['status']=='중기 당일 새 분석 계획 없음 · 현금 유지'
    entered=tick(empty,None,quote(NOW),NOW,
                 long_report={'status':'held','allocations':[]},medium_report=p)
    held=tick(entered,None,quote(next_day),next_day,
              long_report={'status':'held','allocations':[]},medium_report=p)
    assert held['sleeves']['medium']['holdings']==entered['sleeves']['medium']['holdings']
    assert not [t for t in held['trades'] if t['side']=='sell' and t['sleeve']=='medium']


def test_hourly_cycle_calls_one_medium_analyst_per_day(tmp_path,monkeypatch):
    from tools.long_research import cycle
    import data.long_research as feed
    import memory.research_reports as archive
    p=tmp_path/'long_research.json';book=tmp_path/'paper_portfolio.json'
    p.write_text(json.dumps({'fundamentals':[fact()],'fundamentals_checked_day':str(NOW.date()),
        'last_model_review_at':NOW.isoformat(),'report':{'allocations':[{'weight':.1}]}}),encoding='utf-8')
    book.write_text(json.dumps(fresh(NOW,with_medium=True)),encoding='utf-8')
    monkeypatch.setattr(feed,'collect_news',lambda *args,**kwargs:{'status':'ok','checked_at':NOW.isoformat(),'events':[]})
    monkeypatch.setattr(archive,'archive_long',lambda *args,**kwargs:None)
    models=[]
    def make():
        m=Model();models.append(m);return m
    first=cycle(p,book,NOW,make)
    second=cycle(p,book,NOW+timedelta(hours=1),make)
    assert len(models)==1 and models[0].calls==1
    assert first['medium_report']['status']=='approved'
    assert second['last_medium_attempt_day']==str(NOW.date())


def test_medium_requires_second_opinion_and_blocks_conflict():
    from memory.medium_report import second_opinion
    s=state();s['report']={'roles':[]}
    assert decision(s,NOW)['reason_code']=='second_opinion_missing'
    s['report']={'roles':[{'stocks':[{'code':CODE,'stance':'reject'}]}]}
    assert decision(s,NOW)['reason_code']=='second_opinion_conflict'
    assert second_opinion({'report':{}},CODE,'삼성전자',daily=lambda n:'up')=='agree'
    assert second_opinion({'report':{}},CODE,'삼성전자',daily=lambda n:'down')=='conflict'
    assert second_opinion({'report':{'roles':[{'stocks':[{'code':CODE,'stance':'consider'}]}]}},CODE,'삼성전자',daily=lambda n:'down')=='conflict'
