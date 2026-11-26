"""Separate, prospective ETF execution forecast contract. No model or order calls."""
from datetime import datetime,timedelta
import json,math
from data.futures_feed import pending_snapshot

INSTRUMENTS={'KPI200':'KOSPI200 현물지수','069500':'KODEX 200','114800':'KODEX 인버스 (선물지수 일간 역방향 ETF)'}
ETF_PROBABILITY_RULE = """\nETF 확률은 자산별 확인 자료로 먼저 상승·하락 중 더 가능성 높은 방향을 정한 뒤 근거 강도에 맞춰 표시하세요.
불확실하다는 이유만으로 모든 자산을 같은 확률로 두지 마세요. 정확히 50은 해당 자산의 방향을 판단할 자료가 전혀 없을 때만 쓰세요.
없는 자료나 수치는 만들지 말고, 방향 판단이 불가능하면 근거에 그 한계를 밝히세요.\n"""
WEIGHTS_BY_SCHEMA={3:(.10,.25),4:(.20,.40)}  # weak 50–60%, strong 60%+
CURRENT_SCHEMA=4
NEUTRAL_PROBE_WEIGHT=.05  # Short sleeve only; a rule-based experiment, not an AI hit.
NEUTRAL_TREND_PCT=.30  # Prior five sessions, never the target day's price.
def finite(v):return type(v) in (float,int) and math.isfinite(v)

QA_REASONS={
 'index_direction_conflict':'KOSPI200 확률 방향과 고른 ETF 방향이 어긋남',
 'evidence_unavailable':'ETF 3종 과거시세 근거를 모두 확보·검증하지 못함',
 'model_plan_missing':'모델이 ETF 별도 판단을 출력하지 않음',
 'model_plan_invalid':'모델 ETF 판단이 객체 형식이 아님',
 'forecast_count':'KOSPI200·정방향·인버스 3종 예측이 모두 필요함',
 'forecast_row_invalid':'ETF 종목별 예측 항목의 필수 필드가 누락됨',
 'instrument_invalid':'허용되지 않은 ETF 또는 지수 코드가 포함됨',
 'instrument_duplicate':'동일한 ETF 또는 지수 예측이 중복됨',
 'probability_invalid':'ETF 상승 확률이 0~100 범위의 유한 숫자가 아님',
 'reasoning_invalid':'종목별 판단 근거가 없거나 형식·길이가 맞지 않음',
 'citation_invalid':'종목별 근거 ID 검증에 실패함',
 'own_source_missing':'종목 예측에 해당 자산의 과거시세 ID가 없음',
 'opposed_etfs_positive':'정방향·인버스 ETF를 동시에 50% 초과 상승으로 예측해 충돌함',
 'validation_error':'ETF 판단의 필수 항목을 검증할 수 없음',
}

class EvidenceIssue(ValueError):
 def __init__(self,code):self.code=code

def neutral_probe(evidence,target):
    """Select only a consistent, cited five-session trend when all AI odds are 50%."""
    sources={s.get('id'):s for s in evidence.get('sources',[]) if isinstance(s,dict)}
    if set(sources)!={'M901','M902','M903'}:return None
    changes={};dates=set()
    try:
        for sid,code in (('M901','KPI200'),('M902','069500'),('M903','114800')):
            source=sources[sid];body=json.loads(source['text']);closes=body['closes']
            if body['code']!=code or len(closes)<6:return None
            if any(not finite(r['close']) or r['close']<=0 for r in closes):return None
            if [r['date'] for r in closes]!=sorted({r['date'] for r in closes}):return None
            date=closes[-1]['date']
            if date!=source['date'] or date>=target:return None
            dates.add(date)
            changes[code]=100*(closes[-1]['close']/closes[-6]['close']-1)
    except (KeyError,TypeError,ValueError,IndexError):return None
    if len(dates)!=1:return None
    index,long,short=(changes[k] for k in ('KPI200','069500','114800'))
    if index>=NEUTRAL_TREND_PCT and long>=NEUTRAL_TREND_PCT and short<=-NEUTRAL_TREND_PCT:code='069500'
    elif index<=-NEUTRAL_TREND_PCT and short>=NEUTRAL_TREND_PCT and long<=-NEUTRAL_TREND_PCT:code='114800'
    else:return None
    return {'code':code,'basis_date':dates.pop(),'source_ids':['M901','M902','M903'],
            'five_session_return_pct':{k:round(v,4) for k,v in changes.items()},
            'threshold_pct':NEUTRAL_TREND_PCT}

