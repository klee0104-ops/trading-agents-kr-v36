from datetime import datetime,timedelta
from copy import deepcopy
import json
import pytest
from data.sessions import KST
from data.long_universe import validate_members,screen_info,choose_watchlist,choose_batch,update_catalog
from memory.research_reports import archive_short,archive_long,read_public
from memory.long_report import decision
from execution.split_portfolio import fresh,tick,summary,LONG,INVERSE
NOW=datetime(2026,9,29,14,tzinfo=KST)

def member(i):return {'code':f'{i:06}','name':f'가상기업{i}','rank':i}
def info(sector='Industrials'):
 return dict(sector=sector,industry='Synthetic',mostRecentQuarter=(NOW-timedelta(days=60)).timestamp(),trailingPE=10,freeCashflow=100,debtToEquity=40,revenueGrowth=.1,priceToBook=1,returnOnEquity=.1,profitMargins=.2)
def screens():return [screen_info(member(i),info('Sector'+str(i%6)),NOW) for i in range(1,101)]
def env():
 return {'mode':'real','saved_at':NOW.isoformat(),'outcome':{'target_date':'2026-09-29','news_titles':['가상 기사 제목 매우 긴 문장'], 'private_prompt':'SHOULD_NOT_EXPORT',
 'final_result':{'kospi_prediction':{'direction':'up','up_probability':60,'reasoning':['가상 기사 제목 매우 긴 문장']},'core_stocks':[]},
 'analyst_reports':{'Synthetic':{'summary':'검토 [M1]','notable_points':['현금흐름 [M1]'],'citation_ok':True}},
 'source_registry':[{'id':'N1','kind':'N','source':'합성 출처','date':'2026-09-29','text':'DO_NOT_EXPORT_TITLE'}]}}

def test_exact_pool_no_duplicates_or_small_fallback():
 assert len(validate_members([member(i) for i in range(100)]))==100
 for rows in ([member(1)]*100,[member(i) for i in range(99)]):
  with pytest.raises(ValueError):validate_members(rows)

def test_sector_specific_missing_and_preference():
 bank=info('Financial Services');bank.update(debtToEquity=9999,freeCashflow=None)
 assert screen_info(member(1),bank,NOW)['status']=='research'
 assert '자본적정성' in screen_info(member(1),bank,NOW)['reasons'][1]
 assert screen_info(member(1),{},NOW)['status']=='needs_data'
 assert screen_info(member(1),{**info(),'mostRecentQuarter':(NOW+timedelta(days=1)).timestamp()},NOW)['status']=='needs_data'

def test_watchlist_diversity_stability_rotation():
 rows=screens();a=choose_watchlist(rows,[],[],[],NOW)
 assert len(a)==30 and max(sum(s['sector']==sec for s in rows if s['code'] in a) for sec in {s['sector'] for s in rows})<=5
 assert choose_watchlist(rows,a,[],['가상기업99'],NOW)==a
 first=choose_batch(a,[],{});assert not set(first)&set(choose_batch(a,[],dict.fromkeys(first,NOW.isoformat())))


def test_saved_daily_screen_expands_watchlist_without_refetching():
 rows=screens();old={'attempt_day':str(NOW.date()),'status':'ok','screens':rows,'watchlist':choose_watchlist(rows,[],[],[],NOW,limit=15)}
 def forbidden(*args,**kwargs):raise AssertionError('Saved screen must be reused')
 updated=update_catalog(old,NOW,fetcher=forbidden,screener=forbidden)
 assert len(updated['watchlist'])==30 and len(updated['changes'])==1
 assert update_catalog(updated,NOW,fetcher=forbidden,screener=forbidden)['changes']==updated['changes']

def test_catalog_failure_preserves_holdings_but_blocks_new_buys():
 old={'instruments':{'123456':{'name':'과거보유'}},'watchlist':['123456']}
 def fail(now):raise ValueError('Synthetic failure')
 c=update_catalog(old,NOW,fetcher=fail)
 assert c['status']=='unavailable' and '123456' in c['instruments']
 assert decision({'catalog':c},NOW)['status']=='held'

def test_archive_immutable_idempotent_whitelist(tmp_path):
 p=tmp_path/'reports.json';e=env();archive_short(e,p);archive_short(e,p)
 e['saved_at']=(NOW+timedelta(days=1)).isoformat();archive_short(e,p)
 r=read_public(p);assert len(r['reports'])==2
 blob=json.dumps(r,ensure_ascii=False)
 assert 'SHOULD_NOT_EXPORT' not in blob and 'DO_NOT_EXPORT_TITLE' not in blob and '가상 기사 제목 매우 긴 문장' not in blob
 assert '현금흐름 [M1]' in blob

def test_archive_secrets_and_markup_blocked(tmp_path):
 from security.public_report import PublicationBlocked
 for bad in ('<script>alert(1)</script>','ghp_'+'a'*36):
  e=env();e['outcome']['analyst_reports']['Synthetic']['summary']=bad
  with pytest.raises(PublicationBlocked):archive_short(e,tmp_path/'reports.json')
 assert not (tmp_path/'reports.json').exists()

