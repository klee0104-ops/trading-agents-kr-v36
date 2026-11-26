"""
중재자(Moderator) 에이전트 (V3.8).

TradingAgents 원본의 "Research Manager"에 해당한다. Bull/Bear 토론이 끝난 뒤 판단이 아니라
**정리**를 한다:
- QA 검증팀이 확인한 근거만 남기고, 자료에 없는 근거로 만든 주장은 지운다
- "양쪽이 합의한 사실 / 진짜 쟁점 / 근거 없이 반복된 주장"으로 나눈다
- 토론이 평행선이거나 같은 말 반복이면 debate_quality="unresolved"로 표시하고
  트레이더에게 근거 강도가 낮다는 사실만 알린다 (확률 수치는 유도하지 않는다)
- 방향을 직접 정하지 않는다 (정하는 순간 또 하나의 편이 되므로)

출력은 리스크 매니저와 트레이더가 토론 원문 대신 받는 요약이다. 말 많은 쪽에 끌려가는 것을 막는다.
"""
from __future__ import annotations
from agents.base import LLMClient

MODERATOR_SYSTEM_PROMPT = """당신은 토론 중재자(Moderator)입니다. Bull/Bear 토론을 심판하지 말고 정리만 하세요.
당신은 방향(상승/하락)을 정하지 않습니다. 정리 원칙:
1. QA 검증에서 "자료에 없음"으로 판정된 근거에 기대는 주장은 제외하세요.
2. 양쪽이 사실상 동의한 내용은 agreed_facts에, 실제로 맞부딪히는 쟁점은 key_disputes에 적으세요.
3. 같은 말이 반복되거나 새 근거 없이 상대를 반박한 발언은 noise로 분류하세요.
4. debate_quality:
   - "resolved": 한쪽 논거가 확인된 근거로 명확히 우세
   - "contested": 양쪽 모두 확인된 근거가 있고 팽팽함
   - "unresolved": 근거가 빈약하거나 평행선 → 트레이더에게 낮은 확신을 알림
5. stronger_side는 근거의 "확인 여부와 구체성" 기준이지 말솜씨가 아닙니다. 판단 불가면 "none".
반드시 JSON만 출력:
{"agreed_facts": ["..."], "key_disputes": [{"topic": "...", "bull": "...", "bear": "..."}],
 "noise": ["..."], "debate_quality": "resolved|contested|unresolved",
 "stronger_side": "bull|bear|none", "summary": "리스크 매니저와 트레이더가 읽을 3문장 이내 요약"}"""


def _unverified_by_turn(debate_qa: list[dict]) -> dict:
    out = {}
    for r in debate_qa or []:
        if not isinstance(r, dict):
            continue
        key = (r.get("stance"), r.get("round"))
        out[key] = list(r.get("unverified") or [])
    return out


def _unknown_by_turn(debate_qa: list[dict]) -> dict:
    return {(r.get("stance"), r.get("round")): list(r.get("qa_unknown") or [])
            for r in debate_qa or [] if isinstance(r, dict)}


def run_moderator(llm: LLMClient, transcript: list[dict], debate_qa: list[dict] | None = None,
                  extra_context: str = "") -> dict:
    unv = _unverified_by_turn(debate_qa or [])
    unk = _unknown_by_turn(debate_qa or [])
    lines = []
    for t in transcript:
        ev = ", ".join(str(e) for e in (t.get("evidence") or [])) or "(근거 인용 없음)"
        bad = unv.get((t.get("stance"), t.get("round")), [])
        bad_txt = f" | ⚠ 자료에 없는 근거: {', '.join(map(str, bad))}" if bad else ""
        n_unk = len(unk.get((t.get("stance"), t.get("round")), []))
        if n_unk:  # (V3.31) 시스템 대조 실패는 '자료에 없음'과 구분해서 전달
            bad_txt += f" | QA 판정 불가 {n_unk}건(시스템 대조 실패 — 사실 여부 미정, 이것만으로 약하다고 보지 말 것)"
        lines.append(f"[{str(t.get('stance', '')).upper()} r{t.get('round')}] {t.get('argument', '')}"
                     f" (conf={t.get('confidence', '?')}) | 근거: {ev}{bad_txt}")
    user_prompt = "다음 Bull/Bear 토론을 정리하세요.\n\n" + "\n".join(lines) + (("\n" + extra_context) if extra_context else "")
    result = llm.complete_json(MODERATOR_SYSTEM_PROMPT, user_prompt, max_tokens=700)
    return normalize_moderation(result, transcript)


def normalize_moderation(result: dict, transcript: list[dict]) -> dict:
    """필드 기본값 채우고, 코드로도 판단 가능한 '무의미 토론' 신호를 보강한다."""
    r = dict(result) if isinstance(result, dict) else {}
    # (V3.31) 목록 필드가 문자열/객체로 오면 join 이 글자 단위로 쪼개거나 TypeError 로 아침 분석이 멈추던 문제
    for key in ("agreed_facts", "noise"):
        v = r.get(key)
        if isinstance(v, str):
            v = [v] if v.strip() else []
        r[key] = [x if isinstance(x, str) else str(x) for x in v if x not in (None, "")] if isinstance(v, list) else []
    kd = r.get("key_disputes")
    r["key_disputes"] = [d for d in kd if isinstance(d, dict)] if isinstance(kd, list) else []
    if not isinstance(r.get("summary"), str):
        r["summary"] = str(r.get("summary") or "")
    q = r.get("debate_quality")
    if q not in ("resolved", "contested", "unresolved"):
        q = "contested"
    side = r.get("stronger_side")
    if side not in ("bull", "bear", "none"):
        side = "none"
    # 코드 보강: 같은 발언이 반복되면(라운드 간 argument 동일) 토론이 공회전한 것
    args = [str(t.get("argument", "")).strip() for t in transcript]
    repeated = len(args) - len(set(args))
    if repeated >= 2 and q == "resolved":
        q = "contested"
    r["debate_quality"] = q
    r["stronger_side"] = side
    r["repeated_turns"] = repeated
    r["neutral_signal"] = (q == "unresolved")  # 구버전 필드명: 낮은 확신 표시만
    return r


def build_moderation_text(mod: dict) -> str:
    """리스크 매니저/트레이더 프롬프트에 넣을 정리본."""
    if not mod:
        return ""
    lines = ["\n[중재자 정리 — 토론 원문 대신 이것을 기준으로 판단하세요]"]
    if mod.get("agreed_facts"):
        lines.append("합의된 사실: " + "; ".join(mod["agreed_facts"]))
    for d in mod.get("key_disputes") or []:
        if isinstance(d, dict):
            lines.append(f"쟁점 [{d.get('topic', '')}] Bull: {d.get('bull', '')} / Bear: {d.get('bear', '')}")
    if mod.get("noise"):
        lines.append("근거 없이 반복된 주장(무시): " + "; ".join(mod["noise"]))
    quality = {"resolved": "한쪽 우세", "contested": "팽팽함", "unresolved": "미해결(근거 빈약·평행선)"}
    lines.append(f"토론 품질: {quality.get(mod.get('debate_quality'), '?')}, 근거상 우세: {mod.get('stronger_side', 'none')}")
    if mod.get("neutral_signal"):
        lines.append("※ 토론 근거 강도가 낮습니다. 확인된 자료로 더 가능성 높은 방향을 판단하되, 확신을 낮추고 모르는 내용은 명시하세요. 특정 확률에 맞추지 마세요.")
    if mod.get("summary"):
        lines.append("요약: " + mod["summary"])
    return "\n".join(lines)
