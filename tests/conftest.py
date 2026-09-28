import os
# 테스트에서는 네이버 뉴스/시총 목록 등 네트워크 수집을 건너뛴다
os.environ.setdefault("TA_NO_NEWS", "1")

import pytest

@pytest.fixture(autouse=True)
def harness_offline(monkeypatch):
    if os.environ.get('TA_HARNESS_OFFLINE') == '1':
        import socket
        def blocked(*args, **kwargs):
            raise RuntimeError('Network disabled by verification harness')
        monkeypatch.setattr(socket.socket, 'connect', blocked)
        monkeypatch.setattr(socket.socket, 'connect_ex', blocked)
        monkeypatch.setattr(socket, 'create_connection', blocked)
