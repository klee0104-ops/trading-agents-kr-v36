"""Bounded, read-only fundamentals and US-edition news. No broker or LLM."""
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
import hashlib
import math
import re
from urllib.parse import urlparse
from xml.etree import ElementTree as ET
import requests
from rules.citations import sanitize_external

UNIVERSE = {
    '005930': {'name':'삼성전자','symbol':'005930.KS','aliases':['samsung electronics','samsung'], 'related':['nvidia','micron','qualcomm']},
    '000660': {'name':'SK하이닉스','symbol':'000660.KS','aliases':['sk hynix','hynix'], 'related':['nvidia','micron']},
}
ETF = {'069500':'KODEX 200','114800':'KODEX 인버스'}
TRUSTED = ('reuters.com','apnews.com','bloomberg.com','cnbc.com','wsj.com','ft.com',
           'news.samsung.com','news.skhynix.com','nvidianews.nvidia.com','investors.micron.com','federalreserve.gov')
RISK = re.compile(r'\b(bankrupt\w*|fraud|restatement|default|export ban|export restriction\w*|investigation|recall|earnings miss|cuts? (?:guidance|forecast))\b',re.I)

def finite(v):
    return type(v) in (int,float) and math.isfinite(v)

def safe_url(url):
    try:
        u=urlparse(url)
        return u.scheme=='https' and bool(u.hostname) and not u.username and not u.password and u.port in (None,443)
    except (ValueError,TypeError): return False

def trusted(url):
    if not safe_url(url): return False
    host=urlparse(url).hostname
    return any(host==d or host.endswith('.'+d) for d in TRUSTED)

def parse_feed(xml,now):
    if len(xml)>1_000_000 or '<!DOCTYPE' in xml.upper() or '<!ENTITY' in xml.upper():
        raise ValueError('Unsafe feed')
    root=ET.fromstring(xml)
    out=[]
    for item in list(root.iter('item'))[:100]:
        title=sanitize_external(re.sub('<[^>]*>','',item.findtext('title') or ''),300)
        source=item.find('source');link=item.findtext('link') or ''
        if source is None or not trusted(source.get('url','')) or not safe_url(link): continue
        if urlparse(link).hostname!='news.google.com' and not trusted(link): continue
        try:
            published=parsedate_to_datetime(item.findtext('pubDate') or '')
            if published.tzinfo is None or not timedelta(0)<=now-published<=timedelta(days=7): continue
        except (ValueError,TypeError): continue
        if not title: continue
        key=hashlib.sha256((title.casefold()+'|'+source.get('url','')).encode()).hexdigest()[:20]
        out.append({'id':'N'+key,'title':title,'source':sanitize_external(source.text,80),
                    'source_url':source.get('url'),'url':link,'published_at':published.isoformat(),
                    'collected_at':now.isoformat(),'risk_flag':bool(RISK.search(title))})
    return out

def collect_news(now,holdings,universe=None):
    universe=UNIVERSE if universe is None else universe
    # Fixed queries; holdings and account amounts never leave this computer.
    queries=['"Samsung Electronics" OR "SK hynix" when:7d',
             'Nvidia OR Micron OR Qualcomm semiconductor when:7d',
             '"Federal Reserve" OR "US inflation" OR "Korea tariff" when:7d']
    if universe is not UNIVERSE:
        queries=[(' OR '.join('"'+re.sub(r'[^\w\s&.-]',' ',a)+'"' for a in spec.get('aliases',[spec['name']])[:2]))+' when:7d' for spec in list(universe.values())[:10]]+queries[-1:]
    items={};failed=0
    for query in queries:
        try:
            r=requests.get('https://news.google.com/rss/search',params={'q':query,'hl':'en-US','gl':'US','ceid':'US:en'},timeout=12,
                           headers={'User-Agent':'Mozilla/5.0'},allow_redirects=False)
            if r.status_code!=200: raise ValueError('Feed unavailable')
            for item in parse_feed(r.text,now): items[item['id']]=item
        except Exception: failed+=1
    events=[]
    for item in sorted(items.values(),key=lambda x:x['published_at'],reverse=True):
        title=item['title'].casefold();relations=[]
        for code,spec in universe.items():
            kind='direct' if any(a.casefold() in title for a in spec['aliases']) else 'indirect' if any(a.casefold() in title for a in spec['related']) else None
            if kind: relations.append({'code':code,'name':spec['name'],'kind':kind,'held':code in holdings})
        if any(x in title for x in ('federal reserve','inflation','korea tariff')):
            relations += [{'code':c,'name':ETF[c],'kind':'macro','held':True} for c in ETF if c in holdings]
        elif any(x['kind']=='direct' for x in relations):
            relations += [{'code':c,'name':ETF[c],'kind':'indirect','held':True} for c in ETF if c in holdings]
        if relations: events.append({**item,'relations':relations})
    return {'checked_at':now.isoformat(),'status':'ok' if failed==0 else 'partial' if failed<len(queries) else 'unavailable',
            'failed_feeds':failed,'events':events[:80],'edition':'US English; publisher locations vary'}

