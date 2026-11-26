"""
(V3.16) 확률 보정 — "AI가 70%라고 할 때 실제로는 얼마나 올랐나"를 기록에서 재서 확률을 되먹인다.

구간 보정(binning) + 표본 수에 따른 수축(shrinkage):
  p_cal = (n_bin · 관측 상승률 + k · p_raw) / (n_bin + k),  k = SHRINK
표본이 적은 구간은 원래 확률에 가깝게, 많은 구간은 관측값에 가깝게 간다.
검증 기록이 MIN_RECORDS 미만이면 보정하지 않는다(항등). 백테스트에서는 as_of 이전 기록만 쓴다.
"""
from __future__ import annotations
from typing import Optional

BINS = [(0, 35), (35, 45), (45, 55), (55, 65), (65, 101)]
MIN_RECORDS = 20  # 운용 원장은 기존 발동 기준 유지
SHADOW_MIN_RECORDS = 12  # 비교 원장 전용 실험값
MIN_BIN = 4
SHRINK = 10.0
MAX_SHIFT = 15.0  # 20건 이상에서만 최대 15%p; 12~19건은 표본 수에 비례해 축소
FULL_SHIFT_RECORDS = 20


def _bin_of(p: float) -> int:
    for i, (lo, hi) in enumerate(BINS):
        if lo <= p < hi:
            return i
    return len(BINS) - 1


def fit_calibration(records: list[dict], as_of: Optional[str] = None, *, experimental: bool = False) -> dict:
    """검증된 기록 → 구간별 {n, observed_up_rate}. raw 확률(up_probability_raw)이 있으면 그것을 쓴다."""
    rows = []
    for r in records:
        if not r.get("verified") or r.get("actual_direction") not in ("up", "down"):
            continue
        if as_of and (r.get("actual_session_date") or r.get("target_date", "")) >= as_of:
            continue
        p = r.get("up_probability_raw", r.get("up_probability"))
        if p is None:
            continue
        rows.append((float(p), r["actual_direction"] == "up"))
    table = {}
    for i, (lo, hi) in enumerate(BINS):
        sub = [u for p, u in rows if _bin_of(p) == i]
        table[i] = {"range": [lo, hi], "n": len(sub), "observed_up_rate": (sum(sub) / len(sub)) if sub else None}
    minimum = SHADOW_MIN_RECORDS if experimental else MIN_RECORDS
    return {"total": len(rows), "bins": table, "active": len(rows) >= minimum, "minimum": minimum}


def apply_calibration(p_raw: Optional[float], model: dict) -> dict:
    """반환 {"p": 보정 확률, "applied": bool, "note": 설명}."""
    if p_raw is None:
        return {"p": None, "applied": False, "note": ""}
    p_raw = float(p_raw)
    if not model or not model.get("active"):
        minimum = model.get('minimum', MIN_RECORDS) if model else MIN_RECORDS
        return {"p": p_raw, "applied": False, "note": f"검증 기록 {model.get('total', 0) if model else 0}건 — 보정 대기({minimum}건 이상 필요)"}
    b = model["bins"].get(_bin_of(p_raw)) or model["bins"].get(str(_bin_of(p_raw)))
    if not b or b["n"] < MIN_BIN or b["observed_up_rate"] is None:
        return {"p": p_raw, "applied": False, "note": "해당 확률 구간 표본 부족"}
    obs = b["observed_up_rate"] * 100
    p_cal = (b["n"] * obs + SHRINK * p_raw) / (b["n"] + SHRINK)
    max_shift = MAX_SHIFT * min(1.0, model["total"] / FULL_SHIFT_RECORDS)
    p_cal = max(p_raw - max_shift, min(p_raw + max_shift, p_cal))
    p_cal = max(1.0, min(99.0, round(p_cal, 1)))
    return {"p": p_cal, "applied": abs(p_cal - p_raw) >= 0.5,
            "note": f"{b['range'][0]}~{b['range'][1]}% 구간 {b['n']}건 실제 상승률 {obs:.0f}% → {p_raw:.0f}%를 {p_cal:.0f}%로 보정"}
