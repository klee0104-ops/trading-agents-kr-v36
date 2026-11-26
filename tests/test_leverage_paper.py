"""Synthetic, offline ledger and quote checks for the 10m paper contribution."""
from datetime import datetime, timedelta

from data.sessions import KST
from execution.split_portfolio import LEVERAGE, enable_leverage_capital, enable_long_core, fresh, summary, tick

NOW=datetime(2026,10,8,9,0,tzinfo=KST)


def state():
    result=enable_leverage_capital(enable_long_core(fresh(NOW,with_medium=True),NOW),NOW)
    result['short_policy']='etf_v2'
    return result


def quote(now,index=400,normal=40000,inverse=4000,lever=30000):
    return {code:{'date':str(now.date()),'value':value,'observed_at':now.isoformat(),
                  'traded_at':now.isoformat(),'market_status':'OPEN'}
            for code,value in [('KPI200',index),('069500',normal),('114800',inverse),(LEVERAGE,lever)]}


def signal(now):
    at=now.replace(hour=9,minute=5)
    plan={'schema':4,'status':'approved','target_date':str(now.date()),
          'generated_at':at.isoformat(),'selected_code':'069500','weight':.4,
          'etf_qa':{'passed':True},'forecasts':[
              {'code':'KPI200','up_probability':64},
              {'code':'069500','up_probability':63},
              {'code':'114800','up_probability':37}]}
    return {'mode':'real','saved_at':at.isoformat(),'outcome':{'target_date':str(now.date()),
            'final_result':{'etf_execution':plan}}}


def test_deposit_is_new_capital_not_profit_and_idempotent():
    old=enable_long_core(fresh(NOW,with_medium=True),NOW)
    old['equity_history']=[{'date':'2026-10-07','equity':20_005_000},
                           {'date':'2026-10-08','equity':20_006_000}]
    old['sleeves']['short']['cash']+=6_000
    old['sleeves']['short']['equity']+=6_000
    old['sleeves']['short']['realized_pnl']+=6_000
    old['last_tick']=NOW.isoformat()
    old['last_prices']={}
    moved=enable_leverage_capital(old,NOW+timedelta(minutes=1))
    assert moved['initial_cash']==30_000_000
    assert moved['sleeves']['short_leverage']['cash']==10_000_000
    assert moved['capital_events']==[{'at':(NOW+timedelta(minutes=1)).isoformat(),
        'type':'virtual_deposit','to':'short_leverage','amount':10_000_000,
        'capital_before':20_000_000,'capital_after':30_000_000}]
    assert moved['equity_history'][-1]['equity']==20_006_000
    assert enable_leverage_capital(moved,NOW+timedelta(minutes=2))==moved
    public=summary(moved)
    assert public['pnl']==6_000
    assert [r['contributed_capital'] for r in public['equity_curve']]==[20_000_000,20_000_000]


def test_leverage_only_enters_after_qa_and_exits_with_realized_pnl():
    # (V3.39) 09:05 이전 관측은 전일 종가가 섞여 기준에서 제외 → 첫 관측을 09:05 로
    first=NOW+timedelta(minutes=5)
    early=tick(state(),None,quote(first),first,long_report={'status':'held','allocations':[]})
    assert not [t for t in early['trades'] if t['sleeve']=='short_leverage']
    at=NOW+timedelta(minutes=30)
    entered=tick(early,signal(at),quote(at,401,40200,3980,30300),at,
                 long_report={'status':'held','allocations':[]})
    buys=[t for t in entered['trades'] if t['sleeve']=='short_leverage' and t['side']=='buy']
    assert len(buys)==1 and buys[0]['ticker']==LEVERAGE
    assert buys[0]['qty']*buys[0]['price']<=3_000_000
    assert entered['leverage_last_review']['passed'] is True
    exit_at=at+timedelta(minutes=5)
    exited=tick(entered,signal(exit_at),quote(exit_at,402,40400,3970,31300),exit_at,  # (V3.38) 목표 +3.0% 로 상향
                long_report={'status':'held','allocations':[]})
    assert exited['leverage_cycles'][-1]['exit_reason']=='profit_target'
    assert exited['sleeves']['short_leverage']['realized_pnl']>0
    assert not exited['sleeves']['short_leverage']['holdings']
    assert len([t for t in tick(exited,signal(exit_at+timedelta(minutes=5)),
        quote(exit_at+timedelta(minutes=5)),exit_at+timedelta(minutes=5),
        long_report={'status':'held','allocations':[]})['trades'] if
        t['sleeve']=='short_leverage' and t['side']=='buy'])==1


def test_neutral_and_stale_leverage_quote_do_not_buy():
    early=tick(state(),None,quote(NOW),NOW,long_report={'status':'held','allocations':[]})
    at=NOW+timedelta(minutes=30)
    neutral=signal(at)
    neutral['outcome']['final_result']['etf_execution']['forecasts'][0]['up_probability']=50
    neutral_result=tick(early,neutral,quote(at,401,40200,3980,30300),at,
                        long_report={'status':'held','allocations':[]})
    assert not [t for t in neutral_result['trades'] if t['sleeve']=='short_leverage']
    assert neutral_result['leverage_last_review']['passed'] is False
    stale=quote(at,401,40200,3980,30300)
    stale[LEVERAGE]['observed_at']=(at-timedelta(minutes=2)).isoformat()
    blocked=tick(early,signal(at),stale,at,long_report={'status':'held','allocations':[]})
    assert not [t for t in blocked['trades'] if t['sleeve']=='short_leverage']
