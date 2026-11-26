"""(V3.31) 하락 측 근거 0건 사고(10/22 등 15일 중 7일) 재발 방지 + 코드 전수 검토에서 나온 형식·경계 오류 고정."""
from __future__ import annotations
import json
import pytest

from agents.base import LLMClient

REG = [
    {"id": "Q5", "kind": "Q", "group": "quant", "text": "이평 20/60/120 = 6482.64/6584.81/7172.62 → 역배열, 종가는 20일선 아래", "source": "s", "date": "", "refs": []},
    {"id": "Q3", "kind": "Q", "group": "quant", "text": "MACD(12,26,9) 히스토그램 = -114.502 (전일 -135.652) → 하락 모멘텀", "source": "s", "date": "", "refs": []},
    {"id": "N23", "kind": "N", "group": "news", "text": "SK하이닉스, 40조 자사주 매입 종료…증시 ‘수급 공백’ 우려 - 조선비즈", "source": "Chosunbiz", "date": "", "refs": []},
    {"id": "M4", "kind": "M", "group": "us", "text": "SOX 등락 +1.97%, 종가 11505.56", "source": "s", "date": "", "refs": []},
]
EXACT = ["이평 20/60/120 = 6482.64/6584.81/7172.62 → 역배열, 종가는 20일선 아래 [Q5]",
         "MACD(12,26,9) 히스토그램 = -114.502 (전일 -135.652) → 하락 모멘텀 [Q3]",
         "SK하이닉스, 40조 자사주 매입 종료…증시 ‘수급 공백’ 우려 - 조선비즈 (출처: Chosunbiz) [N23]"]
PARA = ["SOX가 1.97% 올라 반도체 강세 [M4]", "반도체 지수 상승폭 +1.97% 확인 [M4]"]


class Seq(LLMClient):
    def __init__(self, *texts):
        self.texts = list(texts); self.calls = 0
    def complete(self, system, user, max_tokens=1500, use_web_search=False):
        self.calls += 1
        return self.texts.pop(0) if self.texts else "{}"


def _turn(ev, stance="bear"):
    return [{"stance": stance, "round": 1, "argument": "x", "evidence": ev}]


def _run(llm, ev, reg=REG):
    from agents.qa_verifier import verify_debate_evidence
    return verify_debate_evidence(llm, _turn(ev), {"summary": "", "_registry": reg})[0]


def test_exact_registry_lines_are_code_verified_without_llm():
    llm = Seq()
    r = _run(llm, EXACT)
    assert llm.calls == 0 and r["verified_count"] == 3 and r["code_verified"] == 3 and r["qa_status"] == "ok"


def test_code_verification_does_not_accept_changed_numbers_or_ids():
    from agents.qa_verifier import code_verify_evidence
    assert not code_verify_evidence("MACD(12,26,9) 히스토그램 = -114.5 (전일 -135.652) → 하락 모멘텀 [Q3]", REG)
    assert not code_verify_evidence("이평 20/60/120 = 6482.64/6584.81/7172.62 → 역배열, 종가는 20일선 아래 [Q3]", REG)
    assert not code_verify_evidence("이평 20/60/120 = 6482.64/6584.81/7172.62 → 역배열, 종가는 20일선 아래", REG)
    assert not code_verify_evidence("SK하이닉스 [N23]", REG)  # 너무 짧은 뉴스 조각은 코드로 확인하지 않음


@pytest.mark.parametrize("reply", [
    json.dumps([{"no": 1, "verified": True}, {"no": 2, "verified": True}]),                      # 배열만
    json.dumps({"items": [{"no": "1", "verified": True}, {"no": "(2)", "verified": True}]}),      # 키·번호 표기 다름
    json.dumps({"results": [{"evidence": "SOX가 1.97% 올라 반도체 강세", "verified": True},       # ID 빼고 되돌려줌
                            {"evidence": "- 반도체 지수 상승폭 +1.97% 확인 [M4]", "verified": True}]}, ensure_ascii=False),
    "```json\n" + json.dumps({"results": [{"no": 1, "verified": True}, {"no": 2, "verified": True}]}) + "\n```",
])
def test_format_drift_in_qa_reply_still_maps(reply):
    r = _run(Seq(reply), PARA)
    assert r["verified_count"] == 2 and r["qa_unmatched"] == 0 and r["qa_unknown"] == []


def test_total_mapping_failure_retries_then_marks_unknown_not_unverified():
    llm = Seq(json.dumps({"foo": 1}), json.dumps({"results": []}))
    r = _run(llm, PARA + EXACT[:1])
    assert llm.calls == 2
    assert r["code_verified"] == 1 and r["verified_count"] == 1
    assert r["qa_unknown"] == PARA and r["unverified"] == [] and r["qa_status"] == "failed"


