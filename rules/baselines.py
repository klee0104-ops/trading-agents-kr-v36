"""
베이스라인(비교 기준) 예측.

"멀티에이전트가 정말 의미가 있나?"를 판단하려면, LLM 없이 누구나 할 수 있는 단순 규칙보다
적중률이 높아야 한다. 이 모듈은 그런 단순 규칙들의 예측을 코드로 만든다 (과금 없음).

입력은 기록에 이미 저장되는 us_indices([{"name","direction"}])만 쓰므로,
과거 기록에도 소급 적용할 수 있다.
"""
from __future__ import annotations

BASELINES = {
    "always_up":   "항상 상승",
    "sp500":       "S&P500 따라가기",
    "sox":         "SOX(반도체) 따라가기",
    "us_majority": "미국 지수 다수결",
    "vix_inverse": "VIX 반대로",
}


def baseline_predictions(us_indices: list[dict]) -> dict:
    """각 베이스라인의 예측 방향 ("up"/"down"). 필요한 지표가 없으면 그 베이스라인은 빠진다."""
    d = {str(q.get("name")): q.get("direction") for q in (us_indices or [])}
    out = {"always_up": "up"}
    if d.get("S&P500") in ("up", "down"):
        out["sp500"] = d["S&P500"]
    if d.get("SOX") in ("up", "down"):
        out["sox"] = d["SOX"]
    non_vix = [v for k, v in d.items() if k != "VIX" and v in ("up", "down")]
    if non_vix:
        ups = sum(1 for v in non_vix if v == "up")
        out["us_majority"] = "up" if ups * 2 >= len(non_vix) else "down"
    if d.get("VIX") in ("up", "down"):
        out["vix_inverse"] = "down" if d["VIX"] == "up" else "up"
    return out


def score_baselines(records: list[dict]) -> dict:
    """
    검증된 기록에서 AI(Trader)와 각 베이스라인의 적중률을 "같은 표본"으로 비교한다.
    반환: {"ai": {...}, "baselines": {key: {"label","total","correct","accuracy"}}, "best_baseline": key}
    """
    rows = [r for r in records if r.get("verified") and r.get("actual_direction") in ("up", "down")]
    ai_total = len(rows)
    ai_correct = sum(1 for r in rows if r.get("direction") == r["actual_direction"])
    res = {}
    for key, label in BASELINES.items():
        t = c = 0
        for r in rows:
            p = baseline_predictions(r.get("us_indices", [])).get(key)
            if p is None:
                continue
            t += 1
            c += (p == r["actual_direction"])
        res[key] = {"label": label, "total": t, "correct": c, "accuracy": (c / t) if t else None}
    scored = [(k, v["accuracy"]) for k, v in res.items() if v["accuracy"] is not None]
    best = max(scored, key=lambda kv: kv[1])[0] if scored else None
    return {
        "ai": {"label": "AI 멀티에이전트", "total": ai_total, "correct": ai_correct,
               "accuracy": (ai_correct / ai_total) if ai_total else None},
        "baselines": res,
        "best_baseline": best,
    }
