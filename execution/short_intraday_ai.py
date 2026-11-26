"""Bounded, cited intraday ETF re-review. No order or provider calls."""
from datetime import datetime, timedelta, time
import math

from rules.citations import check_points

CODES = ('KPI200', '069500', '114800')
WEIGHT = .10
MIN_PROBABILITY = 55
EXPLORATORY_WEIGHT = .05
EXPLORATORY_MIN_PROBABILITY = 52
QA_REASONS = {
    'source_missing': 'KOSPI200·정방향·인버스 시세 근거 누락',
    'trader_format': '트레이더 응답 형식 또는 3종 확률 누락',
    'reviewer_format': '반대검토 응답 형식 누락',
    'reviewer_veto': '반대검토자가 계획을 승인하지 않음',
    'reviewer_citation': '반대검토 근거 인용 검증 실패',
    'forecast_format': '자산 코드·확률·근거 형식 검증 실패',
    'forecast_citation': '자산별 시세 근거 인용 검증 실패',
    'news_citation': '뉴스 재판단에 뉴스 출처 인용 누락',
    'direction_conflict': '정방향·인버스 ETF 동시 상승 판단',
    'trader_action_invalid': '트레이더의 모의매매 지정이 확률 방향과 일치하지 않음',
    'trader_action_citation': '소액 탐색 판단의 근거 인용이 부족함',
}


def _prob(value):
    return type(value) in (int,float) and math.isfinite(value) and 0 <= value <= 100


def _num(value):
    """(V3.31) "62", "62%", 62.0 → 62.0. 그 밖은 원래 값 그대로(검증에서 거절됨)."""
    if isinstance(value, str):
        t = value.strip().rstrip('%').strip()
        try:
            v = float(t)
        except ValueError:
            return value
        return v if math.isfinite(v) else value
    return value


def _coerce_formats(raw, review):
    """(V3.31) 숫자를 문자열로, true/false 를 문자열로 준 응답이 형식 오류로 전부 거절되던 문제.
    값의 의미는 바꾸지 않고 표기만 맞춘다 (범위·인용 검증은 그대로)."""
    if isinstance(raw, dict) and isinstance(raw.get('forecasts'), list):
        raw = dict(raw)
        raw['forecasts'] = [dict(r, up_probability=_num(r.get('up_probability'))) if isinstance(r, dict) else r
                            for r in raw['forecasts']]
    if isinstance(review, dict) and isinstance(review.get('approve'), str):
        flag = review['approve'].strip().lower()
        if flag in ('true', 'false'):
            review = dict(review, approve=(flag == 'true'))
    return raw, review


