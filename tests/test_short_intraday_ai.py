"""Event-driven review and same-day paper transitions using synthetic data only."""
from datetime import datetime, timedelta
import json

from data.sessions import KST
from execution.short_intraday_ai import approved_plan, validate
from execution.split_portfolio import fresh, tick, valuation_audit
from tools.short_intraday_review import MAX_REVIEWS_PER_DAY, assess, run_once, source_registry

NOW = datetime(2026, 9, 30, 10, 30, tzinfo=KST)


def quotes(now=NOW, index=100, long=100, inverse=100):
    return {c:{'date':str(now.date()),'value':v,'market_status':'OPEN',
               'observed_at':now.isoformat(),'traded_at':now.isoformat()}
            for c,v in (('KPI200',index),('069500',long),('114800',inverse))}


def raw(index=60,long=60,inverse=40):
    return {'forecasts':[{'code':c,'up_probability':p,'reasoning':['당일 자산 시세 [M'+str(i)+']']}
                         for i,c,p in ((901,'KPI200',index),(902,'069500',long),(903,'114800',inverse))]}


def plan(now=NOW,index=60,long=60,inverse=40):
    return validate(raw(index,long,inverse),{'approve':True,'reasoning':['시세 재확인 [M901]']},
                    source_registry(quotes(now),now,.4,[]),now,'price')


def account(now=NOW):
    d=fresh(now);d['short_policy']='etf_v2';return d


def test_intraday_two_role_citation_qa_and_expiry():
    p=plan()
    assert p['qa']['passed'] is True and (p['selected_code'],p['weight'])==('069500',.10)
    assert approved_plan(p,NOW+timedelta(minutes=1)) is not None
    assert approved_plan(p,NOW+timedelta(minutes=21)) is None
    rejected=validate(raw(),{'approve':True,'reasoning':['근거 없음']},
                      source_registry(quotes(),NOW,.4,[]),NOW,'price')
    assert rejected['status']=='held'
    conflict=validate(raw(inverse=60),{'approve':True,'reasoning':['시세 [M901]']},
                      source_registry(quotes(),NOW,.4,[]),NOW,'price')
    assert conflict['status']=='held'
    news_without_citation=validate(raw(),{'approve':True,'reasoning':['시세 [M901]']},
                                   source_registry(quotes(),NOW,.4,[{'title':'시장 충격','source':'연합뉴스','published_at':NOW.isoformat()}]),NOW,'news')
    assert news_without_citation['status']=='held'


def test_cited_trader_can_open_five_percent_exploration_despite_reviewer_veto():
    proposal=raw(index=53,long=53,inverse=47)
    proposal['paper_action']='069500'
    proposal['paper_rationale']=['지수와 정방향 ETF 판단을 확인 [M901] [M902]']
    p=validate(proposal,{'approve':False,'reasoning':['방향 확신 부족 [M901]']},
               source_registry(quotes(),NOW,.4,[]),NOW,'price')
    assert (p['status'],p['weight'],p['selected_code'])==('experimental',.05,'069500')
    assert p['qa']['passed'] is False and p['qa']['exploratory_eligible'] is True
    assert approved_plan(p,NOW+timedelta(minutes=1)) is not None
    opened=tick(account(),None,quotes(NOW+timedelta(minutes=1)),NOW+timedelta(minutes=1),intraday_plan=p)
    assert opened['trades'][0]['side']=='buy'
    assert opened['active_short_cycle']['selection_basis']=='intraday_ai_exploratory'
    assert opened['active_short_cycle']['qa_tier']=='exploratory'
    exit_at=NOW.replace(hour=15,minute=21)
    closed=tick(opened,None,quotes(exit_at,long=101,inverse=99),exit_at)
    assert [t['side'] for t in closed['trades']]==['buy','sell']
    cycle=closed['short_cycles'][0]
    assert cycle['status']=='closed' and cycle['net_pnl']!=0
    assert all(score['correct'] is None for score in cycle['scores'])


