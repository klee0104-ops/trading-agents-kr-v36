"""Prospective 40% ETF paper experiment, isolated from the 20m account."""
import json
from pathlib import Path

from execution.split_portfolio import fresh, read_summary

MARKER = 'short_40pct_v1'
CAPITAL = 12_000_000


def fresh_shadow(now, primary_short_equity):
    state = fresh(now)
    short = state['sleeves']['short']
    short['label'] = '단기 공격형 실험'
    state['sleeves'] = {'short': short}
    state['initial_cash'] = CAPITAL
    state['experiment'] = MARKER
    state['comparison_primary_base_equity'] = float(primary_short_equity)
    state['short_policy'] = 'etf_v2'
    state['short_policy_started_at'] = now.isoformat()
    state['legacy_trade_count'] = 0
    return state


def valid_shadow(state):
    return (isinstance(state, dict) and state.get('experiment') == MARKER
            and state.get('initial_cash') == CAPITAL
            and set(state.get('sleeves', {})) == {'short'}
            and state['sleeves']['short'].get('initial_cash') == CAPITAL
            and isinstance(state.get('comparison_primary_base_equity'), (int, float))
            and state['comparison_primary_base_equity'] > 0)


def read_comparison(primary_path, shadow_path):
    path = Path(shadow_path)
    if not path.exists():
        return None
    state = json.loads(path.read_text(encoding='utf-8'))
    if not valid_shadow(state):
        raise ValueError('Invalid aggressive shadow ledger')
    primary=read_summary(primary_path)
    if primary is not None:
        current_short=next((b for b in primary['sleeves'] if b['key']=='short'),None)
        if current_short is not None and current_short.get('initial_cash')!=CAPITAL:
            return {'started_at':state['created_at'],'status':'allocation_changed',
                    'last_tick':state.get('last_tick'),'primary_period_pnl':None,
                    'experiment_period_pnl':None,'experiment_trades':len(state.get('trades',[]))}
    archive=state.get('comparison_archive')
    if isinstance(archive,dict):
        from execution.split_portfolio import number
        base=state['comparison_primary_base_equity']
        p=archive.get('primary_equity');e=archive.get('experiment_equity')
        comparable=archive.get('comparable') is True and number(p) and number(e) and p>0 and e>0
        return {'started_at':state['created_at'],'last_tick':archive.get('at'),
                'status':'archived' if comparable else 'archived_unverified',
                'primary_weight_pct':25,'experiment_weight_pct':40,
                'primary_base_equity':round(base,2),'experiment_base_equity':CAPITAL,
                'primary_period_pnl':round(p-base,2) if comparable else None,
                'experiment_period_pnl':round(e-CAPITAL,2) if comparable else None,
                'primary_period_return_pct':round((p/base-1)*100,4) if comparable else None,
                'experiment_period_return_pct':round((e/CAPITAL-1)*100,4) if comparable else None,
                'experiment_trades':len(state.get('trades',[])),
                'primary_total_trades':None}
    shadow = read_summary(path)
    if primary is None or shadow is None:
        return None
    short = next((b for b in primary['sleeves'] if b['key'] == 'short'), None)
    trial = next((b for b in shadow['sleeves'] if b['key'] == 'short'), None)
    if short is None or trial is None:
        return None
    if short.get('initial_cash') != CAPITAL:
        return {'started_at':state['created_at'],'status':'allocation_changed',
                'last_tick':shadow.get('last_tick'),'primary_period_pnl':None,
                'experiment_period_pnl':None,'experiment_trades':shadow.get('trades',0)}
    base = state['comparison_primary_base_equity']
    ready = (primary['valuation_audit']['status'] == 'ok'
             and shadow['valuation_audit']['status'] == 'ok'
             and primary['last_tick'] == shadow['last_tick'])
    return {'started_at': state['created_at'], 'last_tick': shadow['last_tick'],
            'status': 'ok' if ready else 'pending',
            'primary_weight_pct': 25, 'experiment_weight_pct': 40,
            'primary_base_equity': round(base, 2), 'experiment_base_equity': CAPITAL,
            'primary_period_pnl': round(short['equity'] - base, 2) if ready else None,
            'experiment_period_pnl': round(trial['equity'] - CAPITAL, 2) if ready else None,
            'primary_period_return_pct': round((short['equity'] / base - 1) * 100, 4) if ready else None,
            'experiment_period_return_pct': round((trial['equity'] / CAPITAL - 1) * 100, 4) if ready else None,
            'experiment_trades': shadow['trades'], 'primary_total_trades': primary['trades']}
