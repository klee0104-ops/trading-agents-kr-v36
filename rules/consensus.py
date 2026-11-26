"""Deterministic, source-aware consensus for shadow evaluation only.

This score is independent of the Trader probability and never rewrites it.
Weights are experimental settings, not validated optimal parameters.
"""
from __future__ import annotations

import math

ANALYST_WEIGHT = 0.60
DEBATE_WEIGHT = 0.25
SOX_WEIGHT = 0.15
CONFIDENCE_WEIGHTS = {"낮음": 0.5, "보통": 1.0, "높음": 1.25}
SOX_FULL_MOVE_PCT = 1.5


def _finite(value):
    return type(value) in (int, float) and math.isfinite(value)


def compute_consensus(analysts: dict, debate_qa: list[dict], indices: list) -> dict:
    """Return a bounded score and transparent components; missing evidence is neutral."""
    signed = {"up": 1, "down": -1}
    votes = []
    excluded = []
    for name, report in (analysts or {}).items():
        if not isinstance(report, dict) or report.get("direction") not in signed or report.get("citation_ok") is False:
            excluded.append(name)
            continue
        weight = CONFIDENCE_WEIGHTS.get(report.get("confidence"), 0.5)
        votes.append((signed[report["direction"]], weight))
    analyst = sum(direction * weight for direction, weight in votes) / sum(weight for _, weight in votes) if votes else 0.0

    counts = {"bull": 0, "bear": 0}
    total = 0
    for row in debate_qa or []:
        if not isinstance(row, dict) or row.get("stance") not in counts:
            continue
        verified, cited = row.get("verified_count"), row.get("total_count")
        if not (type(verified) is int and type(cited) is int and 0 <= verified <= cited):
            continue
        counts[row["stance"]] += verified
        total += cited
    debate = (counts["bull"] - counts["bear"]) / total if total else 0.0

    sox = 0.0
    for quote in indices or []:
        name = quote.get("name") if isinstance(quote, dict) else getattr(quote, "name", None)
        change = quote.get("change_pct") if isinstance(quote, dict) else getattr(quote, "change_pct", None)
        if name == "SOX" and _finite(change):
            sox = max(-1.0, min(1.0, change / SOX_FULL_MOVE_PCT))
            break
    score = max(-1.0, min(1.0, ANALYST_WEIGHT * analyst + DEBATE_WEIGHT * debate + SOX_WEIGHT * sox))
    return {"score": round(score, 4), "analyst": round(analyst, 4),
            "debate": round(debate, 4), "sox": round(sox, 4),
            "included_analysts": len(votes), "excluded_analysts": excluded,
            "verified_bull": counts["bull"], "verified_bear": counts["bear"],
            "policy": "experimental_v1"}
