"""
Strategy Manager. V3.5 설계의 신규 추천 컴포넌트: "AI를 직접 조정(재학습)하지 않고
가중치만 조정"한다. Bull/Bear 에이전트 프롬프트 자체는 그대로 두고, 각 역할의 논거에
얼마나 비중을 둘지 계산해서 Risk Manager/Trader의 프롬프트에 참고 컨텍스트로 주입한다.

⚠️ 병합(V3.6) 시 변경한 점
V3.5 브랜치는 "실제로 KOSPI가 오른 비율 = Bull 적중률"로 가중치를 계산했다. 그런데
V3에서 Bull/Bear의 방향 고정을 제거하면서 정확히 이 지표가 "그날 시장이 오를 확률"을
재는 것과 같아 의미가 없다고 결론냈었다(V3_CHANGES.md 1절). 그래서 병합본은
V3의 품질 지표인 **QA 검증팀의 근거 검증률(evidence_verify_rate)** 로 가중치를 낸다.
"근거를 사실대로 인용하는 쪽의 논거에 더 무게를 둔다"는 뜻이다.

가중치 공식: weight = base_weight + (rate - 0.5) * sensitivity, [min_w, max_w]로 clamp.
검증된 근거 수가 너무 적으면(min_sample 미만) 신뢰할 수 없으므로 중립값(base_weight)을 쓴다.
"""
from __future__ import annotations
from memory.store import MemoryStore

BASE_WEIGHT = 0.7
SENSITIVITY = 1.5
MIN_WEIGHT = 0.3
MAX_WEIGHT = 1.0
MIN_SAMPLE = 5  # 누적 근거 인용 수 기준

_ROLE_AGENT = {"bull": "Bull", "bear": "Bear"}


def compute_weights(memory: MemoryStore, base_weight: float = BASE_WEIGHT,
                     sensitivity: float = SENSITIVITY, min_weight: float = MIN_WEIGHT,
                     max_weight: float = MAX_WEIGHT, min_sample: int = MIN_SAMPLE) -> dict:
    stats = memory.compute_stats(include_unverified_evidence=True)
    by_agent = stats.get("by_agent", {})
    returns = (stats.get("returns") or {}).get("by_agent") or {}

    weights = {}
    for role, agent in _ROLE_AGENT.items():
        info = by_agent.get(agent, {})
        total = info.get("evidence_total", 0) or 0
        verified = info.get("evidence_verified", 0) or 0
        if total < min_sample:
            weights[role] = {"weight": base_weight, "evidence_verify_rate": None, "sample_size": total}
            continue
        rate = verified / total
        weight = base_weight + (rate - 0.5) * sensitivity
        weight = max(min_weight, min(max_weight, weight))
        weights[role] = {"weight": round(weight, 3), "evidence_verify_rate": round(rate, 3),
                         "sample_size": total}

    # (V3.16) 분석가별 "말대로 했을 때 수익률" 가중치: 10일 이상 쌓이면 누적수익 부호·크기로 0.5~1.0
    analysts = {}
    for name, perf in returns.items():
        if name in ("Trader", "Bull", "Bear") or (perf.get("days") or 0) < 10:
            continue
        tr = float(perf.get("total_return_pct") or 0.0)
        w = max(0.5, min(1.0, 0.75 + tr / 20.0))   # ±5% 누적이면 ±0.25
        analysts[name] = {"weight": round(w, 2), "total_return_pct": round(tr, 2), "days": perf["days"],
                          "sharpe": perf.get("sharpe_annual")}
    if analysts:
        weights["analysts"] = analysts
    return weights


def build_weight_context_text(weights: dict) -> str:
    bull = weights.get("bull", {})
    bear = weights.get("bear", {})
    if bull.get("evidence_verify_rate") is None and bear.get("evidence_verify_rate") is None \
            and not weights.get("analysts"):
        return ""

    lines = ["\n[참고: Strategy Manager 자동 가중치 — 과거 토론 근거검증률 기반, 코드로 산출]"]
    for label, w in (("Bull(상승 논거)", bull), ("Bear(하락 논거)", bear)):
        if w.get("evidence_verify_rate") is not None:
            lines.append(
                f"- {label} 가중치: {w['weight']} "
                f"(누적 인용 근거 {w['sample_size']}건 중 소스 확인률 {round(w['evidence_verify_rate'] * 100)}%)"
            )
    lines.append("가중치가 낮은 쪽은 과거에 자료에 없는 근거를 인용한 비율이 높았으므로 비중을 줄이고, "
                  "높은 쪽에 조금 더 무게를 두세요. 단, 이는 참고용 보정값이지 절대 기준은 아닙니다.")
    an = weights.get("analysts") or {}
    if an:
        lines.append("분석가별 수익 기여(그 분석가 말대로만 했을 때 누적 수익률, 비용 차감): "
                     + ", ".join(f"{n} {v['weight']} ({v['total_return_pct']:+.1f}%/{v['days']}일)"
                                 for n, v in sorted(an.items(), key=lambda kv: -kv[1]['weight'])))
        lines.append("수익 기여가 낮은 분석가의 방향 주장은 근거가 확실할 때만 반영하세요.")
    return "\n".join(lines)
