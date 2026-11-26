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
                     moderation_text: str = "", debate_qa: list | None = None) -> dict:
    # (V3.30) "어느 쪽 논거가 데이터에 근거했는지" 판단하라면서 근거와 QA 결과를 주지 않던 문제 수정
    qa = {(r.get("stance"), r.get("round")): r for r in (debate_qa or []) if isinstance(r, dict)}
    lines = []
    for t in transcript:
        q = qa.get((t.get("stance"), t.get("round"))) or {}
        ev = "; ".join(str(e) for e in (t.get("evidence") or [])) or "(근거 인용 없음)"
        ver = (f" | QA 확인 {q.get('verified_count', 0)}/{q.get('total_count', 0)}"
               + (f", 미확인: {'; '.join(map(str, q.get('unverified') or []))}" if q.get("unverified") else "")
               + (f", QA 판정 불가(시스템 대조 실패 — 자료에 없다는 뜻 아님): {len(q.get('qa_unknown') or [])}건"
                  if q.get("qa_unknown") else "")) if q else ""
        lines.append(f"{str(t.get('stance', '')).upper()} (round {t.get('round')}, conf={t.get('confidence', '?')}): "
                     f"{t.get('argument', '')} | 근거: {ev}{ver}")
    user_prompt = ("다음은 Bull/Bear 토론 전체 기록입니다. QA 미확인 근거에 기댄 주장은 약하게 보세요:\n\n"
                   + "\n".join(lines) + moderation_text + weight_context)
    return normalize_risk(llm.complete_json(RISK_SYSTEM_PROMPT, user_prompt, max_tokens=500))


_LEVELS = {"낮음": "낮음", "low": "낮음", "보통": "보통", "중간": "보통", "medium": "보통", "moderate": "보통",
           "높음": "높음", "high": "높음"}
_LEANS = {"up": "up", "상승": "up", "bull": "up", "down": "down", "하락": "down", "bear": "down"}


def normalize_risk(raw) -> dict:
    """(V3.31) 리스크 판단 값을 코드 기준으로 고정한다. 허용 목록 밖의 값이 오면
    규칙(높음 판정·25% 상한·상충 검사)이 조용히 빠지던 문제 → 리스크는 '높음', 방향은 없음으로 두고 경고."""
    r = dict(raw) if isinstance(raw, dict) else {}
    issues = []
    lvl = _LEVELS.get(str(r.get("risk_level", "")).strip().lower())
    if lvl is None:
        issues.append(f"risk_level={r.get('risk_level')!r}→높음")
        lvl = "높음"
    r["risk_level"] = lvl
    conf = _LEVELS.get(str(r.get("recommended_confidence", "")).strip().lower())
    if conf is None:
        issues.append(f"recommended_confidence={r.get('recommended_confidence')!r}→낮음")
        conf = "낮음"
    r["recommended_confidence"] = conf
    lean = _LEANS.get(str(r.get("leaning", "")).strip().lower())
    if lean is None and r.get("leaning") is not None:
        issues.append(f"leaning={r.get('leaning')!r}→없음")
    r["leaning"] = lean
    if not isinstance(r.get("summary"), str):
        r["summary"] = "" if r.get("summary") is None else str(r.get("summary"))
    if issues:
        print("[리스크 응답 형식 보정] " + ", ".join(issues))
        r["format_fixed"] = issues
    return r