def test_retry_success_is_used():
    llm = Seq("not json at all", json.dumps({"results": [{"no": 1, "verified": True}, {"no": 2, "verified": False}]}))
    r = _run(llm, PARA)
    assert llm.calls == 2 and r["verified_count"] == 1 and r["unverified"] == [PARA[1]] and r["qa_unknown"] == []


def test_partial_reply_keeps_missing_items_unverified_and_string_true_is_not_true():
    r = _run(Seq(json.dumps({"results": [{"no": 1, "verified": "true"}]})), PARA)
    assert r["verified_count"] == 0 and r["unverified"] == PARA and r["qa_unknown"] == []


def test_code_citation_check_still_overrides_llm():
    bad = ["SOX 등락 +9.99% [M4]"]
    r = _run(Seq(json.dumps({"results": [{"no": 1, "verified": True}]})), bad)
    assert r["verified_count"] == 0 and r["unverified"] == bad


def test_unknown_is_reported_separately_downstream():
    from agents.moderator import run_moderator
    from agents.risk_manager import run_risk_manager
    from graph.rule_verification import detect_conflicts
    qa = [{"stance": "bear", "round": 1, "verified_count": 0, "total_count": 2, "unverified": [], "qa_unknown": PARA}]
    tr = _turn(PARA)

    class Cap(LLMClient):
        prompt = ""
        def complete(self, system, user, max_tokens=1500, use_web_search=False):
            Cap.prompt = user
            return json.dumps({"leaning": "up", "risk_level": "보통", "recommended_confidence": "낮음", "summary": "s",
                               "agreed_facts": [], "key_disputes": [], "noise": [], "debate_quality": "contested",
                               "stronger_side": "none"})
    run_moderator(Cap(), tr, qa)
    assert "QA 판정 불가 2건" in Cap.prompt and "자료에 없는 근거" not in Cap.prompt
    run_risk_manager(Cap(), tr, debate_qa=qa)
    assert "QA 판정 불가" in Cap.prompt and "미확인:" not in Cap.prompt
    reasons = detect_conflicts({"risk_level": "보통"}, {"kospi_prediction": {}}, qa)["reasons"]
    assert any("판정하지 못했습니다" in x for x in reasons) and not any("검증 실패" in x for x in reasons)


def test_debate_turn_format_is_forced():
    from agents.researchers import normalize_turn
    t = normalize_turn({"stance": "Bull", "argument": " a ", "evidence": "SOX [M4]"}, "bull")
    assert t["stance"] == "bull" and t["evidence"] == ["SOX [M4]"] and t["argument"] == "a"
    t = normalize_turn({"evidence": [{"x": 1}, "", "ok [Q1]"]}, "bear")
    assert t["stance"] == "bear" and t["evidence"] == ["ok [Q1]"]
    assert normalize_turn(None, "bear")["evidence"] == []


def test_risk_and_verification_outputs_are_normalized_conservatively():
    from agents.risk_manager import normalize_risk
    from agents.verification import normalize_verification
    r = normalize_risk({"leaning": "상승", "risk_level": "high", "recommended_confidence": "중간"})
    assert (r["leaning"], r["risk_level"], r["recommended_confidence"]) == ("up", "높음", "보통")
    r = normalize_risk({"leaning": "sideways", "risk_level": "??"})
    assert r["leaning"] is None and r["risk_level"] == "높음" and r["recommended_confidence"] == "낮음"
    v = normalize_verification({"verification_passed": "true", "final_risk_level": "high"}, "보통")
    assert v["verification_passed"] is False and v["final_risk_level"] == "보통"
    v = normalize_verification({"verification_passed": True, "final_risk_level": "높음"}, "보통")
    assert v["verification_passed"] is True and v["final_risk_level"] == "높음"


def test_moderation_lists_never_split_into_characters():
    from agents.moderator import normalize_moderation, build_moderation_text as moderation_text
    m = normalize_moderation({"agreed_facts": "SOX 상승", "noise": [{"a": 1}], "key_disputes": "x"}, [])
    assert m["agreed_facts"] == ["SOX 상승"] and m["key_disputes"] == [] and all(isinstance(x, str) for x in m["noise"])
    assert "S; O" not in moderation_text(m)