def validate(raw, review, sources, now, trigger):
    """Full QA or a separately labelled, strictly bounded paper-only probe."""
    raw, review = _coerce_formats(raw, review)
    held = {'schema':1,'status':'held','target_date':str(now.date()),
            'generated_at':now.isoformat(),'expires_at':(now+timedelta(minutes=20)).isoformat(),
            'selected_code':None,'weight':0,'selection_basis':'intraday_ai',
            'forecasts':[],'trigger':trigger,'qa':{'passed':False,'reason':'형식·자료·인용 또는 반대검토 미통과'}}
    def reject(code):
        return {**held,'qa':{'passed':False,'reason_code':code,'reason':QA_REASONS[code]}}
    if (not isinstance(sources,list) or len(sources)<3
            or not {'M901','M902','M903'} <= {s.get('id') for s in sources if isinstance(s,dict)}):
        return reject('source_missing')
    if not isinstance(raw,dict):return reject('trader_format')
    if not isinstance(review,dict):return reject('reviewer_format')
    if type(review.get('approve')) is not bool:return reject('reviewer_format')
    rows=raw.get('forecasts');review_points=review.get('reasoning')
    if not isinstance(rows,list) or len(rows)!=3:return reject('trader_format')
    if (not isinstance(review_points,list) or not 1<=len(review_points)<=3
            or any(not isinstance(p,str) or len(p)>400 for p in review_points)):
        return reject('reviewer_format')
    if any(c['status']!='ok' for c in check_points(review_points,sources)):
        return reject('reviewer_citation')
    checked=[];seen=set()
    for row in rows:
        if not isinstance(row,dict):return reject('forecast_format')
        code=row.get('code');prob=row.get('up_probability');points=row.get('reasoning')
        if (code not in CODES or code in seen or not _prob(prob)
                or not isinstance(points,list) or not 1<=len(points)<=3
                or any(not isinstance(p,str) or len(p)>400 for p in points)):
            return reject('forecast_format')
        checks=check_points(points,sources)
        own='M'+str(901+CODES.index(code))
        if any(c['status']!='ok' for c in checks) or not any(own in c['ids'] for c in checks):
            return reject('forecast_citation')
        checked.append({'code':code,'up_probability':prob,'reasoning':points});seen.add(code)
    if set(seen)!=set(CODES):return reject('forecast_format')
    if trigger=='news' and not any(any('N'==source_id[:1] for source_id in c['ids'])
                                   for row in checked for c in check_points(row['reasoning'],sources)):
        return reject('news_citation')
    probs={r['code']:r['up_probability'] for r in checked}
    if probs['069500']>50 and probs['114800']>50:return reject('direction_conflict')
    def direction(threshold):
        if probs['069500']>=threshold and probs['KPI200']>50:return '069500'
        if probs['114800']>=threshold and probs['KPI200']<50:return '114800'
        return None
    selected=direction(MIN_PROBABILITY)
    exploratory=direction(EXPLORATORY_MIN_PROBABILITY)
    action=raw.get('paper_action')
    if action is not None and action not in ('069500','114800','cash'):
        return reject('trader_action_invalid')
    # (V3.34) 확률은 55% 이상인데 cash 를 고른 경우: 트레이더 판단(cash)은 존중하되 불일치로 기록해 빈도를 추적한다.
    action_conflict=bool(action=='cash' and selected)
    if action=='cash':selected=exploratory=None
    elif action in ('069500','114800') and action!=exploratory:
        return reject('trader_action_invalid')
    source_list=[{k:s.get(k) for k in ('id','source','date')}
                 for s in sources if s.get('id') in ('M901','M902','M903') or str(s.get('id','')).startswith('N')][:8]
    if (exploratory and action==exploratory and
            (review['approve'] is False or selected is None)):
        rationale=raw.get('paper_rationale')
        if (not isinstance(rationale,list) or not 1<=len(rationale)<=3 or
                any(not isinstance(p,str) or len(p)>400 for p in rationale)):
            return reject('trader_action_citation')
        checks=check_points(rationale,sources)
        own='M902' if exploratory=='069500' else 'M903'
        cited={sid for c in checks for sid in c['ids']}
        if any(c['status']!='ok' for c in checks) or not {'M901',own}<=cited:
            return reject('trader_action_citation')
        veto=review['approve'] is False
        return {**held,'status':'experimental','selection_basis':'intraday_ai_exploratory',
                'forecasts':checked,'selected_code':exploratory,'weight':EXPLORATORY_WEIGHT,
                'trader_action':action,'sources':source_list,
                'qa':{'passed':False if veto else True,'exploratory_eligible':True,
                      'reason_code':'reviewer_veto' if veto else 'low_probability',
                      'reason':'반대검토 미승인 · 트레이더 근거 확인 후 5% 가상 탐색' if veto else
                               '상승확률 52~55% 미만 · 5% 가상 탐색'},
                'note':'검증 제한 탐색 · 단기자금 5% · 실제 체결손익만 집계 · 공식 AI 적중률 제외'}
    if review['approve'] is False:return reject('reviewer_veto')
    return {**held,'status':'approved','forecasts':checked,'selected_code':selected,
            'trader_action':action,
            'weight':WEIGHT if selected else 0,
            'sources':source_list,
            'qa':{'passed':True,'reason':'ETF 3종 근거·인용·반대검토 통과',
                  **({'action_conflict':'확률 55% 이상이지만 트레이더가 현금 선택'} if action_conflict else {})},
            'note':'장중 재판단 · '+('단기자금 10% 제한적 모의진입' if selected else '방향 근거 부족 · 현금 판단')}


def approved_plan(plan,now):
    """Execution gate independent of the morning-plan validator."""
    try:
        created=datetime.fromisoformat(plan['generated_at'])
        expires=datetime.fromisoformat(plan['expires_at'])
        rows=plan['forecasts'];code=plan['selected_code'];weight=plan['weight']
        status=plan.get('status');qa=plan.get('qa') or {}
        full=(status=='approved' and plan.get('selection_basis')=='intraday_ai' and
              qa.get('passed') is True)
        probe=(status=='experimental' and plan.get('selection_basis')=='intraday_ai_exploratory' and
               qa.get('exploratory_eligible') is True and
               qa.get('reason_code') in ('reviewer_veto','low_probability') and
               (qa.get('passed') is False if qa.get('reason_code')=='reviewer_veto' else qa.get('passed') is True))
        if (plan.get('schema')!=1 or not (full or probe) or plan.get('target_date')!=str(now.date())
                or created.tzinfo is None or expires.tzinfo is None or not created<=now<=expires
                or not time(9,20)<=now.time().replace(tzinfo=None)<time(15,0)
                or len(rows)!=3 or {r['code'] for r in rows}!=set(CODES)
                or any(not _prob(r['up_probability']) for r in rows)
                or not {'M901','M902','M903'} <= {s.get('id') for s in plan.get('sources',[]) if isinstance(s,dict)}):
            return None
        probs={r['code']:r['up_probability'] for r in rows}
        if probs['069500']>50 and probs['114800']>50:return None
        threshold=EXPLORATORY_MIN_PROBABILITY if probe else MIN_PROBABILITY
        expected=('069500' if probs['069500']>=threshold and probs['KPI200']>50 else
                  '114800' if probs['114800']>=threshold and probs['KPI200']<50 else None)
        if full and plan.get('trader_action')=='cash':expected=None
        if (code!=expected or type(weight) not in (int,float) or
                weight!=(EXPLORATORY_WEIGHT if probe else WEIGHT if code else 0) or
                (probe and (code is None or plan.get('trader_action')!=code))):return None
        return plan
    except (KeyError,TypeError,ValueError):
        return None