def test_historical_long_not_rewritten_by_news(tmp_path):
 p=tmp_path/'reports.json';s={'report':{'generated_at':NOW.isoformat(),'status':'held','note':'합성 보류','fundamentals':[],'roles':[]}}
 archive_long(s,p,NOW);first=p.read_bytes();s['news']={'status':'unavailable','checked_at':(NOW+timedelta(hours=1)).isoformat()}
 archive_long(s,p,NOW+timedelta(hours=1));assert p.read_bytes()==first

def test_dynamic_paper_known_code_and_unreviewed_holding_protected():
 plan={'status':'approved','generated_at':NOW.isoformat(),'valid_until':(NOW+timedelta(days=8)).isoformat(),'horizon_days':180,
 'instruments':{'123456':{'name':'합성산업','sector':'Industrials'}},'reviewed_codes':['123456'],
 'allocations':[{'code':'123456','weight':.2,'buy_below':100}], 'blocked_codes':[]}
 quotes={c:{'date':str(NOW.date()),'value':100,'market_status':'OPEN','observed_at':NOW.isoformat(),'traded_at':NOW.isoformat()} for c in (LONG,INVERSE,'123456')}
 d=tick(fresh(NOW),None,quotes,NOW,plan)
 assert d['trades'][0]['name']=='합성산업' and d['trades'][0]['sleeve']=='long'
 assert summary(d)['sleeves'][0]['holdings'][0]['name']=='합성산업'
 d['sleeves']['long']['decision_key']='';plan['reviewed_codes']=[];plan['allocations']=[]
 later=NOW+timedelta(minutes=5)
 for q in quotes.values():q.update(observed_at=later.isoformat(),traded_at=later.isoformat())
 after=tick(d,None,quotes,later,plan)
 assert len(after['trades'])==1 and after['sleeves']['long']['holdings']
 unknown=deepcopy(plan);unknown['allocations']=[{'code':'999999','weight':.2,'buy_below':100}]
 blocked=tick(fresh(NOW),None,quotes,NOW,unknown)
 assert blocked['trades']==[] and not blocked['sleeves']['long']['holdings']
 assert blocked['quote_health']['status']=='ok'  # Only an unheld buy candidate is missing.
 assert '999999' in blocked['quote_health']['missing_codes']
 assert summary(blocked)['total_return_pct'] is not None

def test_reports_public_csp_and_no_execution():
 from web.export_site import render_fragment
 from security.public_report import harden_report
 from memory.research_reports import short_report
 report=short_report(env());report['id']='synthetic'
 page='<!doctype html><html><head></head><body>'+render_fragment({'research_reports':{'reports':[report],'total':1}})+'</body></html>'
 hardened=harden_report(page)
 assert "connect-src &#x27;none&#x27;" in hardened and 'id="tab-reports"' in page

def test_expansion_does_not_reset_weekly_budget(tmp_path,monkeypatch):
 import data.long_research as data
 import data.long_universe as universe
 from tools.long_research import cycle
 from tools.paper_portfolio import write
 p=tmp_path/'long.json';write(p,{'last_model_review_at':NOW.isoformat(),'fundamentals_checked_day':str(NOW.date()),
                               'fundamentals':[{'code':'123456','status':'ok'}],
                               'report':{'generated_at':NOW.isoformat(),'status':'held','roles':[]}})
 cat={'status':'ok','watchlist':['123456'],'instruments':{'123456':{'name':'합성기업','symbol':'123456.KS','aliases':['Synthetic'],'related':[]}},'checked_at':NOW.isoformat()}
 monkeypatch.setattr(universe,'update_catalog',lambda *a,**kw:cat)
 monkeypatch.setattr(data,'collect_news',lambda *a:{'status':'ok','events':[],'checked_at':NOW.isoformat()})
 def forbidden(*a):raise AssertionError('No extra weekly call')
 monkeypatch.setattr(data,'collect_fundamentals',forbidden)
 s=cycle(p,tmp_path/'book.json',NOW+timedelta(hours=1),forbidden,expanded=True)
 assert s['last_model_review_at']==NOW.isoformat() and s['catalog']['next_batch']==['123456']


def test_expanded_batch_reserves_slots_for_long_holdings(tmp_path,monkeypatch):
 import data.long_research as data
 import data.long_universe as universe
 from tools.long_research import cycle
 from tools.paper_portfolio import write
 held=['000001','000002'];candidates=[f'{i:06}' for i in range(3,10)]
 p=tmp_path/'long.json';write(p,{'last_model_review_at':NOW.isoformat(),'fundamentals_checked_day':str(NOW.date()),
                               'fundamentals':[{'code':c,'status':'ok'} for c in held+candidates[:3]],
                               'report':{'status':'held','roles':[]}})
 write(tmp_path/'book.json',{'sleeves':{'long':{'holdings':dict.fromkeys(held,{})}}})
 instruments={c:{'name':'합성기업'+c,'symbol':c+'.KS','aliases':['Synthetic'],'related':[]} for c in held+candidates}
 catalog={'status':'ok','watchlist':candidates,'instruments':instruments,'review_history':{}}
 monkeypatch.setattr(universe,'update_catalog',lambda *a,**kw:catalog)
 monkeypatch.setattr(data,'collect_news',lambda *a:{'status':'ok','events':[],'checked_at':NOW.isoformat()})
 def forbidden(*a):raise AssertionError('No data or model call while review is not due')
 monkeypatch.setattr(data,'collect_fundamentals',forbidden)
 state=cycle(p,tmp_path/'book.json',NOW+timedelta(hours=1),forbidden,expanded=True)
 assert state['catalog']['next_batch']==held+candidates[:3]


