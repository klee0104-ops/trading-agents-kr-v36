"""Separate, prospective ETF execution forecast contract. No model or order calls."""
from datetime import datetime,timedelta
import json,math
from data.futures_feed import pending_snapshot

INSTRUMENTS={'KPI200':'KOSPI200 현물지수','069500':'KODEX 200','114800':'KODEX 인버스 (선물지수 일간 역방향 ETF)'}
def finite(v):return type(v) in (float,int) and math.isfinite(v)

def collect(now,target,fetcher=None):
    from data.kr_daily import fetch_daily
    fetcher=fetcher or fetch_daily
    out={'status':'held','collected_at':now.isoformat(),'target_date':target,'sources':[],
         'futures_note':pending_snapshot()['note']}
    try:
        dates=[]
        for i,(code,name) in enumerate(INSTRUMENTS.items(),901):
            rows=[r for r in fetcher(code,'index' if code=='KPI200' else 'stock') if r['date']<target and r['date']<=str(now.date())]
            if len(rows)<6:raise ValueError('history incomplete')
            rows=rows[-10:]
            if any(not finite(r.get('close')) or r['close']<=0 for r in rows):raise ValueError('invalid quote')
            day=datetime.fromisoformat(rows[-1]['date']).replace(tzinfo=now.tzinfo)
            if not timedelta(0)<=now-day<=timedelta(days=7):raise ValueError('stale evidence')
            dates.append(rows[-1]['date'])
            out['sources'].append({'id':'M'+str(i),'kind':'M','name':name,'source':'네이버 증권 · 대상 거래일 이전 종가','date':rows[-1]['date'],
             'text':json.dumps({'code':code,'name':name,'closes':rows,'five_session_return_pct':100*(rows[-1]['close']/rows[-6]['close']-1)},ensure_ascii=False)})
        if len(set(dates))!=1:raise ValueError('session mismatch')
        out['status']='ok'
    except Exception:out['sources']=[]
    return out

def validate(raw,evidence,now,target):
    from rules.citations import check_points
    held={'schema':2,'status':'held','target_date':target,'generated_at':now.isoformat(),'entry_window':'09:00–09:15','exit_time':'15:20',
          'note':'ETF 별도 판단 또는 자료 검증 미통과 · 신규 매매 보류','forecasts':[],'weight':0,'selected_code':None,'sources':[],'futures_note':evidence.get('futures_note','선물지수 직접 자료 미수집')}
    try:
        if evidence.get('status')!='ok' or not isinstance(raw,dict):return held
        rows=raw['forecasts']
        if not isinstance(rows,list) or len(rows)!=3: return held
        checked=[];seen=set()
        for row in rows:
            code=row['code'];prob=row['up_probability'];points=row['reasoning']
            if code not in INSTRUMENTS or code in seen or not finite(prob) or not 0<=prob<=100: return held
            if not isinstance(points,list) or not 1<=len(points)<=3 or any(not isinstance(p,str) or len(p)>500 for p in points):return held
            own='M'+str(901+list(INSTRUMENTS).index(code))
            checks=check_points(points,evidence['sources'])
            if any(c['status']!='ok' for c in checks) or not any(own in c['ids'] for c in checks):return held
            seen.add(code);checked.append({'code':code,'name':INSTRUMENTS[code],'up_probability':prob,'reasoning':points,'horizon':'대상일 09:00–09:15 실제 진입 → 15:20 청산'})
        candidates=sorted([r for r in checked if r['code']!='KPI200' and r['up_probability']>=60],key=lambda r:r['up_probability'],reverse=True)
        # Both opposite funds strongly positive is an unresolved contradiction, not two buys.
        if len(candidates)>1:return held
        selected=candidates[0]['code'] if candidates else None
        return {**held,'status':'approved','forecasts':checked,'selected_code':selected,'weight':.25 if selected else 0,
                'sources':[{k:s[k] for k in ('id','name','source','date')} for s in evidence['sources']],
                'note':'ETF별 근거 검증 통과 · 시세/진입시간/위험 한도 추가 확인' if selected else 'ETF 상승 확률 진입 기준 미충족 · 현금 계획'}
    except (KeyError,TypeError,ValueError):return held
