"""Local-only paper portfolio lifecycle. No LLM calls, broker API or publishing."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil


@contextmanager
def locked(path):
    """비차단 배타 잠금. Windows 는 msvcrt, 그 외(POSIX)는 fcntl — (V3.28) 하네스가 Windows 밖에서도 돌도록."""
    path.parent.mkdir(parents=True,exist_ok=True)
    with open(str(path)+'.lock','a+b') as f:
        f.seek(0); f.write(b'0'); f.flush(); f.seek(0)
        try:
            import msvcrt
        except ImportError:
            msvcrt=None
        if msvcrt is not None:
            msvcrt.locking(f.fileno(),msvcrt.LK_NBLCK,1)
            try: yield
            finally:
                f.seek(0); msvcrt.locking(f.fileno(),msvcrt.LK_UNLCK,1)
        else:
            import fcntl
            fcntl.flock(f.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            try: yield
            finally: fcntl.flock(f.fileno(),fcntl.LOCK_UN)


def write(path,state):
    temp=path.with_suffix('.pending')
    temp.write_text(json.dumps(state,ensure_ascii=False,indent=2,allow_nan=False),encoding='utf-8')
    os.replace(temp,path)


def initialize(path,legacy,now):
    from execution.split_portfolio import fresh
    if path.exists(): return False
    if legacy.exists():
        backup=path.parent/'.review-backups'/('portfolio-'+now.strftime('%Y%m%d-%H%M%S'))
        backup.mkdir(parents=True,exist_ok=True)
        shutil.copy2(legacy,backup/'paper_account.json')
    write(path,fresh(now))
    # The legacy capital is retired, not added to either new sleeve.
    if legacy.exists():
        legacy.unlink()  # exact file only, copied to the recovery folder above
    return True


def parse_quote(payload,now):
    from datetime import datetime
    stamp=payload['localTradedAt']
    traded=datetime.fromisoformat(stamp)
    if traded.tzinfo is None: raise ValueError('Quote timezone missing')
    return {'date':str(traded.astimezone(now.tzinfo).date()),'value':float(str(payload['closePrice']).replace(',','')),
            'traded_at':stamp,'observed_at':now.isoformat(),'market_status':payload.get('marketStatus')}


def run_once(path,envelope):
    import requests
    from data.sessions import kst_now
    from execution.split_portfolio import tick, LONG, INVERSE
    state=json.loads(path.read_text(encoding='utf-8'))
    from config import Config
    from memory.long_report import load,decision
    plan=decision(load(Config.LONG_RESEARCH_PATH),kst_now())
    codes={LONG,INVERSE}|{t for b in state["sleeves"].values() for t in b["holdings"]}|{a["code"] for a in plan["allocations"]}
    from execution.split_portfolio import instrument_names
    if not codes<=set(instrument_names(state,plan)): raise ValueError("Unknown instrument")
    quotes={}
    for code in codes:
        response=requests.get('https://m.stock.naver.com/api/stock/'+code+'/basic',
            headers={'User-Agent':'Mozilla/5.0','Referer':'https://m.stock.naver.com/'},timeout=10)
        response.raise_for_status()
        quotes[code]=parse_quote(response.json(),kst_now())
    if state.get('short_policy')!='etf_v2':
        state['short_policy']='etf_v2';state['short_policy_started_at']=kst_now().isoformat()
        state['legacy_trade_count']=len(state.get('trades',[]))
        write(path,state)  # Additive migration only; no balance/history reset.
    try:
        response=requests.get('https://m.stock.naver.com/api/index/KPI200/basic',headers={'User-Agent':'Mozilla/5.0'},timeout=10)
        response.raise_for_status();quotes['KPI200']=parse_quote(response.json(),kst_now())
    except Exception:pass  # Blocks new entry; does not block ETF risk exits.
    now=kst_now()
    updated=tick(state,envelope,quotes,now,long_report=plan)
    if updated!=state:
        write(path,updated)
    from execution.aggressive_shadow import fresh_shadow, valid_shadow
    shadow_path=Path(Config.PAPER_AGGRESSIVE_PATH)
    if shadow_path.resolve()==path.resolve():
        raise ValueError('Aggressive ledger must be separate')
    if shadow_path.exists():
        shadow=json.loads(shadow_path.read_text(encoding='utf-8'))
        if not valid_shadow(shadow):
            raise ValueError('Invalid aggressive shadow ledger')
    else:
        short_equity=updated['sleeves']['short']['equity']
        shadow=fresh_shadow(now,short_equity)
    shadow_updated=tick(shadow,envelope,quotes,now,long_report=None)
    if shadow_updated!=shadow or not shadow_path.exists():
        write(shadow_path,shadow_updated)
    return 'updated' if updated!=state or shadow_updated!=shadow else 'no_update'


def main():
    from config import Config
    from data.sessions import kst_now
    from memory.outcome_store import load_last_outcome
    parser=argparse.ArgumentParser()
    parser.add_argument('--init',action='store_true')
    parser.add_argument('--tick',action='store_true')
    args=parser.parse_args()
    path=Path(Config.PAPER_PORTFOLIO_PATH)
    status='failed'
    try:
        with locked(path):
            if args.init:
                status='initialized' if initialize(path,Path(Config.PAPER_ACCOUNT_PATH),kst_now()) else 'already_exists'
            elif args.tick:
                status=run_once(path,load_last_outcome(Config.LAST_OUTCOME_PATH))
            else: raise ValueError('Explicit operation required')
        code=0
    except Exception:
        code=1
    log=path.parent/'logs'; log.mkdir(exist_ok=True)
    (log/'paper_portfolio_status.json').write_text(json.dumps({'at':kst_now().isoformat(),'status':status}),encoding='utf-8')
    print('Paper portfolio: '+status)
    return code


if __name__=='__main__':
    raise SystemExit(main())
