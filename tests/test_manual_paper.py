"""Synthetic local paper orders only; no network, user ledger or brokerage."""
from copy import deepcopy
from datetime import datetime, timedelta
import json

import pytest

from data.sessions import KST
from execution.manual_paper import ManualOrderError, prepare
from execution.split_portfolio import fresh, summary_at, tick, valuation_audit
from execution.etf_contract import public_summary

NOW = datetime(2026, 9, 30, 9, 5, tzinfo=KST)


def state():
    result = fresh(NOW)
    result['short_policy'] = 'etf_v2'
    return result


def quotes(now=NOW, long=10_000, inverse=4_000):
    return {code:{'date':str(now.date()), 'value':price, 'market_status':'OPEN',
                  'observed_at':now.isoformat(), 'traded_at':now.isoformat()}
            for code,price in (('069500',long),('114800',inverse))}


def test_manual_preview_is_pure_and_buy_sell_pnl_is_separate_from_ai_accuracy():
    original = state()
    before = deepcopy(original)
    bought, fill = prepare(original, 'buy', '069500', .05, quotes(), NOW)
    assert original == before
    assert fill['side'] == 'buy' and fill['origin'] == 'manual'
    assert 500_000 < fill['qty'] * 10_000 <= 600_000
    assert bought['active_short_cycle']['selection_basis'] == 'manual'
    assert valuation_audit(bought, NOW)['status'] == 'ok'
    assert summary_at(bought, NOW)['trades'] == 1
    with pytest.raises(ManualOrderError):
        prepare(bought, 'buy', '114800', .05, quotes(NOW+timedelta(minutes=5)), NOW+timedelta(minutes=5))
    later = NOW + timedelta(minutes=5)
    sold, exit_fill = prepare(bought, 'sell', '069500', None, quotes(later,long=10_100), later)
    assert exit_fill['origin'] == 'manual' and exit_fill['realized_pnl'] > 0
    assert sold['short_cycles'][0]['selection_basis'] == 'manual'
    assert sold['short_cycles'][0]['scores'] == []
    assert public_summary(sold)['manual_closed_cycles'] == 1
    assert abs(sold['sleeves']['short']['realized_pnl']-exit_fill['realized_pnl']) < .0001
    assert valuation_audit(sold, later)['status'] == 'ok'
    with pytest.raises(ManualOrderError, match='하루 한 번'):
        prepare(sold, 'buy', '069500', .05, quotes(later+timedelta(minutes=5)), later+timedelta(minutes=5))


def test_manual_buy_preserves_market_quote_and_risk_gates():
    for weight in (0, .20, True, float('nan')):
        with pytest.raises(ManualOrderError):
            prepare(state(), 'buy', '069500', weight, quotes(), NOW)
    stale = quotes()
    stale['069500']['traded_at'] = (NOW-timedelta(minutes=16)).isoformat()
    with pytest.raises(ManualOrderError, match='시세'):
        prepare(state(), 'buy', '069500', .10, stale, NOW)
    paused = state()
    paused['sleeves']['short']['stop_day'] = str(NOW.date())
    with pytest.raises(ManualOrderError, match='손실 제한'):
        prepare(paused, 'buy', '069500', .05, quotes(), NOW)
    late = NOW.replace(hour=15,minute=1)
    with pytest.raises(ManualOrderError, match='15:00'):
        prepare(state(), 'buy', '069500', .05, quotes(late), late)
    closed = NOW.replace(hour=15,minute=31)
    with pytest.raises(ManualOrderError, match='정규장'):
        prepare(state(), 'buy', '069500', .05, quotes(closed), closed)


def test_manual_holding_follows_existing_scheduled_exit_without_ai_score():
    opened, _ = prepare(state(), 'buy', '114800', .10, quotes(), NOW)
    close = NOW.replace(hour=15,minute=20)
    after = tick(opened, None, quotes(close,inverse=4_100), close)
    assert after['trades'][-1]['side'] == 'sell'
    assert after['short_cycles'][0]['selection_basis'] == 'manual'
    assert after['short_cycles'][0]['scores'] == []
    assert after['short_cycles'][0]['horizon_matched'] is False


def test_local_api_requires_preview_confirmation_and_rejects_replay(tmp_path, monkeypatch):
    from config import Config
    from web import server
    from execution import manual_paper
    from tools.paper_portfolio import write
    path = tmp_path / 'paper.json'
    write(path, state())
    monkeypatch.setattr(Config, 'PAPER_PORTFOLIO_PATH', str(path))
    monkeypatch.setattr(Config, 'WEB_READONLY', False)
    monkeypatch.setattr(manual_paper, 'fetch_quotes', lambda ledger, code:(quotes(), NOW))
    client = server.app.test_client()
    host = f'127.0.0.1:{Config.WEB_PORT}'
    headers = {'Host':host,'Origin':f'http://{host}','X-TA-Token':server.SESSION_TOKEN}
    payload = {'side':'buy','code':'069500','weight':.05}
    assert client.post('/api/paper/manual/preview', json=payload, headers={'Host':host}).status_code == 403
    assert client.post('/api/paper/manual/preview', json={**payload,'weight':.40}, headers=headers).status_code == 400
    preview = client.post('/api/paper/manual/preview', json=payload, headers=headers)
    assert preview.status_code == 200
    assert json.loads(path.read_text(encoding='utf-8'))['trades'] == []
    token = preview.json['preview_id']
    assert client.post('/api/paper/manual/execute', json={'preview_id':token,'confirm':False}, headers=headers).status_code == 400
    executed = client.post('/api/paper/manual/execute', json={'preview_id':token,'confirm':True}, headers=headers)
    assert executed.status_code == 200
    assert executed.json['trade']['side'] == 'buy'
    assert json.loads(path.read_text(encoding='utf-8'))['trades'][0]['origin'] == 'manual'
    assert list((tmp_path/'.review-backups').glob('manual-*.json'))
    assert client.post('/api/paper/manual/execute', json={'preview_id':token,'confirm':True}, headers=headers).status_code == 409


def test_local_api_rejects_ledger_change_and_readonly(tmp_path, monkeypatch):
    from config import Config
    from web import server
    from execution import manual_paper
    from tools.paper_portfolio import write
    path = tmp_path / 'paper.json'
    write(path, state())
    monkeypatch.setattr(Config, 'PAPER_PORTFOLIO_PATH', str(path))
    monkeypatch.setattr(Config, 'WEB_READONLY', False)
    monkeypatch.setattr(manual_paper, 'fetch_quotes', lambda ledger, code:(quotes(), NOW))
    client = server.app.test_client()
    host = f'127.0.0.1:{Config.WEB_PORT}'
    headers = {'Host':host,'Origin':f'http://{host}','X-TA-Token':server.SESSION_TOKEN}
    preview = client.post('/api/paper/manual/preview', json={'side':'buy','code':'069500','weight':.05}, headers=headers)
    assert preview.status_code == 200
    changed = state()
    changed['last_tick'] = (NOW-timedelta(minutes=1)).isoformat()
    write(path, changed)
    denied = client.post('/api/paper/manual/execute', json={'preview_id':preview.json['preview_id'],'confirm':True}, headers=headers)
    assert denied.status_code == 409 and not json.loads(path.read_text(encoding='utf-8'))['trades']
    monkeypatch.setattr(Config, 'WEB_READONLY', True)
    assert client.post('/api/paper/manual/preview', json={'side':'buy','code':'069500','weight':.05}, headers=headers).status_code == 403
