"""
리스크 매니저 에이전트. Bull/Bear 토론 전체를 검토해서 최종 방향에 대한
리스크 수준과 권장 신뢰도를 산출한다. Trader 에이전트는 이 결과를 참고해
최종 결정을 내린다.
"""
from __future__ import annotations
from agents.base import LLMClient

RISK_SYSTEM_PROMPT = """당신은 리스크 관리(Risk Manager) 에이전트입니다.
Bull과 Bear의 토론 전체를 검토하고, 다음을 판단하세요:
1. 두 입장 중 어느 쪽 논거가 더 데이터에 근거해 설득력 있는지
2. 오늘 KOSPI 예측에 대한 리스크 수준 (낮음/보통/높음)
3. 권장 신뢰도 (낮음/보통/높음) — 토론이 팽팽했다면 신뢰도를 낮게 잡으세요.
주의: risk_level은 "불확실성"의 크기입니다. 하락 가능성이 크다고 risk_level을 높이는 게 아니라,
방향이 어느 쪽이든 예측이 빗나갈 가능성이 클 때 높이세요. leaning(방향)과 risk_level(불확실성)은 별개입니다.
반드시 JSON만 출력:
{"leaning": "up 또는 down", "risk_level": "낮음/보통/높음", "recommended_confidence": "낮음/보통/높음", "summary": "..."}"""


def run_risk_manager(llm: LLMClient, transcript: list[dict], weight_context: str = "",
                     moderation_text: str = "") -> dict:
    lines = [f"{t['stance'].upper()} (round {t['round']}, conf={t.get('confidence', '?')}): {t['argument']}"
              for t in transcript]
    user_prompt = "다음은 Bull/Bear 토론 전체 기록입니다:\n\n" + "\n".join(lines) + moderation_text + weight_context
    return llm.complete_json(RISK_SYSTEM_PROMPT, user_prompt, max_tokens=500)
