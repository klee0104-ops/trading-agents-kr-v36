"""(V3.30) 에이전트 지시문 검수 결과를 고정: 확률 50 앵커·비대칭 규칙·예시값 복사 위험·근거 미전달 재발 방지."""
from __future__ import annotations
import re
from datetime import datetime


def test_analysts_interpret_not_copy_and_technical_uses_kospi_quant():
    from agents import specialists as s
    for p in (s.TECHNICAL_PROMPT, s.MACRO_PROMPT, s.SEMI_PROMPT, s.NEWS_PROMPT):
        assert "그대로 옮겨 적지 마세요" in p and "해석" in p
    assert "[Q]" in s.TECHNICAL_PROMPT and "KOSPI 자체" in s.TECHNICAL_PROMPT
    assert "미국 주요 지수의 등락률과 이미 코드로 계산된" not in s.TECHNICAL_PROMPT


def test_trader_rules_symmetric_and_target_day():
    from agents.trader import TRADER_SYSTEM_PROMPT as t
    assert "±0.3% 이내" in t and "-0.3% 이내" not in t
    assert "대상 거래일" in t and "다음 거래일 KOSPI가 상승 마감할 확률" not in t
    assert "45~55" not in t


def test_intraday_trader_has_no_50_anchor_and_consistent_odds():
    from tools.short_intraday_review import TRADER_PROMPT, SKEPTIC_PROMPT
    assert "불확실하면 50" not in TRADER_PROMPT
    assert not re.search(r'"up_probability":\s*50', TRADER_PROMPT)
    assert "100 − KOSPI200" in TRADER_PROMPT and "0.13%" in TRADER_PROMPT
    assert "'불확실하다'는 것만으로 거절하지 마세요" in SKEPTIC_PROMPT and "approve=true:" in SKEPTIC_PROMPT


def test_intraday_reviewer_budget_not_tight():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "tools/short_intraday_review.py").read_text(encoding="utf-8")
    assert "max_tokens=500" not in src


def test_medium_example_values_are_not_copyable_numbers():
    from agents.medium_team import PROMPT
    assert "100000" not in PROMPT and '"weight":0.5' not in PROMPT and "그대로 복사하지 말고" in PROMPT


def test_risk_manager_receives_evidence_and_qa_counts():
    from agents.base import MockLLMClient
    from agents.risk_manager import run_risk_manager
    seen = {}

    class Cap(MockLLMClient):
        def complete(self, system, user, max_tokens=1500, use_web_search=False):
            seen["user"] = user
            return super().complete(system, user, max_tokens, use_web_search)
    t = [{"stance": "bull", "round": 1, "argument": "a", "evidence": ["SOX 등락 +1.10% [M2]"], "confidence": 0.6}]
    qa = [{"stance": "bull", "round": 1, "verified_count": 0, "total_count": 1, "unverified": ["SOX 등락 +1.10% [M2]"]}]
    run_risk_manager(Cap(), t, debate_qa=qa)
    assert "SOX 등락 +1.10% [M2]" in seen["user"] and "QA 확인 0/1" in seen["user"] and "미확인" in seen["user"]


def test_intraday_quote_carries_change_pct():
    from tools.paper_portfolio import parse_quote
    from data.sessions import KST
    q = parse_quote({"localTradedAt": "2026-10-22T10:00:00+09:00", "closePrice": "891.06",
                     "compareToPreviousClosePrice": "-2.40", "fluctuationsRatio": "-0.27"},
                    datetime(2026, 10, 22, 10, 0, 30, tzinfo=KST))
    assert q["change_pct"] == -0.27 and q["prev_close"] == 893.46
    q2 = parse_quote({"localTradedAt": "2026-10-22T10:00:00+09:00", "closePrice": "891.06"},
                     datetime(2026, 10, 22, 10, 0, 30, tzinfo=KST))
    assert "change_pct" not in q2
