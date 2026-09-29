"""Reserved futures data contract. Intentionally disabled; no I/O or orders.

A future provider must distinguish contract prices from the F-KOSPI200 index,
validate timestamps and rollover, and obtain data-use permission before use.
There is deliberately no runtime switch or credential parameter.
"""

def pending_snapshot():
    """Return unavailable data, never fabricated zero prices or evidence IDs."""
    return {
        'status': 'planned',
        'enabled': False,
        'label': '선물 데이터 연결 · 추후 오픈 예정',
        'note': '선물 데이터 연결은 추후 오픈 예정입니다. 선물 시세와 F-KOSPI200 직접 지수는 미연결이며, 인버스 ETF 가격을 선물지수 값으로 대체하지 않습니다.',
        'contract_quote': None,
        'f_kospi200_index': None,
        'sources': [],
    }
