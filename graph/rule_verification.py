"""
Rule Verification — 순수 Python, LLM 미사용.

V3.5 설계 문서의 핵심 아이디어를 반영: "검증은 계산(Rule)과 설명(Reasoning) 두
단계로 분리한다." 지금까지 QA 검증팀(agents/qa_verifier.py)은 매번 LLM을 불러서
상위 검증을 했는데, 사실 "명백한 논리적 충돌"의 상당수는 그냥 if문으로 잡을 수
있다. 이 모듈이 그 역할이다.

흐름:
    detect_conflicts() [여기, 무료] → 충돌 있으면만 → verify_final_result() [LLM, 유료]

충돌이 없으면 LLM 상위 검증 자체를 건너뛰어서 토큰을 아낀다.
"""
from __future__ import annotations


# 리스크 수준 문자열을 비교 가능한 순서로 매핑 (낮음 < 보통 < 높음)
_LEVEL_ORDER = {"낮음": 0, "보통": 1, "높음": 2}


def detect_conflicts(risk_assessment: dict, final_result: dict,
                      debate_qa: list[dict] | None = None, expected_watch: int = 10) -> dict:
    """
    명백한 논리적 충돌을 규칙 기반으로 탐지한다.
    반환: {"conflict": bool, "reasons": [문자열 목록]}
    """
    reasons: list[str] = []

    pred = final_result.get("kospi_prediction", {})
    trader_confidence = pred.get("confidence")
    risk_level = risk_assessment.get("risk_level")
    risk_leaning = risk_assessment.get("leaning") or risk_assessment.get("direction")
    trader_direction = pred.get("direction")

    # 규칙 1: 리스크가 "높음"인데 Trader가 "높음" 확신도를 낸 경우.
    # (리스크가 크다는 건 불확실성이 크다는 뜻인데, 그런데도 확신을 강하게 갖는 건
    #  둘 중 하나가 서로를 반영 안 했을 가능성이 크다 — V3.5 문서의
    #  "Risk(HIGH) + Trader(BUY)" 예시와 같은 패턴)
    if risk_level == "높음" and trader_confidence == "높음":
        reasons.append(f"리스크 매니저는 '높음' 리스크로 판단했는데 Trader는 '높음' 확신도를 냈습니다.")

    # 규칙 2: 리스크 매니저가 명시적으로 판단한 방향(leaning)과 Trader의 최종 방향이
    # 정반대인데, 리스크 수준까지 높다면 근거 없이 뒤집었을 위험이 크다.
    if risk_leaning and trader_direction and risk_leaning != trader_direction and risk_level == "높음":
        reasons.append(
            f"리스크 매니저 판단 방향({risk_leaning})과 Trader의 최종 방향({trader_direction})이 "
            f"반대인데, 리스크 수준도 '높음'입니다."
        )

    # 규칙 3: 스키마 정합성 — core_stocks 2개, watch_stocks 10개 고정 여부.
    # 이건 "논리 충돌"이 아니라 "출력 규격 위반"이지만, 사람이 나중에 원인 찾느라
    # 헤매지 않도록 여기서 같이 잡아준다 (역시 LLM 없이 코드로 가능).
    core_count = len(final_result.get("core_stocks", []))
    watch_count = len(final_result.get("watch_stocks", []))
    if core_count != 2:
        reasons.append(f"core_stocks가 2개가 아니라 {core_count}개입니다 (삼성전자/SK하이닉스 고정 위반).")
    if watch_count != expected_watch:
        reasons.append(f"watch_stocks가 {expected_watch}개가 아니라 {watch_count}개입니다.")

    # 규칙 4: 하위 QA(토론 근거 검증)에서 이미 검증 실패가 나왔다면, 상위 결론도
    # 오염됐을 가능성이 있으니 LLM 상위 검증을 건너뛰지 않는다.
    if debate_qa:
        unverified_total = sum(len(r.get("unverified", [])) for r in debate_qa)
        if unverified_total > 0:
            reasons.append(f"토론 단계에서 근거 검증 실패가 {unverified_total}건 있었습니다.")

    return {"conflict": len(reasons) > 0, "reasons": reasons}
