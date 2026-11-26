"""Deterministic, paper-only sizing contract for an approved medium plan."""
import math

MEDIUM_CAPITAL = 2_000_000
MIN_ANALYST_WEIGHT = .15
MAX_ANALYST_WEIGHT = .50
POSITION_STOP = .12
SLEEVE_RISK_BUDGET = .06
TARGET_WEIGHT = min(.50, SLEEVE_RISK_BUDGET / POSITION_STOP)
POLICY = 'medium_50pct_v2'


def execution_weight(analyst_weight):
    """An analyst can veto by choosing cash; a buy must clear the policy floor."""
    if (type(analyst_weight) not in (int,float) or not math.isfinite(analyst_weight)
            or not MIN_ANALYST_WEIGHT <= analyst_weight <= MAX_ANALYST_WEIGHT):
        return None
    return TARGET_WEIGHT
