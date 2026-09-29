from copy import deepcopy
from datetime import datetime
import json
import pytest
from tools.refresh_public_quotes import merge_quote, run
from data.sessions import KST

NOW = datetime(2026, 9, 29, 14, 0, tzinfo=KST)
BASE = {'kospi_history': {'dates': ['2026-09-25','2026-09-28'], 'closes': [2700, 2800]},
        'prediction': {'direction': 'down', 'up_probability': 38},
        'records': [{'target_date': '2026-09-29', 'verified': False}], 'paper': {'cash': 1000},
        'analysis_at': '2026-09-29T08:00', 'generated_at': '2026-09-29T08:01'}
ROW = {'date': '2026-09-29', 'close': 2828, 'prev_close': 2800, 'traded_at': '2026-09-29T13:59:00'}


def test_only_quotes_change_and_same_day_replaces_not_appends():
    original = deepcopy(BASE)
    data = merge_quote(BASE, [ROW], NOW)
    assert BASE == original
    for key in ('prediction','records','paper','analysis_at','generated_at'):
        assert data[key] == BASE[key]
    assert data['public_quote']['intraday'] is True
    assert data['public_quote']['change_pct'] == 1
    second = merge_quote(data, [{**ROW, 'close': 2801}], NOW)
    assert len(second['kospi_history']['dates']) == 3
    assert second['kospi_history']['closes'][-1] == 2801
    assert merge_quote(data, [ROW], NOW.replace(minute=5)) == data


@pytest.mark.parametrize('change', [{'close': float('nan')}, {'close': 0}, {'prev_close': 0},
                                  {'close': True}, {'date': '2026-09-30'}, {'traded_at': 'bad'}])
def test_invalid_quote_rejected(change):
    with pytest.raises(ValueError): merge_quote(BASE, [{**ROW, **change}], NOW)


def test_old_day_not_presented_as_intraday_and_regression_rejected():
    old = {**ROW, 'date':'2026-09-28', 'traded_at':'2026-09-28T15:30:00'}
    assert merge_quote(BASE,[old],NOW)['public_quote']['intraday'] is False
    data=merge_quote(BASE,[ROW],NOW)
    with pytest.raises(ValueError): merge_quote(data,[old],NOW)


def prepare(tmp_path, monkeypatch):
    from web.export_site import render_fragment
    from security import public_report
    from data import kr_daily, sessions, kr_flow
    (tmp_path/'site').mkdir()
    (tmp_path/'sharing_public.json').write_text('{"enabled":true}')
    path=tmp_path/'site/index.html'
    path.write_text('<html><head></head><body>'+render_fragment(BASE)+'</body></html>',encoding='utf-8')
    monkeypatch.setattr(public_report,'configured_secrets',lambda: [])
    monkeypatch.setattr(kr_daily,'fetch_daily',lambda *a,**k:[ROW])
    monkeypatch.setattr(sessions,'kst_now',lambda:NOW)
    monkeypatch.setattr(kr_flow,'fetch_foreign_net_buy_naver',lambda *a: None)
    return path


def test_publish_failure_restores_original_report(tmp_path,monkeypatch):
    from web import publish_site
    path=prepare(tmp_path,monkeypatch)
    before=path.read_bytes()
    def fail(*a): raise RuntimeError('network')
    monkeypatch.setattr(publish_site,'publish_if_enabled',fail)
    with pytest.raises(RuntimeError): run(tmp_path)
    assert path.read_bytes()==before


def test_unchanged_quote_does_not_publish_again(tmp_path,monkeypatch):
    from web import publish_site
    prepare(tmp_path,monkeypatch)
    calls=[]
    monkeypatch.setattr(publish_site,'publish_if_enabled',lambda p: calls.append(p) or {'changed':True})
    assert run(tmp_path)=='published'
    assert run(tmp_path)=='unchanged'
    assert len(calls)==1


def test_collection_failure_and_concurrent_export_preserve_report(tmp_path,monkeypatch):
    from data import kr_daily
    path=prepare(tmp_path,monkeypatch)
    before=path.read_bytes()
    def fail(*a,**k): raise RuntimeError('offline')
    monkeypatch.setattr(kr_daily,'fetch_daily',fail)
    with pytest.raises(RuntimeError): run(tmp_path)
    assert path.read_bytes()==before
    def concurrent(*a,**k): path.write_bytes(b'newer export'); return [ROW]
    monkeypatch.setattr(kr_daily,'fetch_daily',concurrent)
    with pytest.raises(RuntimeError): run(tmp_path)
    assert path.read_bytes()==b'newer export'


def test_live_flow_does_not_rewrite_analysis_inputs_and_failure_is_explicit(monkeypatch):
    from tools.refresh_public_quotes import refresh_flow
    from data import kr_flow
    data={**deepcopy(BASE),'inputs':{'kr_flow':{'foreign_net_krw_bn':0}}}
    monkeypatch.setattr(kr_flow,'fetch_foreign_net_buy_naver',lambda *a:{'date':'2026-09-29','foreign_net_krw_bn':-1234})
    fresh=refresh_flow(data,NOW)
    assert fresh['inputs']==data['inputs']
    assert fresh['public_flow']['foreign_net_krw_bn']==-1234
    assert refresh_flow(fresh,NOW.replace(minute=5))==fresh
    monkeypatch.setattr(kr_flow,'fetch_foreign_net_buy_naver',lambda *a:None)
    failed=refresh_flow(fresh,NOW)
    assert failed['public_flow']==fresh['public_flow']
    assert failed['public_flow_status']=='unavailable'


def test_only_complete_scored_sample_can_restore_performance_dates(monkeypatch):
    from tools.refresh_public_quotes import refresh_flow
    from data import kr_flow
    monkeypatch.setattr(kr_flow,'fetch_foreign_net_buy_naver',lambda *a:None)
    d={'returns':{'days':1,'ai':{'total_return_pct':2}},'records':[
        {'target_date':'2026-09-28','verified':True,'actual_change_pct':1},
        {'target_date':'2026-09-29','verified':False,'actual_change_pct':2}]}
    assert refresh_flow(d,NOW)['returns']['dates']==['2026-09-28']
    d['returns']['days']=10
    assert 'dates' not in refresh_flow(d,NOW)['returns']
