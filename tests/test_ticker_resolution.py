"""
resolve_code()의 별칭(alias) 매핑 회귀 테스트.

"네이버"(공식 상장명 NAVER), "포스코"(공식 상장명 POSCO홀딩스)처럼 LLM이 흔히
쓰는 한글명과 KRX 공식 상장명이 다른 경우, 예전에는 조용히 실패해서 해당 종목의
예측이 검증 통계에서 영구히 빠지는 버그가 있었다. 이 테스트는 그 회귀를 막는다.
"""
import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest
from data.price_history import resolve_code, _NAME_ALIASES

@pytest.fixture(autouse=True)
def ticker_fixture(monkeypatch):
    # Alias resolution is a unit test; do not depend on a personal cache or KRX.
    from data import price_history
    mapping = {'삼성전자': '005930', 'NAVER': '035420', 'POSCO홀딩스': '005490',
               'LG에너지솔루션': '373220', '삼성바이오로직스': '207940', 'SK바이오사이언스': '302440'}
    monkeypatch.setattr(price_history, '_load_ticker_map', lambda force_refresh=False: mapping)


@pytest.mark.parametrize("name", list(_NAME_ALIASES.keys()))
def test_known_alias_names_resolve(name):
    """별칭 목록에 등록된 이름은 네트워크 없이(캐시만으로) 반드시 해석되어야 한다."""
    code = resolve_code(name)
    assert code and code.isdigit() and len(code) == 6


def test_exact_krx_name_still_works():
    assert resolve_code("삼성전자") == "005930"


def test_numeric_code_passthrough():
    assert resolve_code("005930") == "005930"


def test_unknown_name_raises_value_error():
    with pytest.raises(ValueError):
        resolve_code("존재하지않는가상의종목명XYZ123")
