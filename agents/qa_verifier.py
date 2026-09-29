"""
QA 검증팀 (품질보증) 에이전트.

두 단계로 나뉜다:
1. verify_debate_evidence(): 하위 검증 — Bull/Bear가 토론에서 인용한 evidence가
   실제로 분석가 리포트/유사사례 자료에 있는 내용인지 대조한다.
2. verify_final_result(): 상위 검증 — Risk Manager/Trader가 최종 결론을 낼 때,
   토론에 없던 근거를 새로 지어내지는 않았는지, 토론/리스크 판단과 논리적으로
   맞는지 대조한다.

설계 원칙 (비용 관리):
- 둘 다 웹 검색을 쓰지 않는다. "새 정보를 찾는" 검증이 아니라 "이미 가진 자료와
  일치하는지"만 보는 대조 검증이라 토큰이 훨씬 적게 든다.
- 처음엔 "차단"하지 않고 "플래그"만 남긴다. 불일치가 실제로 얼마나 자주 나오는지
  데이터가 쌓이기 전까지는 자동 차단/재시도 로직을 넣지 않는 것이 안전하다.
"""
from __future__ import annotations
from agents.base import LLMClient


QA_DEBATE_SYSTEM_PROMPT = """당신은 QA 검증(품질보증) 에이전트입니다. 웹 검색 없이,
주어진 소스 자료만 보고 토론 발언에 인용된 근거(evidence)가 실제로 그 자료에 있는
내용인지만 대조하세요. 새로운 사실을 찾거나 판단을 내리지 마세요 — 오직 대조만 하세요.

각 근거 항목에 대해:
- 소스 자료에 실질적으로 등장하는 내용이면 verified: true
- 소스 자료에 없거나, 소스와 명백히 다른 수치/주장이면 verified: false
- 근거에 붙은 ID([M1], [N3] 등)가 가리키는 자료와 내용이 다르면 verified: false
- 뉴스 근거인데 매체·ID가 없거나, 목록에 없는 논문·저자·연도를 인용했으면 verified: false

반드시 JSON만 출력:
{"results": [{"evidence": "...", "verified": true, "note": "짧은 이유"}]}"""

QA_FINAL_SYSTEM_PROMPT = """당신은 QA 검증(품질보증) 에이전트입니다. 웹 검색 없이,
주어진 토론 기록과 리스크 판단만 보고, 최종 결론(Trader의 reasoning과 종목별 reason)이
그 안에 실제로 있던 내용에 근거하는지 대조하세요.

체크할 것:
1. reasoning/reason에 언급된 근거가 토론 기록이나 리스크 판단에 실제로 있었는가
   (토론에 없던 완전히 새로운 주장을 지어내지 않았는가)
2. 최종 방향(direction)이 리스크 매니저의 판단과 명백히 모순되지 않는가
   (모순되더라도 틀린 것은 아니지만, 근거 없이 뒤집었다면 문제)

반드시 JSON만 출력:
{"consistent": true 또는 false, "issues": ["문제가 있다면 구체적으로, 없으면 빈 배열"]}"""