def test_exploration_requires_explicit_cited_action_and_fresh_execution_plan():
    sources=source_registry(quotes(),NOW,.4,[])
    veto={'approve':False,'reasoning':['불확실 [M901]']}
    assert validate(raw(53,53,47),veto,sources,NOW,'price')['status']=='held'
    proposal=raw(53,53,47)
    proposal.update(paper_action='069500',paper_rationale=['근거 없음'])
    assert validate(proposal,veto,sources,NOW,'price')['qa']['reason_code']=='trader_action_citation'
    proposal['paper_rationale']=['근거 [M901] [M902]']
    p=validate(proposal,veto,sources,NOW,'price')
    altered={**p,'weight':.40}
    assert approved_plan(altered,NOW+timedelta(minutes=1)) is None
    assert approved_plan(p,NOW+timedelta(minutes=21)) is None


def test_trader_cash_action_blocks_directional_buy():
    proposal=raw()
    proposal.update(paper_action='cash',paper_rationale=['현금 유지 [M901]'])
    p=validate(proposal,{'approve':True,'reasoning':['검토 [M901]']},
               source_registry(quotes(),NOW,.4,[]),NOW,'scheduled_open')
    assert p['status']=='approved' and p['selected_code'] is None
    assert approved_plan(p,NOW+timedelta(minutes=1)) is not None


def test_five_minute_watcher_periodic_reviews_stop_at_daily_cap(tmp_path):
    path=tmp_path/'review.json'
    calls=[]
    class Stub:
        def complete(self,system,user,**kwargs):
            calls.append(system)
            return json.dumps({'approve':True,'reasoning':['시세 [M901]']} if '반대검토자' in system else raw(),ensure_ascii=False)
    first=NOW.replace(hour=9,minute=20)
    fetch=lambda moment:(quotes(moment),moment)
    no_news=lambda moment:[]
    assert run_once(path,fetch,no_news,Stub,lambda:first)=='approved'
    assert len(calls)==2
    assert run_once(path,fetch,no_news,Stub,lambda:first+timedelta(minutes=65))=='watching'
    for attempt in range(1,MAX_REVIEWS_PER_DAY):
        at=first+timedelta(minutes=70*attempt)
        assert run_once(path,fetch,no_news,Stub,lambda at=at:at)=='approved'
        assert len(calls)==2*(attempt+1)
    after=first+timedelta(minutes=70*(MAX_REVIEWS_PER_DAY-1)+40)
    assert run_once(path,fetch,no_news,Stub,lambda:after)=='watching'
    assert len(calls)==2*MAX_REVIEWS_PER_DAY
    saved=json.loads(path.read_text(encoding='utf-8'))
    assert saved['attempts']==MAX_REVIEWS_PER_DAY and '호출 한도' in saved['last_status']


def test_reviewer_failure_records_stage_without_raw_response(tmp_path):
    path=tmp_path/'review.json'
    class BrokenReviewer:
        def complete(self,system,user,**kwargs):
            if '반대검토자' in system:
                raise RuntimeError('private provider response must not be stored')
            return json.dumps(raw(),ensure_ascii=False)
    first=NOW.replace(hour=9,minute=20)
    fetch=lambda moment:(quotes(moment),moment)
    assert run_once(path,fetch,lambda moment:[],BrokenReviewer,lambda:first)=='held'
    saved=json.loads(path.read_text(encoding='utf-8'))
    assert saved['last_failure_code']=='reviewer_response'
    assert saved['plan'] is None
    assert 'private provider response' not in path.read_text(encoding='utf-8')


