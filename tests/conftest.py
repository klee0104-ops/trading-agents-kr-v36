import os
import tempfile
# 테스트에서는 네이버 뉴스/시총 목록 등 네트워크 수집을 건너뛴다
os.environ.setdefault("TA_NO_NEWS", "1")
# (V3.28) pytest 를 직접 실행해도 사용자 실제 원장·기록(계좌/예측/교훈/리서치)을 읽거나 쓰지 않도록
# 모든 데이터 경로를 임시 폴더로 고정한다 (verify_harness 가 이미 지정했으면 그 값을 쓴다).
_TA_TMP = tempfile.mkdtemp(prefix="ta-pytest-")
for _k, _f in {"MEMORY_DB_PATH": "memory.json", "LESSONS_PATH": "lessons.json", "PAPER_ACCOUNT_PATH": "paper.json",
               "PAPER_PORTFOLIO_PATH": "paper_portfolio.json", "LONG_RESEARCH_PATH": "long_research.json",
               "REPORT_ARCHIVE_PATH": "research_reports.json", "LAST_OUTCOME_PATH": "outcome.json",
               "EVENT_ARCHIVE_DB_PATH": "events.db"}.items():
    os.environ.setdefault(_k, os.path.join(_TA_TMP, _f))

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
