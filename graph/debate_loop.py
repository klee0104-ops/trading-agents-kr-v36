"""
토론 루프. Bull과 Bear가 서로의 직전 발언을 보고 반박하는 과정을
설정된 라운드 수만큼 반복한다. 이번 요청("루프까지 적용")의 핵심 부분.

종료 조건은 두 가지 중 하나:
1. max_rounds에 도달
2. 두 에이전트의 confidence 차이가 매우 작고(수렴), 최소 2라운드는 진행된 경우
   -> 굳이 더 토론해도 새로운 정보가 안 나올 때 조기 종료해서 토큰을 아낀다.
"""
from __future__ import annotations
from agents.base import LLMClient
from agents.researchers import run_bull, run_bear


def run_debate_loop(llm: LLMClient, analyst_report: dict, max_rounds: int = 3,
                     convergence_threshold: float = 0.05) -> list[dict]:
    transcript: list[dict] = []

    for round_idx in range(max_rounds):
        bull_turn = run_bull(llm, analyst_report, transcript)
        bull_turn["round"] = round_idx + 1
        transcript.append(bull_turn)

        bear_turn = run_bear(llm, analyst_report, transcript)
        bear_turn["round"] = round_idx + 1
        transcript.append(bear_turn)

        # 조기 종료 체크: 최소 2라운드(4턴) 진행 후, confidence가 수렴하면 멈춘다.
        if round_idx >= 1:
            diff = abs(bull_turn.get("confidence", 0.5) - bear_turn.get("confidence", 0.5))
            if diff < convergence_threshold:
                break

    return transcript
