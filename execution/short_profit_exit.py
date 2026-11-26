"""Cost-aware profit exits for newly approved schema-4 paper ETF cycles.

This evaluates observed prices, not exchange stop orders. Thresholds are
prospective experiment settings, not parameters fitted to historical returns.
"""
from execution.split_portfolio import FEE, SLIPPAGE, number

POLICY = {
    'id': 'net_profit_v1',
    'activation_net_pct': 0.60,
    'trail_giveback_pp': 0.35,
    'target_net_pct': 1.20,
}


def evaluate(cycle, book, prices):
    """Return (exit_reason, net_return_pct) from a fresh paper quote.

    Net return estimates the same sell slippage and ETF fee as rebalance().
    A sell may still realize less if the next observed price is worse.
    """
    if cycle.get('exit_policy') != POLICY:
        return None
    code = cycle.get('selected_code')
    held = book.get('holdings', {}).get(code) or {}
    price = prices.get(code)
    qty, cost = held.get('qty'), held.get('cost')
    if not all(number(x) and x > 0 for x in (qty, cost, price)):
        return None
    current = 100 * (qty * price * (1 - SLIPPAGE) * (1 - FEE) / cost - 1)
    previous = cycle.get('profit_peak_net_pct')
    if previous is not None and not number(previous):
        return None
    peak = max(current, previous) if previous is not None else current
    cycle['profit_peak_net_pct'] = peak
    cycle['profit_current_net_pct'] = current
    if current >= POLICY['target_net_pct']:
        return 'profit_target_exit', current
    if (peak >= POLICY['activation_net_pct']
            and peak - current >= POLICY['trail_giveback_pp']):
        return 'profit_trailing_exit', current
    return None
