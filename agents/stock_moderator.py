"""
종목 중재자 (V3.11). 트레이더가 낸 종목별 신호(상승/하락/보합)를 그 종목의 실제 사실(가격 흐름,
뉴스 제목, 시장 전망)과 대조해 근거 없는 신호를 **보합으로 되돌리거나** 근거가 확실하면 그대로 둔다.
판단이 아니라 검토다: 새 방향을 만들어내지 않고, 트레이더 신호를 "유지 / 보합으로 조정"만 한다.
조정된 종목은 `moderated: true`, `original_signal`, `moderation_reason`을 달아 대시보드에 "중재자 조정"으로 표시된다.
AI 호출 1회. 관심 종목이 없으면 호출하지 않는다.
"""
from __future__ import annotations
from agents.base import LLMClient

STOCK_MOD_PROMPT = """당신은 종목 중재자입니다. 트레이더가 종목마다 낸 신호(up/down/flat)를 검토합니다.
원칙:
1. 종목 자체의 사실(최근 1일·5일 등락, 그 종목 뉴스, 업종과 미국 지표의 연결)이 신호를 뒷받침하면 "keep".
2. reason이 시장 전체 이야기의 반복이거나, 그 종목 뉴스·가격 흐름과 맞지 않거나, 근거가 없으면 "flat"으로 조정.
3. 새 방향(up↔down 뒤집기)은 만들지 마세요. 선택지는 keep 또는 flat뿐입니다.
4. 핵심 종목(삼성전자·SK하이닉스)은 SOX 방향과 일치하면 keep입니다.
반드시 JSON만 출력:
{"reviews": [{"name": "종목명", "action": "keep 또는 flat", "reason": "한 문장"}]}"""


def run_stock_moderator(llm: LLMClient, final_result: dict, stock_facts_text: str, kospi_outlook: str) -> dict:
    stocks = list(final_result.get("core_stocks") or []) + list(final_result.get("watch_stocks") or [])
    if not stocks:
        return final_result
    lines = [f"- {s.get('name')}: 신호={s.get('directional_signal')} / 근거: {s.get('reason', '')}" for s in stocks]
    user_prompt = (f"[KOSPI 전망] {kospi_outlook}\n\n[종목별 사실]\n{stock_facts_text}\n\n"
                   f"[트레이더 종목 신호]\n" + "\n".join(lines) + "\n\n각 종목을 검토하세요.")
    try:
        review = llm.complete_json(STOCK_MOD_PROMPT, user_prompt, max_tokens=900)
    except Exception:  # noqa: BLE001 — 중재 실패는 예측을 막지 않는다
        return final_result
    return apply_reviews(final_result, review)


def apply_reviews(final_result: dict, review: dict) -> dict:
    by_name = {}
    for r in (review or {}).get("reviews") or []:
        if isinstance(r, dict) and r.get("name"):
            by_name[str(r["name"]).strip()] = r
    changed = 0
    for key in ("core_stocks", "watch_stocks"):
        for s in final_result.get(key) or []:
            r = by_name.get(str(s.get("name", "")).strip())
            s["moderated"] = False
            if not r:
                continue
            s["moderator_note"] = r.get("reason", "")
            if r.get("action") == "flat" and s.get("directional_signal") != "flat":
                s["original_signal"] = s.get("directional_signal")
                s["directional_signal"] = "flat"
                s["moderated"] = True
                s["moderation_reason"] = r.get("reason", "")
                changed += 1
    final_result["stock_moderation"] = {"reviewed": len(by_name), "adjusted_to_flat": changed}
    return final_result
