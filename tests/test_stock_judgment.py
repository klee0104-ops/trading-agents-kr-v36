from copy import deepcopy
from rules.stock_judgment import validate_stocks
from agents.stock_moderator import apply_reviews

def sample():
    return {'name':'합성종목','directional_signal':'flat','assessment_status':'assessed',
            'direction_bias':'up','bias_confidence':'low','reason':'종목별 근거 설명',
            'bias_reason':'상방과 하방의 근거 비교'}

def test_flat_is_distinct_from_missing_evidence():
    for bias in ('up','down','neutral'):
        s=sample();s['direction_bias']=bias
        assert validate_stocks({'watch_stocks':[s]})['watch_stocks'][0]['assessment_status']=='assessed'
    for key in ('reason','bias_reason','direction_bias','bias_confidence','assessment_status'):
        s=sample();s.pop(key)
        assert validate_stocks({'watch_stocks':[s]})['watch_stocks'][0]['assessment_status']=='held'

def test_contradiction_and_moderator_rejection_hold_judgment():
    s=sample();s['directional_signal']='down'
    assert validate_stocks({'watch_stocks':[s]})['watch_stocks'][0]['assessment_status']=='held'
    s=sample();s['directional_signal']='up'
    r=apply_reviews({'watch_stocks':[s]},{'reviews':[{'name':s['name'],'action':'flat','reason':'근거 부족'}]})
    assert r['watch_stocks'][0]['assessment_status']=='held'

def test_alignment_missing_stock_is_held_in_new_contract():
    from agents.trader import align_watch_stocks
    r=validate_stocks(align_watch_stocks({'watch_stocks':[]},['누락종목']))
    assert r['watch_stocks'][0]['assessment_status']=='held'
