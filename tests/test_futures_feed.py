def test_reserved_feed_cannot_be_enabled_or_supply_trade_evidence(monkeypatch):
    import socket
    from data.futures_feed import pending_snapshot
    def forbidden(*args, **kwargs):
        raise AssertionError('reserved feed must not access network')
    monkeypatch.setattr(socket, 'socket', forbidden)
    monkeypatch.setenv('FUTURES_ENABLED', 'true')
    snapshot = pending_snapshot()
    assert snapshot['enabled'] is False
    assert snapshot['status'] == 'planned'
    assert snapshot['contract_quote'] is None
    assert snapshot['f_kospi200_index'] is None
    assert snapshot['sources'] == []
    snapshot['sources'].append('fake')
    assert pending_snapshot()['sources'] == []
