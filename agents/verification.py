"""
Verification 에이전트. V3.5 설계에서 기존 8개의 개별 검증 Agent(Data Verification,
Consistency Checker, Confidence Calibration, Historical Validation, Governance ...)를
1개로 축소한 것. 핵심 원칙은 "검증은 코드, 판단만 LLM":

1. code_verify(): Risk Manager의 LLM 판단이 Rule Engine(코드 기반 객관적 신호)이나
   과거 유사 사례(RAG)와 상충하는지를 순수 코드로 점검한다. 여기까지는 토큰 비용이 없다.
2. run_verification(): 코드 검증에서 상충이 하나도 없으면 LLM을 아예 호출하지 않고
   Risk Manager의 판단을 그대로 승인한다. 상충이 있을 때만 LLM을 불러 "어느 쪽이
   더 타당한가"를 판단하게 한다 — 판단이 필요한 순간에만 비용을 쓴다.
"""
from __future__ import annotations
from collections import Counter
from typing import Optional

from agents.base import LLMClient
from rules.engine import MarketAnalysis, RISK_ORDER

VERIFICATION_SYSTEM_PROMPT = """당신은 검증(Verification) 에이전트입니다.
Risk Manager의 판단이 규칙엔진(코드로 계산된 객관적 신호)이나 과거 유사 사례와
상충할 때만 호출됩니다. 상충 내용을 검토해서 어느 쪽이 더 타당한지 판단하고,
최종 리스크 수준을 결정하세요. 규칙엔진은 오탐도 있을 수 있으니 맹신하지 말고,
토론 맥락과 함께 균형 있게 판단하세요.
반드시 JSON만 출력: {"verification_passed": true 또는 false, "final_risk_level": "낮음/보통/높음", "notes": "..."}"""


def code_verify(risk_assessment: dict, market_analysis: MarketAnalysis,
                 similar_cases: list) -> dict:
    conflicts: list[str] = []

    rule_risk = market_analysis.risk_level_hint
    llm_risk = risk_assessment.get("risk_level", "보통")
    if RISK_ORDER.get(llm_risk, 1) < RISK_ORDER.get(rule_risk, 1):
        conflicts.append(
            f"규칙엔진은 리스크를 '{rule_risk}'로 판단했으나 Risk Manager는 "
            f"'{llm_risk}'로 더 낮게 판단함 (트리거된 규칙: {', '.join(market_analysis.risk_flags) or '없음'})"
        )

    if similar_cases:
        directions = [c.direction for c in similar_cases]
        dominant, count = Counter(directions).most_common(1)[0]
        dominant_ratio = count / len(directions)
        leaning = risk_assessment.get("leaning")
        if dominant_ratio >= 0.7 and leaning and leaning != dominant:
            conflicts.append(
                f"과거 유사 사례 {round(dominant_ratio * 100)}%가 '{dominant}' 방향이었으나 "
                f"Risk Manager는 '{leaning}' 방향으로 판단함"
            )

    return {
        "conflicts": conflicts,
        "rule_risk_level": rule_risk,
        "has_conflict": len(conflicts) > 0,
    }


def run_verification(llm: LLMClient, risk_assessment: dict, market_analysis: MarketAnalysis,
                      similar_cases: Optional[list] = None) -> dict:
    checks = code_verify(risk_assessment, market_analysis, similar_cases or [])

    if not checks["has_conflict"]:
        return {
            "verification_passed": True,
            "final_risk_level": risk_assessment.get("risk_level", "보통"),
            "notes": "규칙엔진/과거사례와 상충 없음 (LLM 호출 생략, 비용 절감)",
            "code_checks": checks,
        }

    conflict_lines = "\n".join(f"- {c}" for c in checks["conflicts"])
    user_prompt = f"""[Risk Manager 판단]
leaning={risk_assessment.get('leaning')}, risk_level={risk_assessment.get('risk_level')}
{risk_assessment.get('summary', '')}

[규칙엔진 자동 판단]
risk_level_hint={market_analysis.risk_level_hint}, flags={market_analysis.risk_flags}

[코드 검증에서 발견된 상충]
{conflict_lines}

위 상충을 검토해 최종 리스크 수준과 신뢰 여부를 판단하세요."""

    result = llm.complete_json(VERIFICATION_SYSTEM_PROMPT, user_prompt, max_tokens=400)
    result["code_checks"] = checks
    return result
