"""Dated large-cap pool, sector-aware screening and stable research watchlist."""
from datetime import datetime,timedelta
import re
from concurrent.futures import ThreadPoolExecutor
import requests
from data.long_research import finite,UNIVERSE

def validate_members(rows):
    out=[];seen=set()
    for r in rows:
        c=str(r.get('code',''));n=str(r.get('name','')).strip()
        if not re.fullmatch(r'[0-9A-Z]{6}',c) or not n or len(n)>80 or c in seen:raise ValueError('Invalid member')
        seen.add(c);out.append({'code':c,'name':n,'rank':len(out)+1})
    if len(out)!=100:raise ValueError('Expected 100 common stocks')
    return out

def fetch_members(now):
    # Deliberately NOT labelled as the official KOSPI100 index.
    from data.universe import parse_market_value_payload,_is_preferred
    rows=[]
    for page in (1,2):
        r=requests.get('https://m.stock.naver.com/api/stocks/marketValue/KOSPI',params={'page':page,'pageSize':100},
                       headers={'User-Agent':'Mozilla/5.0','Referer':'https://m.stock.naver.com/'},timeout=12)
        r.raise_for_status()
        rows.extend(x for x in parse_market_value_payload(r.json()) if not _is_preferred(x['name']))
        if len(rows)>=100:break
    rows=rows[:100]
    return {'as_of':str(now.date()),'checked_at':now.isoformat(),'status':'ok','members':validate_members(rows),
            'label':'코스피 시가총액 상위 100개 보통주','source':'네이버 증권 시가총액 순위',
            'note':'공식 코스피100 지수 구성과 다릅니다. 우선주 제외 후 100개를 선정합니다.'}

def screen_info(member,info,now):
    sector=str(info.get('sector') or 'Unknown');industry=str(info.get('industry') or 'Unknown')
    financial=sector=='Financial Services'
    metrics={k:info.get(k) if finite(info.get(k)) else None for k in
             ('trailingPE','priceToBook','returnOnEquity','profitMargins','freeCashflow','debtToEquity','revenueGrowth')}
    q=info.get('mostRecentQuarter');recent=finite(q) and 0<=(now.timestamp()-q)<=180*86400
    missing=[];reasons=[]
    required=('priceToBook','returnOnEquity','profitMargins') if financial else ('trailingPE','freeCashflow','debtToEquity','revenueGrowth')
    missing=[k for k in required if metrics[k] is None]
    if not recent:missing.append('최근 결산기 확인 필요')
    if sector=='Unknown':missing.append('업종 확인 필요')
    if missing:status='needs_data';score=0;reasons=['자료 부족은 부적격 기업 판정이 아닙니다.']
    elif financial:
        passed=0<metrics['priceToBook']<=2 and metrics['returnOnEquity']>=.08 and metrics['profitMargins']>0
        status='research' if passed else 'watch';score=3 if passed else 1
        reasons=['금융업: PBR·ROE·이익률로 조사 후보 선별. 부채비율/FCF 제조업 기준 미적용.',
                 '건전성·자본적정성 별도 검증 전 자동 매수 승인 불가.']
    else:
        checks=[0<metrics['trailingPE']<=25,metrics['freeCashflow']>0,0<=metrics['debtToEquity']<=150,metrics['revenueGrowth']>=0]
        score=sum(checks);status='research' if all(checks) else 'watch'
        reasons=['수익 대비 가격·현금흐름·재무부담·성장 지표를 비교한 조사용 필터입니다. 매수 승인이 아닙니다.']
    aliases=[member['name']]
    for key in ('shortName','longName'):
        name=re.sub(r'[^\w\s&.-]',' ',str(info.get(key) or ''))[:90].strip()
        if name and name not in aliases:aliases.append(name)
    base=UNIVERSE.get(member['code'],{})
    return {**member,'sector':sector,'industry':industry,'status':status,'score':score,'reasons':reasons,
            'missing':missing,'metrics':metrics,'checked_at':now.isoformat(),
            'period_end':datetime.fromtimestamp(q,now.tzinfo).date().isoformat() if recent else None,
            'aliases':base.get('aliases',aliases),'related':base.get('related',[]),'symbol':member['code']+'.KS',
            'source':'Yahoo Finance 요약 지표 (분기 원문 검증 전)'}

