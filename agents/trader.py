"""
트레이더 에이전트. 파이프라인의 마지막 단계.
분석가 리포트 + 토론 전체 + 리스크 매니저 판단 + (있다면) 과거 실적 기록을 종합해서
최종 KOSPI 방향 예측과 핵심/관심 종목별 방향성 신호를 만든다.

출력 스키마는 기존에 만든 HTML 대시보드 아티팩트와 동일하게 맞춰서,
백엔드로 전환하더라도 프론트엔드를 거의 그대로 재사용할 수 있게 했다.
"""
from __future__ import annotations
from agents.base import LLMClient

TRADER_SYSTEM_PROMPT = """당신은 최종 의사결정을 내리는 트레이더(Trader) 에이전트입니다.
분석가 리포트, Bull/Bear 토론 전체, 리스크 매니저의 판단, 그리고 (있다면) 과거 예측 적중 기록을
모두 종합해서 최종 결론을 내리세요.

중요: 이건 매수/매도를 추천하는 게 아니라 정보 제공입니다. sensitivity(민감도)는
정성적 평가이고, directional_signal은 참고용 방향성 신호일 뿐입니다.

방향 판단 원칙:
1. KOSPI 방향의 핵심 선행지표는 SOX(필라델피아 반도체)입니다. SOX가 뚜렷하게(±1.5% 이상) 움직였다면
   그 방향을 기본값으로 삼고, 뒤집으려면 그보다 강한 반대 근거가 있어야 합니다.
2. 리스크 수준(높음/보통/낮음)은 "불확실성"이지 방향이 아닙니다. 리스크가 높다는 이유로 방향을
   하락으로 정하지 마세요. 리스크가 높으면 confidence를 낮추세요.
3. 미국 지수가 -0.3% 이내의 소폭 등락이면 "하락"의 근거로 쓰지 마세요 (보합).
4. up_probability는 "정직한 확률"이어야 합니다. 70이라고 답한 날들은 실제로 10번 중 7번쯤 올라야 하고,
   50 근처는 "모르겠다"는 뜻입니다. 근거가 엇갈리면 45~55로, SOX·미국 지수 다수·리스크 매니저 판단이
   모두 같은 방향일 때만 70 이상 또는 30 이하로 답하세요. 나중에 이 확률의 정확도를 채점합니다.
   방향(up/down)과 신뢰도는 이 확률에서 자동으로 계산되므로 따로 적지 않아도 됩니다.

반드시 아래 JSON 스키마로만 응답하세요:
{
  "kospi_prediction": {
    "up_probability": 0~100 사이 정수 (다음 거래일 KOSPI가 상승 마감할 확률),
    "reasoning": ["근거1", "근거2", "근거3"]
  },
  "core_stocks": [
    {"name": "삼성전자", "related_us_indicator": "SOX", "sensitivity": "high/mid/low",
     "directional_signal": "up 또는 down", "reason": "..."},
    {"name": "SK하이닉스", "related_us_indicator": "SOX", "sensitivity": "high/mid/low",
     "directional_signal": "up 또는 down", "reason": "..."}
  ],
  "watch_stocks": [
    {"name": "종목명", "sector": "섹터", "related_us_indicator": "지표",
     "sensitivity": "high/mid/low", "directional_signal": "up, down 또는 flat", "reason": "..."}
  ]
}
watch_stocks는 [오늘의 관심 종목] 목록에 있는 종목을 **그 이름 그대로, 전부, 그 순서로** 넣으세요 (직접 고르지 마세요).
core_stocks는 삼성전자/SK하이닉스 고정입니다. 종목별 directional_signal은 up/down/flat 중 하나이며,
그 종목만의 근거(가격 흐름, 뉴스, 업종과 미국 지표의 관계)가 없으면 flat(보합)으로 두세요.
reason에는 그 종목에 대한 근거만 적고, 시장 전체 이야기를 반복하지 마세요."""


def run_trader(llm: LLMClient, analyst_report: dict, transcript: list[dict],
                risk_assessment: dict, history_context: str = "",
                verification: dict | None = None,
                use_web_search: bool = True, rule_context: str = "",
                moderation_text: str = "", stock_facts_text: str = "",
                watch_names: list[str] | None = None) -> dict:
    debate_lines = "\n".join(
        f"{t['stance'].upper()} (round {t['round']}): {t['argument']}" for t in transcript
    )
    verification_text = ""
    if verification:
        verification_text = (
            f"\n[검증 에이전트 결과]\n"
            f"최종 리스크 수준={verification.get('final_risk_level')}, "
            f"검증 통과={verification.get('verification_passed')}\n"
            f"{verification.get('notes', '')}"
        )
    user_prompt = f"""[규칙엔진 사실 — 코드로 산출]{rule_context or ' (없음)'}

[분석가 리포트]
{analyst_report.get('summary', '')}

[Bull/Bear 토론 전체]
{debate_lines}
{moderation_text}

[리스크 매니저 판단]
leaning={risk_assessment.get('leaning')}, risk_level={risk_assessment.get('risk_level')},
recommended_confidence={risk_assessment.get('recommended_confidence')}
{risk_assessment.get('summary', '')}
{verification_text}
{history_context}

[오늘의 관심 종목 — 종목별 사실 (코드로 수집)]
{stock_facts_text or '(없음)'}

위 내용을 종합해 최종 JSON을 출력하세요."""

    result = llm.complete_json(TRADER_SYSTEM_PROMPT, user_prompt, max_tokens=3500,
                                use_web_search=use_web_search)
    result = normalize_prediction(result)
    if watch_names:
        result = align_watch_stocks(result, watch_names)
    return result


def align_watch_stocks(result: dict, watch_names: list[str]) -> dict:
    """(V3.11) 관심 종목을 오늘 추첨된 목록에 강제로 맞춘다: 빠진 종목은 flat으로 채우고, 목록 밖 종목은 버린다."""
    by_name = {str(s.get("name", "")).strip(): s for s in (result.get("watch_stocks") or []) if isinstance(s, dict)}
    aligned = []
    for n in watch_names:
        s = dict(by_name.get(n) or {"name": n, "directional_signal": "flat", "reason": "트레이더가 이 종목을 다루지 않아 보합 처리"})
        s["name"] = n
        if s.get("directional_signal") not in ("up", "down", "flat"):
            s["directional_signal"] = "flat"
        aligned.append(s)
    result["watch_stocks"] = aligned
    for s in result.get("core_stocks") or []:
        if isinstance(s, dict) and s.get("directional_signal") not in ("up", "down", "flat"):
            s["directional_signal"] = "flat"
    return result


def normalize_prediction(result: dict) -> dict:
    """(V3.6.3) up_probability에서 direction/outlook/confidence를 코드로 도출해 채워 넣는다.
    확률이 없으면(구버전 응답) LLM이 준 direction/confidence를 그대로 둔다."""
    from rules.outlook import clamp_probability, derive
    pred = result.get("kospi_prediction")
    if not isinstance(pred, dict):
        return result
    p = clamp_probability(pred.get("up_probability"))
    if p is None and pred.get("direction") in ("up", "down"):
        # 구버전 호환: 방향+신뢰도만 있으면 대략의 확률로 환산해 둔다
        base = {"낮음": 8, "보통": 17, "높음": 30}.get(pred.get("confidence"), 12)
        p = float(50 + base if pred["direction"] == "up" else 50 - base)
        pred["probability_imputed"] = True
    if p is not None:
        pred["up_probability"] = p
        pred.update(derive(p))
    return result
