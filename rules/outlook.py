"""
상승 확률 → 방향 / 5단계 전망 / 신뢰도 도출 (V3.6.3, 전부 코드).

LLM(Trader)은 "다음 거래일 KOSPI가 오를 확률(0~100)" 하나만 낸다. 방향·전망·신뢰도는
여기서 기계적으로 도출하므로 서로 어긋날 수 없고, 확률은 나중에 보정(calibration) 채점이 된다:
"70이라고 말한 날들은 실제로 10번 중 7번쯤 올랐는가?"
"""
from __future__ import annotations

OUTLOOK_LEVELS = [  # (하한 확률, 코드, 라벨)
    (70, "strong_up", "강한 상승"),
    (55, "mild_up", "약한 상승"),
    (45, "flat", "보합"),
    (30, "mild_down", "약한 하락"),
    (0, "strong_down", "강한 하락"),
]

# 보정 채점용 구간
CALIBRATION_BINS = [(0, 30), (30, 45), (45, 55), (55, 70), (70, 101)]


def clamp_probability(p) -> float | None:
    try:
        v = float(p)
    except (TypeError, ValueError):
        return None
    if v != v or v in (float("inf"), float("-inf")):  # (V3.25) NaN/무한대 → 확률 없음 (예전엔 100%로 굳어 전액 롱)
        return None
    if 0.0 <= v <= 1.0 and v != 0 and v != 1:  # 0.62처럼 소수로 준 경우
        v *= 100
    return max(0.0, min(100.0, round(v, 1)))


def derive(up_probability: float | None) -> dict:
    """확률 → {direction, outlook, outlook_label, confidence}. 확률이 없으면 빈 dict."""
    if up_probability is None:
        return {}
    p = up_probability
    direction = "up" if p >= 50 else "down"
    outlook, label = next((c, l) for lo, c, l in OUTLOOK_LEVELS if p >= lo)
    # 신뢰도는 전망 단계와 일치시킨다: 강한 상승/하락=높음, 약한=보통, 보합=낮음
    confidence = {"strong_up": "높음", "strong_down": "높음", "mild_up": "보통", "mild_down": "보통"}.get(outlook, "낮음")
    return {"direction": direction, "outlook": outlook, "outlook_label": label, "confidence": confidence}


def calibration(rows: list[dict]) -> dict:
    """
    rows: [{"up_probability": float, "actual": "up"|"down"}]
    반환: 구간별 (건수, 평균 예측확률, 실제 상승률) + Brier score (0에 가까울수록 좋음, 0.25 = 동전던지기).
    """
    rows = [r for r in rows if r.get("up_probability") is not None and r.get("actual") in ("up", "down")]
    bins = []
    for lo, hi in CALIBRATION_BINS:
        sub = [r for r in rows if lo <= r["up_probability"] < hi]
        if sub:
            bins.append({
                "range": f"{lo}~{min(hi, 100)}%", "n": len(sub),
                "avg_prob": round(sum(r["up_probability"] for r in sub) / len(sub), 1),
                "actual_up_rate": round(100 * sum(1 for r in sub if r["actual"] == "up") / len(sub), 1),
            })
        else:
            bins.append({"range": f"{lo}~{min(hi, 100)}%", "n": 0, "avg_prob": None, "actual_up_rate": None})
    brier = None
    if rows:
        brier = round(sum(((r["up_probability"] / 100) - (1 if r["actual"] == "up" else 0)) ** 2
                          for r in rows) / len(rows), 4)
    return {"bins": bins, "brier": brier, "n": len(rows)}
