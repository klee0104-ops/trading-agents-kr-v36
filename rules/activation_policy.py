"""Prospective paper-only trade activation settings; experimental, not optimized."""
from __future__ import annotations

import math
from config import Config

SHADOW_CAPITAL = 12_000_000
SHADOW_MIN_EDGE = Config.ACTIVATION_MIN_EDGE
SHADOW_TARGET_VOL_PCT = Config.ACTIVATION_TARGET_VOL_PCT
SHADOW_VOL_FLOOR = Config.ACTIVATION_VOL_FLOOR
SHADOW_CONSENSUS_BOOST = Config.ACTIVATION_CONSENSUS_BOOST
SHADOW_WEAK_WEIGHT = Config.ACTIVATION_WEAK_WEIGHT
SHADOW_STRONG_WEIGHT = Config.ACTIVATION_STRONG_WEIGHT
SHADOW_MAX_WEIGHT = Config.ACTIVATION_MAX_WEIGHT
SHADOW_NEUTRAL_PROBE_WEIGHT = Config.ACTIVATION_PROBE_WEIGHT
MIN_STRONG_PROBABILITY = 57.0
STRONG_SAMPLE_SIZE = 40
STRONG_TOP_FRACTION = 0.20


def strong_threshold(probabilities: list[float]) -> float:
    """Only earlier, finite forecasts; insufficient sample retains 57%."""
    values = [abs(float(p) - 50.0) for p in probabilities
              if type(p) in (int, float) and math.isfinite(p) and 0 <= p <= 100]
    values = values[-STRONG_SAMPLE_SIZE:]
    if len(values) < STRONG_SAMPLE_SIZE:
        return MIN_STRONG_PROBABILITY
    ordered = sorted(values, reverse=True)
    boundary = ordered[max(0, math.ceil(len(ordered) * STRONG_TOP_FRACTION) - 1)]
    return min(MIN_STRONG_PROBABILITY, 50.0 + boundary)


def shadow_weight(probability: float, history: list[float], consensus: float | None,
                  realized_vol_pct: float | None, selected_code: str) -> dict:
    from execution.position import position_from_probability

    threshold = strong_threshold(history)
    position = position_from_probability(probability, realized_vol_pct,
        target_vol_pct=SHADOW_TARGET_VOL_PCT, min_edge=SHADOW_MIN_EDGE,
        vol_floor=SHADOW_VOL_FLOOR)
    base = SHADOW_STRONG_WEIGHT if probability >= threshold else SHADOW_WEAK_WEIGHT
    aligned = (type(consensus) in (int, float) and math.isfinite(consensus)
               and (consensus > 0 if selected_code == '069500' else consensus < 0)
               and probability > 50)
    if position['instrument'] == 'cash' and not aligned:
        return {'weight': 0.0, 'strong_threshold': threshold,
                'aligned_consensus': False, 'probability_position': 0.0,
                'reason': 'edge below experimental cash threshold'}
    weight = max(base, abs(position['position']))
    if aligned:
        weight *= SHADOW_CONSENSUS_BOOST
    return {'weight': round(min(SHADOW_MAX_WEIGHT, weight), 4),
            'strong_threshold': threshold, 'aligned_consensus': bool(aligned),
            'probability_position': position['position']}
