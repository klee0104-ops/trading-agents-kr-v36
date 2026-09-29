from copy import deepcopy
from datetime import datetime
import json
from data.sessions import KST
from data.public_stock_quotes import refresh_stock_quotes


def test_quotes_are_read_only_isolated_and_fail_closed(tmp_path, monkeypatch):
    from data import kr_daily
    (tmp_path/'ticker_map_cache.json').write_text(json.dumps({'A':'000001','B':'000002'}))
    data={'core_stocks':[{'name':'A','directional_signal':'down'},{'name':'B'}],
          'watch_stocks':[{'name':'unknown'}], 'prediction':{'direction':'up'}}
    before=deepcopy(data)
    def fetch(code,*a,**k):
        if code=='000002':raise RuntimeError('offline')
        return [{'date':'2026-09-29','close':98,'prev_close':100}]
    monkeypatch.setattr(kr_daily,'fetch_daily',fetch)
    now=datetime(2026,9,29,14,tzinfo=KST)
    out=refresh_stock_quotes(data,now,tmp_path)
    assert data==before
    assert out['stock_quotes']['A']['change_pct']==-2
    assert out['stock_quotes']['A']['intraday'] is True
    assert out['stock_quotes']['B']['status']=='unavailable'
    assert out['stock_quotes']['unknown']['status']=='unavailable'
    assert refresh_stock_quotes(out,now.replace(minute=5),tmp_path)==out


def test_invalid_prices_and_old_dates_never_replace_last_good_quote(tmp_path,monkeypatch):
    from data import kr_daily
    (tmp_path/'ticker_map_cache.json').write_text('{"A":"000001"}')
    data={'core_stocks':[{'name':'A'}],'stock_quotes':{'A':{'date':'2026-09-29','value':100,'status':'ok'}}}
    now=datetime(2026,9,29,14,tzinfo=KST)
    for row in [{'date':'2026-09-29','close':float('nan'),'prev_close':100},
                {'date':'2026-09-28','close':99,'prev_close':100},
                {'date':'2026-09-30','close':99,'prev_close':100}]:
        monkeypatch.setattr(kr_daily,'fetch_daily',lambda *a,**k:[row])
        q=refresh_stock_quotes(data,now,tmp_path)['stock_quotes']['A']
        assert q['value']==100 and q['status']=='unavailable'