def collect(now,target,fetcher=None):
    from data.kr_daily import fetch_daily
    fetcher=fetcher or fetch_daily
    out={'status':'held','collected_at':now.isoformat(),'target_date':target,'sources':[],
         'futures_note':pending_snapshot()['note']}
    try:
        dates=[]
        for i,(code,name) in enumerate(INSTRUMENTS.items(),901):
            rows=[r for r in fetcher(code,'index' if code=='KPI200' else 'stock') if r['date']<target and r['date']<=str(now.date())]
            if len(rows)<6:raise EvidenceIssue('history_incomplete')
            rows=rows[-10:]
            if any(not finite(r.get('close')) or r['close']<=0 for r in rows):raise EvidenceIssue('quote_invalid')
            day=datetime.fromisoformat(rows[-1]['date']).replace(tzinfo=now.tzinfo)
            if not timedelta(0)<=now-day<=timedelta(days=7):raise EvidenceIssue('history_stale')
            dates.append(rows[-1]['date'])
            out['sources'].append({'id':'M'+str(i),'kind':'M','name':name,'source':'네이버 증권 · 대상 거래일 이전 종가','date':rows[-1]['date'],
             'text':json.dumps({'code':code,'name':name,'closes':rows,'five_session_return_pct':100*(rows[-1]['close']/rows[-6]['close']-1)},ensure_ascii=False)})
        if len(set(dates))!=1:raise EvidenceIssue('session_mismatch')
        out['status']='ok'
    except EvidenceIssue as exc:
        out['sources']=[];out['reason_code']=exc.code;out['instrument']=code if exc.code!='session_mismatch' and code in INSTRUMENTS else None
    except Exception:
        # Never persist exception text: provider errors can include credential-bearing URLs.
        out['sources']=[];out['reason_code']='source_fetch_failed';out['instrument']=code if code in INSTRUMENTS else None
    return out

