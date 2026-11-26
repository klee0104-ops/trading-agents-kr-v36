"""Medium-team decision and selected public summary, separate from the long report."""
from datetime import datetime, timedelta

from data.long_research import finite
from rules.medium_sizing import execution_weight


def candidate_facts(state,now):
    """Reuse a recent, separately dated research batch; current observations win."""
    rows={}
    report=state.get('report') or {}
    try:
        stamp=datetime.fromisoformat(report['generated_at'])
        recent=timedelta(0)<=now-stamp<=timedelta(days=2)
    except (KeyError,TypeError,ValueError):recent=False
    if recent:
        for f in report.get('fundamentals') or []:
            if isinstance(f,dict) and isinstance(f.get('code'),str):rows[f['code']]=f
    for f in state.get('fundamentals') or []:
        if isinstance(f,dict) and isinstance(f.get('code'),str):rows[f['code']]=f
    return list(rows.values())


def _daily_bias(name):
    """아침 분석 종목 판단(core/watch) 중 이름이 같은 종목의 방향. 없으면 None."""
    if not name:
        return None
    try:
        from config import Config
        from memory.outcome_store import load_last_outcome
        fr=((load_last_outcome(Config.LAST_OUTCOME_PATH) or {}).get('outcome') or {}).get('final_result') or {}
        for s in (fr.get('core_stocks') or [])+(fr.get('watch_stocks') or []):
            if isinstance(s,dict) and s.get('name')==name:
                return s.get('direction_bias') or s.get('directional_signal')
    except Exception:  # noqa: BLE001
        return None
    return None


def second_opinion(state,code,name=None,daily=_daily_bias):
    """'agree' | 'conflict' | 'missing'"""
    stances=[s.get('stance') for r in ((state.get('report') or {}).get('roles') or []) if isinstance(r,dict)
             for s in (r.get('stocks') or []) if isinstance(s,dict) and s.get('code')==code]
    bias=daily(name)
    if 'reject' in stances or bias=='down':
        return 'conflict'
    if 'consider' in stances or bias=='up':
        return 'agree'
    return 'missing'


def decision(state,now):
    report=state.get('medium_report') or {}
    result={'schema':1,'status':'held','generated_at':report.get('generated_at'),
            'valid_until':report.get('valid_until'),'selected_code':None,'weight':0,
            'buy_below':None,'horizon_days':None,'execution_weight':None,
            'instruments':{},'reason_code':'report_unavailable'}
    catalog=state.get('catalog') or {}
    specs=catalog.get('instruments') or {}
    result['instruments']={c:{'name':s['name'],'sector':s.get('sector','Unknown')}
                           for c,s in specs.items() if isinstance(s,dict) and isinstance(s.get('name'),str)}
    try:
        generated=datetime.fromisoformat(report['generated_at'])
        expires=datetime.fromisoformat(report['valid_until'])
        if report.get('status')!='approved' or not generated<=now<=expires or now-generated>timedelta(days=2):
            return result
        news=state.get('news') or {}
        age=now-datetime.fromisoformat(news['checked_at'])
        if news.get('status')!='ok' or not timedelta(0)<=age<=timedelta(hours=2):
            result['reason_code']='news_stale';return result
        if state.get('alerts_overflow'):
            result['reason_code']='alerts_overflow';return result
        code=report.get('selected_code')
        if code is None:
            result.update(status='approved',reason_code='cash_choice');return result
        if code not in report.get('candidate_codes',[]):return result
        reviewed=set(report.get('reviewed_news_ids') or [])
        if any(n.get('id') not in reviewed and
               any(r.get('code')==code for r in n.get('relations',[]) if isinstance(r,dict))
               for n in state.get('alerts',[]) if isinstance(n,dict)):
            result['reason_code']='unreviewed_risk_news';return result
        facts={f.get('code'):f for f in candidate_facts(state,now)}
        fact=facts.get(code) or {}
        if state.get('dart_financial_required'):
            verification=fact.get('dart_verification') or {}
            if (verification.get('status') not in ('verified','verified_derived_eps') or
                verification.get('period_end')!=fact.get('period_end')):
                result['reason_code']='dart_unverified';return result
        from agents.medium_team import eligible
        if not eligible(fact):
            result['reason_code']='fundamentals_changed';return result
        if catalog:
            checked=datetime.fromisoformat(catalog['checked_at'])
            if catalog.get('status')!='ok' or not timedelta(0)<=now-checked<=timedelta(days=3):
                result['reason_code']='catalog_stale';return result
        # (V3.39) 두 번째 의견: 중기 분석가 한 명의 판단만으로 사지 않는다(삼성전자 2회 −12% 손절, −18.8만).
        # 장기팀 기업가치 분석가 'consider' 또는 아침 분석의 종목 판단 '상승' 중 하나가 같은 방향이어야 하고,
        # 어느 쪽이든 반대(reject/하락)면 보류.
        opinion=second_opinion(state,code,result['instruments'].get(code,{}).get('name'))
        if opinion=='conflict':
            result['reason_code']='second_opinion_conflict';return result
        if opinion!='agree':
            result['reason_code']='second_opinion_missing';return result
        weight=report.get('weight');limit=report.get('buy_below');days=report.get('horizon_days')
        target=execution_weight(weight)
        if target is None or not finite(limit) or limit<=0 or type(days) is not int or not 20<=days<=120:
            return result
        result.update(status='approved',selected_code=code,weight=weight,buy_below=limit,
                      horizon_days=days,execution_weight=target,reason_code='approved')
    except (KeyError,ValueError,TypeError,OverflowError):
        return result
    return result


def public_summary(state,now):
    """Never export raw RSS headlines, full model output, or account state."""
    from rules.citations import sanitize_external
    report=state.get('medium_report') or {}
    gate=decision(state,now)
    events=(state.get('news') or {}).get('events') or []
    alerts=state.get('alerts') or []
    titles=[n.get('title','') for n in (events if isinstance(events,list) else [])+(alerts if isinstance(alerts,list) else [])
            if isinstance(n,dict)]
    def clean(value):
        line=sanitize_external(str(value or ''),500)
        for title in titles:
            if isinstance(title,str) and len(title)>10:line=line.replace(title,'[기사 제목 생략]')
        return line
    return {'team':'medium','status':gate['status'],'reason_code':gate['reason_code'],
            'generated_at':report.get('generated_at'),'valid_until':report.get('valid_until'),
            'calls':report.get('calls',0),'selected_code':gate['selected_code'],
            'selected_name':gate['instruments'].get(gate['selected_code'],{}).get('name'),
            'weight':gate['weight'],'execution_weight':gate['execution_weight'],
            'buy_below':gate['buy_below'],'horizon_days':gate['horizon_days'],
            'candidate_codes':report.get('candidate_codes',[])[:5],
            'thesis':[clean(x) for x in report.get('thesis',[])[:3]],
            'risk':[clean(x) for x in report.get('risk',[])[:3]],
            'sources':[{'id':x.get('id'),'source':x.get('source'),'date':x.get('date')}
                       for x in report.get('sources',[])[:30] if isinstance(x,dict)]}