def normalize_fundamentals(code,periods,balance,price,now,currency='KRW',spec=None):
    """Quarterly statements only. No missing values silently replaced with zero."""
    spec=spec or UNIVERSE[code]
    result={'code':code,'name':spec['name'],'collected_at':now.isoformat(),'source':'Yahoo Finance via yfinance',
            'source_url':'https://finance.yahoo.com/quote/'+spec['symbol']+'/financials/',
            'currency':currency,'status':'insufficient','eligible':False,'metrics':{},'missing':[]}
    try:
        if currency!='KRW': raise ValueError('currency')
        periods=sorted(periods,key=lambda p:p['date'],reverse=True)
        if len(periods)<5: raise ValueError('five_quarters')
        ds=[datetime.fromisoformat(p['date']).date() for p in periods[:5]]
        if not 0<=(now.date()-ds[0]).days<=180: raise ValueError('stale_period')
        if any(not 60<=(ds[i]-ds[i+1]).days<=120 for i in range(4)): raise ValueError('quarter_spacing')
        for p in periods[:4]:
            if not all(finite(p.get(k)) for k in ('revenue','income','fcf')): raise ValueError('income_or_cashflow')
        if not finite(periods[4].get('revenue')):raise ValueError('prior_year_revenue')
        if balance.get('date')!=periods[0]['date']: raise ValueError('balance_period')
        if not all(finite(balance.get(k)) for k in ('debt','equity','shares')): raise ValueError('balance')
        if balance['equity']<=0 or balance['shares']<=0 or not finite(price) or price<=0: raise ValueError('valuation')
        if periods[4]['revenue']<=0: raise ValueError('base_revenue')
        eps=sum(p['eps'] for p in periods[:4]) if all(finite(p.get('eps')) for p in periods[:4]) else None;income=sum(p['income'] for p in periods[:4]);fcf=sum(p['fcf'] for p in periods[:4])
        metrics={'price':price,'ttm_eps':eps,'ttm_income':income,'ttm_fcf':fcf,
                 'revenue_yoy_pct':100*(periods[0]['revenue']/periods[4]['revenue']-1),
                 'debt_equity':balance['debt']/balance['equity'],
                 'pe':price/eps if eps is not None and eps>0 else None,'pb':price/(balance['equity']/balance['shares'])}
        result.update(status='ok',period_end=periods[0]['date'],metrics=metrics)
        if eps is None:
            result.update(status='insufficient',missing=['분기 EPS 누락: '+p['date'] for p in periods[:4] if not finite(p.get('eps'))])
            return result
        result['eligible']=bool(income>0 and fcf>0 and eps>0 and metrics['revenue_yoy_pct']>=0
                                and 0<=metrics['debt_equity']<=1 and 0<metrics['pe']<=20 and 0<metrics['pb']<=3)
    except (ValueError,TypeError,KeyError,ZeroDivisionError) as e:
        result['missing']=[str(e)[:60]]
    return result

def collect_fundamentals(now,universe=None):
    universe=UNIVERSE if universe is None else universe
    import yfinance as yf
    out=[]
    for code,spec in universe.items():
        try:
            t=yf.Ticker(spec['symbol']);inc=t.quarterly_income_stmt;cf=t.quarterly_cashflow;bs=t.quarterly_balance_sheet
            def val(frame,row,col):
                return float(frame.loc[row,col]) if row in frame.index and col in frame.columns else None
            columns=sorted(set(inc.columns)&set(cf.columns),reverse=True)[:5]
            periods=[{'date':str(c.date()),'revenue':val(inc,'Total Revenue',c),'income':val(inc,'Net Income',c),
                      'eps':val(inc,'Diluted EPS',c),'fcf':val(cf,'Free Cash Flow',c)} for c in columns]
            c=columns[0]
            bal={'date':str(c.date()),'debt':val(bs,'Total Debt',c),'equity':val(bs,'Stockholders Equity',c),'shares':val(bs,'Ordinary Shares Number',c)}
            info=t.get_info();price=info.get('regularMarketPrice')
            out.append(normalize_fundamentals(code,periods,bal,price,now,info.get('financialCurrency'),spec=spec))
            out[-1]['sector']=spec.get('sector','Unknown')
            if spec.get('sector') in ('Financial Services','Unknown'):
                out[-1]['eligible']=False
                out[-1].setdefault('missing',[]).append('업종별 추가 자료 검증 필요 · 자동 매수 제외')
        except Exception:
            out.append({'code':code,'name':spec['name'],'status':'unavailable','eligible':False,'metrics':{},'missing':['provider_unavailable'],'collected_at':now.isoformat()})
    return out