def test_second_periodic_review_and_soft_plan_is_saved(tmp_path):
    path=tmp_path/'review.json'
    calls=[]
    class CautiousTeam:
        def complete(self,system,user,**kwargs):
            calls.append(system)
            if '반대검토자' in system:
                return json.dumps({'approve':False,'reasoning':['확신 부족 [M901]']},ensure_ascii=False)
            proposal=raw(53,53,47)
            proposal.update(paper_action='069500',paper_rationale=['정방향 탐색 [M901] [M902]'])
            return json.dumps(proposal,ensure_ascii=False)
    morning=NOW.replace(hour=9,minute=20)
    periodic=morning+timedelta(minutes=70)
    fetch=lambda moment:(quotes(moment),moment)
    assert run_once(path,fetch,lambda moment:[],CautiousTeam,lambda:morning)=='approved'
    saved=json.loads(path.read_text(encoding='utf-8'))
    assert saved['plan']['status']=='experimental' and saved['attempts']==1
    assert run_once(path,fetch,lambda moment:[],CautiousTeam,lambda:periodic)=='approved'
    saved=json.loads(path.read_text(encoding='utf-8'))
    assert saved['plan']['trigger']=='scheduled_periodic' and saved['attempts']==2
    assert len(calls)==4


def test_rate_limit_pauses_models_for_day_but_keeps_quote_watcher(tmp_path):
    from agents.base import LLMCallError
    path=tmp_path/'review.json'
    calls=[]
    class Limited:
        def complete(self,system,user,**kwargs):
            calls.append(system)
            raise LLMCallError('Gemini API 호출 실패: ClientError: 429 RESOURCE_EXHAUSTED')
    first=NOW.replace(hour=9,minute=20)
    fetch=lambda moment:(quotes(moment),moment)
    assert run_once(path,fetch,lambda moment:[],Limited,lambda:first)=='held'
    later=first+timedelta(minutes=70)
    assert run_once(path,fetch,lambda moment:[],Limited,lambda:later)=='watching'
    saved=json.loads(path.read_text(encoding='utf-8'))
    assert saved['attempts']==1 and len(calls)==1
    assert len(saved['observations'])==2
    assert saved['last_failure_code']=='rate_limited'


def test_intraday_plan_can_enter_switch_and_exit_without_rewriting_morning_prediction():
    at=NOW+timedelta(minutes=1)
    opened=tick(account(),None,quotes(at),at,intraday_plan=plan())
    assert opened['trades'][0]['ticker']=='069500'
    assert opened['active_short_cycle']['selection_basis']=='intraday_ai'
    opposite_at=NOW+timedelta(minutes=5)
    opposite=plan(opposite_at,index=40,long=40,inverse=60)
    # (V3.39) 반대 ETF 판단이면 기존 보유는 청산하지만, 장중 신규 진입은 하루 1회라 반대 ETF 는 새로 사지 않는다
    switched=tick(opened,None,quotes(opposite_at+timedelta(minutes=1)),opposite_at+timedelta(minutes=1),intraday_plan=opposite)
    assert [t['side'] for t in switched['trades']]==['buy','sell']
    assert not switched['sleeves']['short']['holdings']
    assert switched['short_cycles'][0]['exit_reason']=='intraday_ai_switch'
    assert all(score['correct'] is None for score in switched['short_cycles'][0]['scores'])
    assert valuation_audit(switched,opposite_at+timedelta(minutes=1))['status']=='ok'


def test_intraday_cash_opinion_keeps_profitable_position():
    at=NOW+timedelta(minutes=1)
    opened=tick(account(),None,quotes(at),at,intraday_plan=plan())
    cash_at=NOW+timedelta(minutes=10)
    up=quotes(cash_at+timedelta(minutes=1))
    up['069500']['value']=up['069500']['value']*1.01
    kept=tick(opened,None,up,cash_at+timedelta(minutes=1),intraday_plan=plan(cash_at,index=50,long=50,inverse=50))
    assert [t['side'] for t in kept['trades']]==['buy'] and kept['sleeves']['short']['holdings']
    assert '보유 유지' in kept['sleeves']['short']['status']


def test_intraday_plan_never_overrides_manual_holding():
    from execution.manual_paper import prepare
    morning=NOW.replace(hour=9,minute=5)
    held,_=prepare(account(morning),'buy','069500',.05,quotes(morning),morning)
    later=NOW+timedelta(minutes=1)
    after=tick(held,None,quotes(later),later,intraday_plan=plan(NOW,index=40,long=40,inverse=60))
    assert len(after['trades'])==1 and after['active_short_cycle']['selection_basis']=='manual'