def verify_debate_evidence(llm: LLMClient, transcript: list[dict],
                            analyst_report: dict, similar_cases_context: str = "",
                            quant: dict | None = None) -> list[dict]:
    """토론 각 턴의 evidence를 소스 자료와 대조한다. (하위 QA)
    (V3.14) quant(정량 지표)가 있으면 ① 소스 자료에 지표 수치를 포함시켜 LLM이 대조하게 하고,
    ② 코드로 정성 주장("과매수", "골든크로스", "외국인 순매수" …)을 지표 라벨과 대조해
    모순되는 근거는 LLM 판정과 무관하게 unverified 로 내린다."""
    quant_src = ""
    if quant:
        from rules.quant import quant_text
        quant_src = quant_text(quant)
    registry = analyst_report.get("_registry") or []
    if registry:  # (V3.24) ID 붙은 근거 자료 목록이 원 소스다 (정량 지표 포함)
        from rules.citations import registry_text
        quant_src = "\n" + registry_text(registry)
    source_text = (
        f"[분석가 리포트]\n{analyst_report.get('summary', '')}\n"
        f"주요 포인트: {', '.join(analyst_report.get('notable_points', []))}\n"
        f"{similar_cases_context}{quant_src}"
    )

    qa_results = []
    for turn in transcript:
        evidence = turn.get("evidence") or []
        if not evidence:
            # 근거를 아예 인용하지 않은 발언은 검증할 게 없으니 그 자체를 이슈로 남긴다.
            qa_results.append({
                "round": turn.get("round"), "stance": turn.get("stance"),
                "verified_count": 0, "total_count": 0,
                "unverified": [], "no_evidence_cited": True,
            })
            continue

        user_prompt = f"{source_text}\n\n[검증할 근거 목록]\n" + "\n".join(f"- {e}" for e in evidence)
        result = llm.complete_json(QA_DEBATE_SYSTEM_PROMPT, user_prompt, max_tokens=500)
        items = result.get("results", [])
        verified_count = sum(1 for i in items if i.get("verified"))
        unverified = [i["evidence"] for i in items if not i.get("verified")]

        quant_contra: list[dict] = []
        if quant:
            from rules.quant import check_claims
            for c in check_claims(evidence, quant):
                if c["verdict"] == "contradicted":
                    quant_contra.append({"evidence": c["text"], "claim": c["claim"], "actual": c["actual"]})
                    if c["text"] not in unverified:
                        unverified.append(c["text"])
            verified_count = max(0, len(evidence) - len(unverified))

        # (V3.24) 출처 규칙: 근거 ID가 없거나, 목록에 없는 ID거나, 수치가 인용 자료와 다르면
        # LLM 판정과 무관하게 미검증으로 내린다 (코드 판정이 우선).
        citation_rejected: list[dict] = []
        if registry:
            from rules.citations import check_points
            for c in check_points([str(e) for e in evidence], registry):
                if c["status"] != "ok":
                    citation_rejected.append({"evidence": c["text"], "reason": c["reason"]})
                    if c["text"] not in unverified:
                        unverified.append(c["text"])
            verified_count = max(0, len(evidence) - len(unverified))

        qa_results.append({
            "round": turn.get("round"), "stance": turn.get("stance"),
            "verified_count": verified_count, "total_count": len(evidence),
            "unverified": unverified, "no_evidence_cited": False,
            "quant_contradicted": quant_contra,
            "citation_rejected": citation_rejected,
        })

    return qa_results


def verify_final_result(llm: LLMClient, final_result: dict, transcript: list[dict],
                         risk_assessment: dict) -> dict:
    """최종 Trader 결론이 토론/리스크 판단과 정합적인지 대조한다. (상위 QA)"""
    debate_lines = "\n".join(
        f"{t['stance'].upper()} (round {t.get('round')}): {t.get('argument', '')}"
        for t in transcript
    )
    pred = final_result.get("kospi_prediction", {})
    reasoning = "; ".join(pred.get("reasoning", []))
    stock_reasons = "; ".join(
        f"{s.get('name')}: {s.get('reason')}"
        for s in (final_result.get("core_stocks", []) + final_result.get("watch_stocks", []))
    )

    user_prompt = f"""[토론 기록]
{debate_lines}

[리스크 매니저 판단]
{risk_assessment}

[최종 결론]
방향: {pred.get('direction')}, 신뢰도: {pred.get('confidence')}
근거: {reasoning}
종목별 근거: {stock_reasons}

위 최종 결론이 토론/리스크 판단에 실제로 있던 내용에 근거하는지 대조하세요."""

    return llm.complete_json(QA_FINAL_SYSTEM_PROMPT, user_prompt, max_tokens=500)


def summarize_debate_qa(qa_results: list[dict]) -> dict:
    """에이전트(Bull/Bear)별 근거검증 합계를 낸다 (memory 저장용)."""
    summary = {"bull": {"verified": 0, "total": 0}, "bear": {"verified": 0, "total": 0}}
    for r in qa_results:
        stance = r.get("stance")
        if stance not in summary:
            continue
        summary[stance]["verified"] += r.get("verified_count", 0)
        summary[stance]["total"] += r.get("total_count", 0)
    return summary