def test_new_batch_collects_once_without_repeating_daily_model_review(tmp_path,monkeypatch):
 import data.long_research as data
 import data.long_universe as universe
 from tools.long_research import cycle
 from tools.paper_portfolio import write
 p=tmp_path/'long.json';book=tmp_path/'book.json'
 write(p,{'last_model_review_at':NOW.isoformat(),'last_candidate_attempt_day':str(NOW.date()),
          'fundamentals_checked_day':str(NOW.date()),'dart_financial_checked_day':str(NOW.date()),
          'fundamentals':[{'code':'000001'}],
          'report':{'generated_at':NOW.isoformat(),'status':'approved','roles':[]}})
 codes=[f'{i:06}' for i in range(2,7)]
 instruments={c:{'name':'합성기업'+c,'symbol':c+'.KS','aliases':['Synthetic'],'related':[]} for c in codes}
 catalog={'status':'ok','watchlist':codes,'instruments':instruments,'review_history':{}}
 monkeypatch.setattr(universe,'update_catalog',lambda *a,**kw:catalog)
 monkeypatch.setattr(data,'collect_news',lambda *a:{'status':'ok','events':[],'checked_at':NOW.isoformat()})
 collected=[]
 def collect(now,selected):
  collected.append(list(selected))
  return [{'code':c,'name':instruments[c]['name'],'status':'insufficient','eligible':False,'metrics':{}} for c in selected]
 monkeypatch.setattr(data,'collect_fundamentals',collect)
 def no_model():raise AssertionError('Daily model-call budget must remain closed')
 first=cycle(p,book,NOW+timedelta(hours=1),no_model,expanded=True)
 second=cycle(p,book,NOW+timedelta(hours=2),no_model,expanded=True)
 assert collected==[codes]
 assert first['report']['status']=='approved' and second['last_model_review_at']==NOW.isoformat()


def test_paged_pool_includes_alphanumeric_codes(monkeypatch):
 from data.long_universe import fetch_members
 import data.long_universe as module
 pages=[]
 class Response:
  def __init__(self,page):self.page=page
  def raise_for_status(self):pass
  def json(self):
   start=0 if self.page==1 else 100
   return {'stocks':[{'itemCode':'0126Z0' if i==1 else f'{i:06}','stockName':f'합성{i}'+('우' if i%10==0 else ''),'marketValue':1000-i,'stockEndType':'stock'} for i in range(start,start+100)]}
 def get(url,params,**kw):
  assert params['pageSize']==100
  pages.append(params['page']);return Response(params['page'])
 monkeypatch.setattr(module.requests,'get',get)
 members=fetch_members(NOW)['members']
 assert pages==[1,2] and len(members)==100 and any(m['code']=='0126Z0' for m in members)
 assert not any(m['name'].endswith('우') for m in members)


def test_valuation_supplement_rejects_forecast_and_period_mismatch():
 from data.long_universe import parse_valuation
 period=datetime(2026,6,30,tzinfo=KST).timestamp()
 payload={'totalInfos':[{'code':'per','value':'12.22배','valueDesc':'2026.06.'},{'code':'pbr','value':'3.17배','valueDesc':'2026.06.'},{'code':'cnsPer','value':'5.67배'}]}
 assert parse_valuation(payload,NOW,period)=={'trailingPE':12.22,'priceToBook':3.17}
 assert parse_valuation(payload,NOW,datetime(2026,3,31,tzinfo=KST).timestamp())=={}
 payload['totalInfos'][0]['value']='N/A'
 assert 'trailingPE' not in parse_valuation(payload,NOW,period)

def test_report_route_is_read_only_and_local(tmp_path,monkeypatch):
 from web import server
 from config import Config
 p=tmp_path/'reports.json';archive_short(env(),p)
 monkeypatch.setattr(Config,'REPORT_ARCHIVE_PATH',str(p))
 monkeypatch.setattr(Config,'WEB_READONLY',True)
 c=server.app.test_client();headers={'Host':f'127.0.0.1:{Config.WEB_PORT}'}
 r=c.get('/api/reports',headers=headers)
 assert r.status_code==200 and len(r.json['reports'])==1
 assert r.headers['Cache-Control']=='no-store'
 assert c.post('/api/reports',json={},headers=headers).status_code==403
 assert c.get('/api/reports',headers={'Host':'external.example'}).status_code==403
 before=p.read_bytes();c.get('/api/reports',headers=headers);assert p.read_bytes()==before
