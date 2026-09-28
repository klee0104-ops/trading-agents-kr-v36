"""
(V3.16) 복기 에이전트 — 채점이 끝난 날의 전 과정을 실제 결과와 대조해 "왜 틀렸나 / 왜 맞았나"를 남긴다.

호출 조건(호출부): 빗나갔거나 전략 수익률이 음수인 날 + 크게 맞은 날(과신 점검). 하루 최대 1회.
출력은 짧은 규칙 문장 하나(lesson)와 원인·오도 근거·무시한 지표. lessons.json 에 저장돼
다음 분석의 중재자·트레이더 프롬프트에 비슷한 상황일 때 주입된다.
"""
from __future__ import annotations
from agents.base import LLMClient

REVIEW_SYSTEM_PROMPT = """당신은 복기(사후 검토) 에이전트입니다. 하루 전 KOSPI 예측의 전 과정과 실제 결과를 받습니다.
할 일은 판단이 아니라 진단입니다. 웹 검색 없이 주어진 자료만 보고:
1. 결과가 빗나갔다면(또는 손실이 났다면) 어떤 근거가 오도했는지, 어떤 정량 지표·데이터를 무시했는지 짚으세요.
2. 결과가 맞았다면 근거가 타당했는지, 아니면 운이었는지(근거와 무관한 이유로 맞았는지) 판단하세요.
3. 다음에 비슷한 상황에서 지킬 규칙을 한 문장(40자 내외)으로 쓰세요. 구체적인 조건을 포함하세요.
   예: "실현변동성 30% 이상이고 SOX 힌트가 중립이면 확률을 55 아래로 유지한다."
반드시 JSON만 출력:
{"lesson": "한 문장 규칙", "cause": "핵심 원인 한 줄", "misleading_evidence": ["오도한 근거"],
 "ignored_signals": ["무시한 지표/데이터"], "luck": true 또는 false, "confidence_note": "과신/과소 여부 한 줄"}"""


def build_review_prompt(record: dict, digest: dict) -> str:
    kp = digest.get("prediction") or {}
    lines = [
        f"[결과] 대상일 {record.get('actual_session_date') or record.get('target_date')}: "
        f"예측 {record.get('direction')} (상승확률 {record.get('up_probability')}%, 전망 {record.get('outlook')}, "
        f"포지션 {record.get('position')}), 실제 {record.get('actual_direction')} "
        f"({record.get('actual_change_pct')}%), 전략 수익률 {record.get('strategy_return_pct')}%",
        f"[규칙엔진] {digest.get('rule_summary', '')}",
        f"[정량 지표] {digest.get('quant_text', '')}",
    ]
    for name, a in (digest.get("analysts") or {}).items():
        lines.append(f"[분석가 {name}] {a.get('direction')} / {a.get('confidence')}: {a.get('summary', '')}")
    for t in digest.get("debate") or []:
        lines.append(f"[토론 {t.get('stance')} r{t.get('round')}] {t.get('argument', '')} | 근거: {', '.join(t.get('evidence') or [])}")
    if digest.get("qa_rejected"):
        lines.append(f"[QA가 기각한 근거] {', '.join(digest['qa_rejected'])}")
    if digest.get("moderation"):
        lines.append(f"[중재자] {digest['moderation']}")
    if digest.get("risk"):
        lines.append(f"[리스크] {digest['risk']}")
    lines.append(f"[트레이더 근거] {' / '.join(kp.get('reasoning') or [])}")
    return "\n".join(lines)


def run_postmortem(llm: LLMClient, record: dict, digest: dict) -> dict:
    result = llm.complete_json(REVIEW_SYSTEM_PROMPT, build_review_prompt(record, digest), max_tokens=500)
    return {
        "lesson": str(result.get("lesson", "")).strip()[:200],
        "cause": str(result.get("cause", "")).strip()[:200],
        "misleading_evidence": [str(x) for x in (result.get("misleading_evidence") or [])][:5],
        "ignored_signals": [str(x) for x in (result.get("ignored_signals") or [])][:5],
        "luck": bool(result.get("luck", False)),
        "confidence_note": str(result.get("confidence_note", "")).strip()[:200],
    }


def should_review(record: dict) -> bool:
    """빗나감 / 손실 / 크게 맞음(과신 점검) 이면 복기."""
    if not record.get("verified"):
        return False
    if record.get("correct") is False:
        return True
    ret = record.get("strategy_return_pct")
    if isinstance(ret, (int, float)) and (ret < 0 or ret > 1.0):
        return True
    return False