def validate(raw,evidence,now,target):
    from rules.citations import check_points
    held={'schema':CURRENT_SCHEMA,'status':'held','target_date':target,'generated_at':now.isoformat(),'entry_window':'09:00–09:15','exit_time':'15:20',
          'note':'ETF 별도 판단 또는 자료 검증 미통과 · 신규 매매 보류','forecasts':[],'weight':0,'selected_code':None,'sources':[],'futures_note':evidence.get('futures_note','선물지수 직접 자료 미수집')}
    scope='ETF 전용 과거시세·자료 ID·확률 형식 검증'
    def reject(reason_code,instrument=None):
        reason=QA_REASONS[reason_code]
        qa={'passed':False,'scope':scope,'reason_code':reason_code,'reason':reason,
            'evidence_status':evidence.get('status','unknown'),
            'evidence_reason_code':evidence.get('reason_code'),
            'source_count':len(evidence.get('sources') or [])}
        if instrument in INSTRUMENTS:qa['instrument']=instrument
        return {**held,'note':'ETF 전용 QA 미통과 · '+reason+' · 신규 매매 보류','etf_qa':qa}
    try:
        if evidence.get('status')!='ok':return reject('evidence_unavailable',evidence.get('instrument'))
        if raw is None:return reject('model_plan_missing')
        if not isinstance(raw,dict):return reject('model_plan_invalid')
        rows=raw.get('forecasts')
        if not isinstance(rows,list) or len(rows)!=3:return reject('forecast_count')
        checked=[];seen=set()
        for row in rows:
            if not isinstance(row,dict) or not {'code','up_probability','reasoning'}<=row.keys():return reject('forecast_row_invalid')
            code=row['code'];prob=row['up_probability'];points=row['reasoning']
            if code not in INSTRUMENTS:return reject('instrument_invalid')
            if code in seen:return reject('instrument_duplicate',code)
            if not finite(prob) or not 0<=prob<=100:return reject('probability_invalid',code)
            if not isinstance(points,list) or not 1<=len(points)<=3 or any(not isinstance(p,str) or len(p)>500 for p in points):return reject('reasoning_invalid',code)
            own='M'+str(901+list(INSTRUMENTS).index(code))
            checks=check_points(points,evidence['sources'])
            if any(c['status']!='ok' for c in checks):return reject('citation_invalid',code)
            if not any(own in c['ids'] for c in checks):return reject('own_source_missing',code)
            seen.add(code);checked.append({'code':code,'name':INSTRUMENTS[code],'up_probability':prob,'reasoning':points,'horizon':'대상일 09:00–09:15 실제 진입 → 15:20 청산'})
        candidates=sorted([r for r in checked if r['code']!='KPI200' and r['up_probability']>50],key=lambda r:r['up_probability'],reverse=True)
        # Both opposite funds positive is an unresolved contradiction, not two buys.
        if len(candidates)>1:return reject('opposed_etfs_positive')
        # (V3.31) 지수(KPI200) 방향과 고른 ETF 방향이 어긋나면(예: 지수 65% 상승인데 인버스 55%) 매수하지 않는다.
        # 장중 재검토·레버리지 검증과 같은 기준(KODEX200 은 지수>50, 인버스는 지수<50).
        if candidates:
            kpi=next((r['up_probability'] for r in checked if r['code']=='KPI200'),None)
            pick=candidates[0]['code']
            if kpi is None or (pick=='069500' and not kpi>50) or (pick=='114800' and not kpi<50):
                return reject('index_direction_conflict',pick)
        selected=candidates[0]['code'] if candidates else None
        probability=candidates[0]['up_probability'] if selected else None
        weight=(WEIGHTS_BY_SCHEMA[CURRENT_SCHEMA][0] if probability<60 else WEIGHTS_BY_SCHEMA[CURRENT_SCHEMA][1]) if selected else 0
        selection_basis='model_probability' if selected else 'cash'
        probe=None
        if not selected and all(r['up_probability']==50 for r in checked):
            probe=neutral_probe(evidence,target)
            if probe:
                selected=probe['code'];weight=NEUTRAL_PROBE_WEIGHT;selection_basis='neutral_probe'
        return {**held,'status':'approved','etf_qa':{'passed':True,'scope':scope,'reason_code':'passed','reason':'ETF 3종 자료·확률·근거 ID 검증 통과','evidence_status':'ok','source_count':len(evidence['sources'])},
                'forecasts':checked,'selected_code':selected,'weight':weight,'selection_basis':selection_basis,
                'neutral_probe':probe,
                'sources':[{k:s[k] for k in ('id','name','source','date')} for s in evidence['sources']],
                'note':('AI 3종 상승확률 50% 중립 · 전일 기준 5거래일 지수·ETF 일치 추세로 단기자금 5% 규칙 기반 모의 탐색 · AI 방향 적중률 제외 · 시세/위험 한도 추가 확인' if probe else
                        ('ETF별 근거 검증 통과 · 50% 초과~60% 미만 제한적 모의진입 · 시세/위험 한도 추가 확인' if probability<60 else
                         'ETF별 근거 검증 통과 · 시세/진입시간/위험 한도 추가 확인') if selected else 'ETF 상승 확률 50% 초과 후보·중립 탐색 근거 없음 · 현금 계획')}
    except (KeyError,TypeError,ValueError):return reject('validation_error')
