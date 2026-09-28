"""
Bull/Bear 리서처 에이전트. 서로 반대 입장에서 논쟁하며, 상대방의 직전 주장에
직접 반박하도록 프롬프트를 구성한다. (TradingAgents의 핵심 아이디어인
"토론을 통한 의견 정제"를 간소화 구현)

⚠️ 설계 변경 이력: 예전 버전은 스키마 예시에 "direction": "up"(Bull)/"down"(Bear)을
고정값으로 못박아뒀었다. 이러면 Bull은 데이터를 실제로 보고 판단하는 게 아니라
그냥 항상 "up"이라고 말하도록 강제된 것과 같아서, "에이전트별 적중률"을 재봐야
"그날 시장이 오를 확률 vs 내릴 확률"을 재는 것과 동일한 의미 없는 지표가 됐었다.

지금 버전은 direction을 스키마에서 아예 제거했다. 대신:
1. 반드시 analyst_report/뉴스에 실제로 있는 근거(evidence)를 인용하도록 강제
2. 근거가 약하면 confidence를 낮추도록 명시 지시 (억지로 강한 확신 만들지 않기)
3. QA 검증팀(qa_verifier.py)이 evidence가 실제로 소스에 있는지 사후 대조한다.
"""
from __future__ import annotations
from agents.base import LLMClient


BULL_SYSTEM_PROMPT = """당신은 낙관론자(Bull) 리서처 에이전트입니다. 역할은 KOSPI 상승 쪽 논거를
최대한 설득력 있게 제시하는 것이지만, 근거 없이 낙관을 주장해서는 안 됩니다.

규칙:
1. 반드시 주어진 분석가 리포트나 뉴스 자료에 실제로 등장하는 사실만 근거로 삼으세요.
   지어낸 수치나 존재하지 않는 뉴스를 인용하면 안 됩니다.
2. evidence 필드에는 근거로 삼은 구체적 사실을 그대로 옮겨 적으세요 (요약/왜곡 금지).
3. 만약 주어진 자료에 상승을 뒷받침할 근거가 약하거나 부족하다면, 억지로 강한 확신을
   만들지 말고 confidence를 낮게 잡으세요 (낙관론자 역할이어도 데이터가 나쁘면 낮은
   확신으로 정직하게 표현하는 것이 맞습니다).
4. 상대방(Bear)의 직전 주장에 허점이 있다면 근거를 들어 반박하세요.

반드시 JSON만 출력: {"stance": "bull", "argument": "...", "evidence": ["근거1", "근거2"], "confidence": 0.0~1.0}"""

BEAR_SYSTEM_PROMPT = """당신은 비관론자(Bear) 리서처 에이전트입니다. 역할은 KOSPI 하락/조정 리스크
쪽 논거를 최대한 설득력 있게 제시하는 것이지만, 근거 없이 비관을 주장해서는 안 됩니다.

규칙:
1. 반드시 주어진 분석가 리포트나 뉴스 자료에 실제로 등장하는 사실만 근거로 삼으세요.
   지어낸 수치나 존재하지 않는 뉴스를 인용하면 안 됩니다.
2. evidence 필드에는 근거로 삼은 구체적 사실을 그대로 옮겨 적으세요 (요약/왜곡 금지).
3. 만약 주어진 자료에 하락/리스크를 뒷받침할 근거가 약하거나 부족하다면, 억지로 강한
   확신을 만들지 말고 confidence를 낮게 잡으세요.
4. 상대방(Bull)의 직전 주장에 허점이 있다면 근거를 들어 반박하세요.

반드시 JSON만 출력: {"stance": "bear", "argument": "...", "evidence": ["근거1", "근거2"], "confidence": 0.0~1.0}"""


def _build_user_prompt(analyst_report: dict, transcript: list[dict]) -> str:
    lines = [f"[분석가 리포트]\n{analyst_report.get('summary', '')}"]
    if analyst_report.get("notable_points"):
        lines.append("주요 포인트: " + ", ".join(analyst_report["notable_points"]))
    if analyst_report.get("quant_context"):  # (V3.14) 정량 지표 — 근거로 인용 가능, QA가 수치와 대조함
        lines.append(analyst_report["quant_context"])

    if transcript:
        lines.append("\n[지금까지의 토론]")
        for turn in transcript:
            ev = ("; 근거: " + ", ".join(turn.get("evidence", []))) if turn.get("evidence") else ""
            lines.append(f"- {turn['stance'].upper()}: {turn['argument']}{ev}")

    lines.append("\n위 내용을 참고해 당신의 입장에서 다음 발언을 제시하세요.")
    return "\n".join(lines)


def run_bull(llm: LLMClient, analyst_report: dict, transcript: list[dict]) -> dict:
    prompt = _build_user_prompt(analyst_report, transcript)
    return llm.complete_json(BULL_SYSTEM_PROMPT, prompt, max_tokens=600)


def run_bear(llm: LLMClient, analyst_report: dict, transcript: list[dict]) -> dict:
    prompt = _build_user_prompt(analyst_report, transcript)
    return llm.complete_json(BEAR_SYSTEM_PROMPT, prompt, max_tokens=600)