def test_intraday_numeric_strings_and_id_subset():
    from execution.short_intraday_ai import _coerce_formats
    raw, rev = _coerce_formats({"forecasts": [{"code": "KPI200", "up_probability": "62"}, {"up_probability": "62%"}]},
                               {"approve": "true"})
    assert raw["forecasts"][0]["up_probability"] == 62.0 and raw["forecasts"][1]["up_probability"] == 62.0
    assert rev["approve"] is True
    raw, _ = _coerce_formats({"forecasts": [{"up_probability": "높음"}]}, None)
    assert raw["forecasts"][0]["up_probability"] == "높음"  # 의미 없는 값은 그대로 → 검증에서 거절
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "execution/short_intraday_ai.py").read_text(encoding="utf-8")
    assert "} < {'M901'" not in src and src.count("not {'M901','M902','M903'} <=") == 2


def test_position_accepts_decimal_probability():
    from execution.position import position_from_probability
    a = position_from_probability(0.62, 15.0)["position"]
    b = position_from_probability(62, 15.0)["position"]
    assert a == b and a > 0


def test_stock_moderator_matches_name_variants():
    from agents.stock_moderator import apply_reviews
    res = {"core_stocks": [{"name": "삼성전자", "directional_signal": "up"}], "watch_stocks": []}
    out = apply_reviews(res, {"reviews": [{"name": "삼성전자(005930)", "action": "flat", "reason": "r"}]})
    assert out["core_stocks"][0]["directional_signal"] == "flat"
    assert out["stock_moderation"] == {"reviewed": 1, "matched": 1, "adjusted_to_flat": 1}


def test_memory_store_write_is_atomic(tmp_path):
    from memory.store import MemoryStore
    p = tmp_path / "m.json"
    st = MemoryStore(str(p))
    st._write({"a": 1})
    assert json.loads(p.read_text(encoding="utf-8")) == {"a": 1}
    assert not [f for f in tmp_path.iterdir() if ".tmp" in f.name]


def test_target_date_is_decided_once_per_run():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "graph/pipeline.py").read_text(encoding="utf-8")
    assert src.count("self._decide_target(snapshot)") == 1 and src.count("self._run_target(snapshot)") >= 3  # 대상일 결정은 한 곳


def test_etf_plan_rejects_index_direction_conflict():
    from pathlib import Path
    root = Path(__file__).resolve().parents[1]
    assert "index_direction_conflict" in (root / "data/etf_forecast.py").read_text(encoding="utf-8")
    assert "(code=='069500' and kpi>50) or (code=='114800' and kpi<50)" in (root / "execution/etf_contract.py").read_text(encoding="utf-8")


# ---- (V3.34) 장기 자료 대기·레버리지 진입 0건 수정 ----
def test_dart_eps_falls_back_to_basic_when_diluted_missing():
    from data.dart_financials import _eps
    rows = [{'sj_div': 'IS', 'account_id': 'ifrs-full_BasicAndDilutedEarningsLossPerShare', 'currency': 'KRW',
             'thstrm_amount': '1,234'}]
    assert _eps(rows, 'thstrm_amount') == 1234
    rows.append({'sj_div': 'IS', 'account_id': 'ifrs-full_DilutedEarningsLossPerShare', 'currency': 'KRW', 'thstrm_amount': '1,200'})
    assert _eps(rows, 'thstrm_amount') == 1200  # 희석 우선
    amb = [{'sj_div': 'IS', 'account_id': 'ifrs-full_BasicEarningsLossPerShare', 'currency': 'KRW', 'thstrm_amount': v}
           for v in ('100', '90')]
    assert _eps(amb, 'thstrm_amount') is None  # 값이 다르면 모호 → 사용 안 함


def test_no_borrowing_accounts_means_zero_debt():
    from data.dart_financials import _debt
    assert _debt([{'sj_div': 'BS', 'account_id': 'ifrs-full_Equity', 'currency': 'KRW', 'thstrm_amount': '1'}]) == 0.0


def test_leverage_threshold_relaxed_to_57_other_guards_kept():
    from execution import leverage_contract as lc
    assert lc.MIN_PROBABILITY == 57 and lc.MAX_TOTAL_DIRECTIONAL_PCT == .50 and lc.INDEX_MOMENTUM_PCT == .15
    src = open(lc.__file__, encoding='utf-8').read()
    assert "probs['KPI200'] < 60" not in src and "probs[INVERSE] > 50" in src


def test_intraday_trader_action_must_follow_probability():
    from tools.short_intraday_review import TRADER_PROMPT
    assert "55 이상을 쓰고 cash를 고르지 마세요" in TRADER_PROMPT and "0.13%" in TRADER_PROMPT