def parse_valuation(payload,now,expected_period):
    """Supplement screening only; never substitute forecast EPS for missing quarterly EPS."""
    if not finite(expected_period):return {}
    day=datetime.fromtimestamp(expected_period,now.tzinfo)
    if not 0<=(now-day).total_seconds()<=180*86400:return {}
    expected=day.strftime('%Y.%m.')
    rows={r.get('code'):r for r in payload.get('totalInfos',[]) if isinstance(r,dict)}
    out={}
    for key,field in (('per','trailingPE'),('pbr','priceToBook')):
        row=rows.get(key,{})
        raw=str(row.get('value','')).replace(',','').replace('배','').strip()
        if row.get('valueDesc')!=expected or not re.fullmatch(r'-?\d+(?:\.\d+)?',raw):continue
        value=float(raw)
        if finite(value):out[field]=value
    return out


def collect_screens(members,now):
    import yfinance as yf
    def one(m):
        try:info=yf.Ticker(m['code']+'.KS').get_info()
        except Exception:info={}
        supplemental={}
        if any(not finite(info.get(k)) for k in ('trailingPE','priceToBook')) and info.get('mostRecentQuarter'):
            try:
                response=requests.get('https://m.stock.naver.com/api/stock/'+m['code']+'/integration',headers={'User-Agent':'Mozilla/5.0','Referer':'https://m.stock.naver.com/'},timeout=10)
                response.raise_for_status();payload=response.json()
                if payload.get('itemCode')==m['code']:
                    supplemental=parse_valuation(payload,now,info['mostRecentQuarter'])
                    info={**info,**{k:v for k,v in supplemental.items() if not finite(info.get(k))}}
            except Exception:pass
        row=screen_info(m,info,now)
        if supplemental:
            row['source']='Yahoo Finance 요약 + 네이버 PER/PBR (동일 결산월, 조사용)'
            row['valuation_source']='네이버 증권 · Yahoo와 결산월 대조'
        return row
    with ThreadPoolExecutor(max_workers=3) as pool:return list(pool.map(one,members))

def choose_watchlist(screens,previous,held,short_names,now,limit=15):
    by={s['code']:s for s in screens};previous=set(previous);chosen=[];counts={}
    # Holdings are monitored separately; short-term interest is a tie-break, never a buy signal.
    ranked=sorted(screens,key=lambda s:(s['status']!='research',-s['score'],s['code'] not in previous,s['name'] not in short_names,s['rank']))
    for row in ranked:
        sector=row['sector']
        if counts.get(sector,0)>=3:continue
        chosen.append(row['code']);counts[sector]=counts.get(sector,0)+1
        if len(chosen)==limit:break
    return chosen

def choose_batch(watchlist,held,history,limit=3):
    queue=sorted(set(watchlist)|set(held),key=lambda c:(history.get(c,''),c not in held,c))
    return queue[:limit]

def update_catalog(old,now,held=(),short_names=(),fetcher=fetch_members,screener=collect_screens):
    from copy import deepcopy
    out=deepcopy(old or {})
    if out.get('attempt_day')==str(now.date()) and out.get('status')=='ok':return out
    out['attempt_day']=str(now.date())
    try:
        universe=fetcher(now);universe['members']=validate_members(universe['members']);screens=screener(universe['members'],now)
        if len(screens)!=100 or {s['code'] for s in screens}!={m['code'] for m in universe['members']}:raise ValueError('Incomplete screen')
        previous=out.get('watchlist',[]);watch=choose_watchlist(screens,previous,held,short_names,now)
        out.update(universe=universe,screens=screens,watchlist=watch,status='ok',checked_at=now.isoformat())
        change={'at':now.isoformat(),'added':sorted(set(watch)-set(previous)),'removed':sorted(set(previous)-set(watch)),
                'reason':'업종별 조사 필터·업종당 최대 3개·기존 관심 목록 안정성 우선'}
        if change['added'] or change['removed']:out.setdefault('changes',[]).append(change)
        out['changes']=out.get('changes',[])[-52:]
        retained=out.setdefault('instruments',{})
        retained.update({s['code']:{k:s[k] for k in ('name','sector','industry','symbol','aliases','related')} for s in screens})
    except Exception:out['status']='unavailable'
    return out
