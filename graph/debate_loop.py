"""
라운드마다 같은 이전 기록을 보고 독립 발언한다. 첫 라운드는 둘 다 빈 기록이다.
동일 라운드의 상대 발언은 다음 라운드부터 공개한다. 순차 호출이지만 정보는 대칭이다.
양쪽 모두 자기 직전 발언의 주장·근거를 반복하고 확신도도 안정됐을 때만 조기 종료한다.
이 종료는 반복 감지이며 합의나 정답 판정이 아니다.
"""
from __future__ import annotations
from copy import deepcopy
import math
from agents.base import LLMClient
from agents.researchers import run_bull, run_bear


def _unchanged(previous: dict, current: dict, threshold: float) -> bool:
    """Malformed or empty answers must never count as stable evidence."""
    if threshold < 0:
        return False
    argument = current.get("argument")
    evidence = current.get("evidence")
    if not isinstance(argument, str) or not argument.strip():
        return False
    if not isinstance(evidence, list) or not evidence or not all(isinstance(e, str) and e.strip() for e in evidence):
        return False
    if argument != previous.get("argument") or evidence != previous.get("evidence"):
        return False
    try:
        before, after = float(previous["confidence"]), float(current["confidence"])
    except (KeyError, TypeError, ValueError):
        return False
    return (math.isfinite(before) and math.isfinite(after)
            and 0 <= before <= 1 and 0 <= after <= 1
            and abs(before - after) <= threshold)


def run_debate_loop(llm: LLMClient, analyst_report: dict, max_rounds: int = 3,
                     convergence_threshold: float = 0.05) -> list[dict]:
    transcript: list[dict] = []

    for round_idx in range(max_rounds):
        history = deepcopy(transcript)
        bull_turn = run_bull(llm, analyst_report, deepcopy(history))
        bull_turn["round"] = round_idx + 1
        bear_turn = run_bear(llm, analyst_report, deepcopy(history))
        bear_turn["round"] = round_idx + 1
        transcript.extend([bull_turn, bear_turn])

        # 서로 다른 입장의 확신도를 비교하지 않고, 각자의 발언 반복을 확인한다.
        if round_idx >= 1:
            if (_unchanged(history[-2], bull_turn, convergence_threshold)
                    and _unchanged(history[-1], bear_turn, convergence_threshold)):
                break

    return transcript
