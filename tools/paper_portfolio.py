"""Local-only paper portfolio lifecycle. No LLM calls, broker API or publishing."""
import argparse
from contextlib import contextmanager
import json
import os
from pathlib import Path
import shutil


def _log_failure(stage, error):
    """(V3.31) 실패 원인을 남긴다 — 예전엔 'failed'/'partial' 만 남아 원인을 알 수 없었다. 비밀값은 가린다."""
    try:
        from security.secret_store import redact
        from data.sessions import kst_now as _now
        log = Path(__file__).resolve().parent.parent/'logs'/'paper_portfolio_errors.log'
        log.parent.mkdir(exist_ok=True)
        if log.exists() and log.stat().st_size > 512_000:
            log.replace(log.with_suffix('.log.1'))
        with log.open('a',encoding='utf-8') as f:
            f.write(f"{_now().isoformat()} [{stage}] {type(error).__name__}: {redact(str(error))[:300]}\n")
    except Exception:  # noqa: BLE001 — 기록 실패가 모의 계좌 처리를 막으면 안 된다
        pass


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
    write(path,fresh(now,with_medium=True))
    # The legacy capital is retired, not added to either new sleeve.
    if legacy.exists():
        legacy.unlink()  # exact file only, copied to the recovery folder above
    return True


def parse_quote(payload,now):
    from datetime import datetime
    stamp=payload['localTradedAt']
    traded=datetime.fromisoformat(stamp)
    if traded.tzinfo is None: raise ValueError('Quote timezone missing')
    out={'date':str(traded.astimezone(now.tzinfo).date()),'value':float(str(payload['closePrice']).replace(',','')),
         'traded_at':stamp,'observed_at':now.isoformat(),'market_status':payload.get('marketStatus')}
    # (V3.30) 장중 재판단 자료용 선택 필드: 전일 대비 등락률·전일 종가 (없거나 비정상이면 생략)
    import math
    try:
        ratio=float(str(payload.get('fluctuationsRatio')).replace(',',''))
        diff=float(str(payload.get('compareToPreviousClosePrice')).replace(',',''))
        if math.isfinite(ratio) and math.isfinite(diff):
            out['change_pct']=round(ratio,2);out['prev_close']=round(out['value']-diff,2)
    except (TypeError,ValueError):
        pass
    return out


def run_once(path,envelope):
    import requests
    from data.sessions import kst_now,market_status
    from execution.split_portfolio import tick, enable_medium, enable_long_core, enable_leverage_capital, LONG, INVERSE, LEVERAGE
    now=kst_now()
    session=market_status(now)['code']
    if session in ('pre','weekend','holiday') or (session=='closed' and now.hour*60+now.minute>=15*60+45):
        # A quote checked now would still be the previous session's trade.
        # Preserve the last complete mark and every fill unchanged.
        return 'market_closed'
    state=json.loads(path.read_text(encoding='utf-8'))
    migrated=enable_medium(state,kst_now())
    if migrated!=state:
        backup=path.parent/'.review-backups'/('medium-capital-'+kst_now().strftime('%Y%m%d-%H%M%S'))
        backup.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,backup/'paper_portfolio.json')
        write(path,migrated)
        state=migrated
    core_migrated=enable_long_core(state,kst_now())
    if core_migrated!=state:
        backup=path.parent/'.review-backups'/('long-core-capital-'+kst_now().strftime('%Y%m%d-%H%M%S'))
        backup.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,backup/'paper_portfolio.json')
        write(path,core_migrated)
        state=core_migrated
    leverage_migrated=enable_leverage_capital(state,kst_now())
    if leverage_migrated!=state:
        backup=path.parent/'.review-backups'/('leverage-capital-'+kst_now().strftime('%Y%m%d-%H%M%S'))
        backup.mkdir(parents=True,exist_ok=True)
        shutil.copy2(path,backup/'paper_portfolio.json')
        write(path,leverage_migrated)
        state=leverage_migrated
    from config import Config
    from memory.long_report import load,decision
    plan_failed=False
    try:
        plan=decision(load(Config.LONG_RESEARCH_PATH),kst_now())
    except (OSError,ValueError,KeyError,TypeError):
        # A broken long research file may pause long buys, but must not prevent
        # an already-held short ETF from reaching its scheduled risk exit.
        plan=decision({},kst_now());plan_failed=True
    from memory.medium_report import decision as medium_decision
    try: medium_plan=medium_decision(load(Config.LONG_RESEARCH_PATH),kst_now())
    except (OSError,ValueError,KeyError,TypeError): medium_plan=medium_decision({},kst_now())
    codes={LONG,INVERSE,LEVERAGE}|{t for b in state["sleeves"].values() for t in b["holdings"]}|{a["code"] for a in plan["allocations"]}
    if medium_plan.get('selected_code'):codes.add(medium_plan['selected_code'])
    from execution.split_portfolio import instrument_names
    known=set(instrument_names(state,plan))|set(instrument_names(state,medium_plan));failed_codes=set(codes-known)
    quotes={}
    # Optional long-stock requests come first and have a shorter timeout.
    # Fetch ETF prices last so a slow long provider cannot make exit quotes stale.
    for code in sorted(codes & known,key=lambda c:(c in (LONG,INVERSE,LEVERAGE),c)):
        try:
            response=requests.get('https://m.stock.naver.com/api/stock/'+code+'/basic',
                headers={'User-Agent':'Mozilla/5.0','Referer':'https://m.stock.naver.com/'},
                timeout=10 if code in (LONG,INVERSE,LEVERAGE) else 3)
            response.raise_for_status()
            quotes[code]=parse_quote(response.json(),kst_now())
        except (requests.RequestException,KeyError,TypeError,ValueError):
            failed_codes.add(code)
    if state.get('short_policy')!='etf_v2':
        state['short_policy']='etf_v2';state['short_policy_started_at']=kst_now().isoformat()
        state['legacy_trade_count']=len(state.get('trades',[]))
        write(path,state)  # Additive migration only; no balance/history reset.
    index_failed=False
    try:
        response=requests.get('https://m.stock.naver.com/api/index/KPI200/basic',headers={'User-Agent':'Mozilla/5.0'},timeout=10)
        response.raise_for_status();quotes['KPI200']=parse_quote(response.json(),kst_now())
    except (requests.RequestException,KeyError,TypeError,ValueError):
        index_failed=True  # Blocks new entry; does not block ETF risk exits.
    now=kst_now()
    try:
        review_path=Path(Config.SHORT_INTRADAY_REVIEW_PATH)
        if review_path.parent.resolve()!=path.parent.resolve():raise ValueError('Review account directory mismatch')
        review_state=json.loads(review_path.read_text(encoding='utf-8'))
        intraday_plan=review_state.get('plan') if isinstance(review_state,dict) else None
    except (OSError,ValueError,TypeError):
        intraday_plan=None
    updated=tick(state,envelope,quotes,now,long_report=plan,intraday_plan=intraday_plan,medium_report=medium_plan)
    if updated!=state:
        old_exposure=any(isinstance(row,dict) and 'exposure_pct' in row
                         for row in state.get('equity_history',[]))
        new_exposure=any(isinstance(row,dict) and 'exposure_pct' in row
                         for row in updated.get('equity_history',[]))
        if new_exposure and not old_exposure:
            backup=path.parent/'.review-backups'/('activation-exposure-'+now.strftime('%Y%m%d-%H%M%S'))
            backup.mkdir(parents=True,exist_ok=True)
            shutil.copy2(path,backup/'paper_portfolio.json')
        write(path,updated)
    try:
        from execution.exposure_alert import low_exposure_warning
        warning=low_exposure_warning(updated.get('equity_history') or [])
        if warning:
            warning_path=path.parent/'logs'/'paper_exposure_warning.json'
            warning_path.parent.mkdir(parents=True,exist_ok=True)
            write(warning_path,warning)
    except (OSError,ValueError,KeyError,TypeError):
        pass
    opportunity_failed=False
    if market_status(now)['code']=='open':
        try:
            from execution.paper_opportunity import record
            record(path.parent/'logs'/'paper_opportunity_study.json',quotes,now,
                   envelope,intraday_plan,updated)
        except (OSError,ValueError,KeyError,TypeError):
            opportunity_failed=True
    from execution.aggressive_shadow import fresh_shadow, valid_shadow
    shadow_path=Path(Config.PAPER_AGGRESSIVE_PATH)
    if shadow_path.resolve()==path.resolve():
        raise ValueError('Aggressive ledger must be separate')
    shadow_failed=False;shadow_changed=False
    try:
        if shadow_path.exists():
            shadow=json.loads(shadow_path.read_text(encoding='utf-8'))
            if not valid_shadow(shadow):
                raise ValueError('Invalid aggressive shadow ledger')
        else:
            short_equity=updated['sleeves']['short']['equity']
            shadow=fresh_shadow(now,short_equity)
        current_plan=((((envelope if isinstance(envelope,dict) else {}).get('outcome') or {}).get('final_result') or {}).get('etf_execution') or {})
        promoted=current_plan.get('schema')==4 and current_plan.get('status')=='approved'
        if shadow.get('comparison_archive'):
            shadow_updated=shadow
        elif (promoted and not shadow['sleeves']['short']['holdings']
              and state.get('last_tick')==shadow.get('last_tick')):
            # The prospective 40% experiment becomes the primary policy.
            # Freeze its pre-promotion result instead of creating duplicate new trades.
            shadow_updated=dict(shadow)
            shadow_updated['comparison_archive']={
                'at':now.isoformat(), 'comparable':True,
                'primary_equity':state['sleeves']['short']['equity'],
                'experiment_equity':shadow['sleeves']['short']['equity']}
        else:
            # If an old experimental holding remains, its scheduled exit still runs.
            shadow_updated=tick(shadow,None if promoted else envelope,quotes,now,long_report=None)
        shadow_changed=shadow_updated!=shadow or not shadow_path.exists()
        if shadow_changed:
            write(shadow_path,shadow_updated)
    except (OSError,ValueError,KeyError,TypeError) as e:
        shadow_failed=True;_log_failure('shadow',e)
    activation_failed=False;activation_changed=False
    if Config.PAPER_SHADOW_V2_ENABLED:
        try:
            from execution.shadow_v2 import (fresh_shadow as fresh_activation,
                valid_shadow as valid_activation, tick_shadow, earlier_probabilities)
            activation_path=Path(Config.PAPER_SHADOW_V2_PATH)
            if activation_path.resolve() in (path.resolve(),shadow_path.resolve()):
                raise ValueError('Activation shadow ledger must be separate')
            if activation_path.parent.resolve()!=path.parent.resolve():
                raise ValueError('Activation shadow ledger must share the primary account directory')
            existed=activation_path.exists()
            activation=(json.loads(activation_path.read_text(encoding='utf-8')) if existed
                        else fresh_activation(now,updated['sleeves']['short']['equity']))
            if not valid_activation(activation):
                raise ValueError('Invalid activation shadow ledger')
            if (not activation.get('comparison_started_at')
                    and str(now.date())>=activation.get('comparison_start_date',str(now.date()))):
                activation['comparison_started_at']=now.isoformat()
                activation['comparison_primary_base_equity']=state['sleeves']['short']['equity']
                activation['comparison_shadow_base_equity']=activation['sleeves']['short']['equity']
            history=earlier_probabilities(Config.MEMORY_DB_PATH,str(now.date()))
            activation_updated=tick_shadow(activation,envelope,quotes,now,history,
                                           Config.MEMORY_DB_PATH)
            activation_changed=activation_updated!=activation or not existed
            if activation_changed:
                write(activation_path,activation_updated)
        except (OSError,ValueError,KeyError,TypeError) as e:
            activation_failed=True;_log_failure('shadow_v2',e)
    # Separate 12m paper experiment. Reuses the quotes already fetched above;
    # no paid model call, brokerage endpoint, or extra market-data request.
    intraday_updated=False;intraday_failed=False
    if Config.PAPER_INTRADAY_ENABLED:
        try:
            from tools.intraday_paper import run_once as run_intraday
            intraday_path=Path(Config.PAPER_INTRADAY_PATH)
            if intraday_path.resolve() in (path.resolve(),shadow_path.resolve()):
                raise ValueError('Intraday ledger must be separate')
            intraday=run_intraday(intraday_path,envelope,quotes,now)
            intraday_updated=intraday['last_tick']==now.isoformat()
        except (OSError,ValueError,KeyError,TypeError) as e:
            intraday_failed=True;_log_failure('intraday',e)
    partial=(plan_failed or shadow_failed or activation_failed or intraday_failed or opportunity_failed or
             updated.get('quote_health',{}).get('status')=='partial' or
             (market_status(now)['code']=='open' and (failed_codes or index_failed)))
    if partial: return 'partial'
    return 'updated' if updated!=state or shadow_changed or activation_changed or intraday_updated else 'no_update'


def main():
    from config import Config
    from data.sessions import kst_now
    from memory.outcome_store import load_last_outcome
    parser=argparse.ArgumentParser()
    parser.add_argument('--init',action='store_true')
    parser.add_argument('--tick',action='store_true')
    parser.add_argument('--migrate-medium',action='store_true')
    parser.add_argument('--migrate-leverage',action='store_true')
    parser.add_argument('--review-stop',choices=['long','short','medium','long_core','short_leverage'])
    parser.add_argument('--resume-stop',choices=['long','short','medium','long_core','short_leverage'])
    parser.add_argument('--reviewer')
    parser.add_argument('--reason')
    args=parser.parse_args()
    path=Path(Config.PAPER_PORTFOLIO_PATH)
    status='failed'
    try:
        with locked(path):
            if args.init:
                status='initialized' if initialize(path,Path(Config.PAPER_ACCOUNT_PATH),kst_now()) else 'already_exists'
            elif args.tick:
                status=run_once(path,load_last_outcome(Config.LAST_OUTCOME_PATH))
            elif args.migrate_medium:
                from execution.split_portfolio import enable_medium
                state=json.loads(path.read_text(encoding='utf-8'))
                updated=enable_medium(state,kst_now())
                if updated==state:status='already_migrated_or_deferred'
                else:
                    backup=path.parent/'.review-backups'/('medium-capital-'+kst_now().strftime('%Y%m%d-%H%M%S'))
                    backup.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(path,backup/'paper_portfolio.json')
                    write(path,updated);status='medium_migrated'
            elif args.migrate_leverage:
                from execution.split_portfolio import enable_leverage_capital
                state=json.loads(path.read_text(encoding='utf-8'))
                updated=enable_leverage_capital(state,kst_now())
                if updated==state:status='already_migrated_or_deferred'
                else:
                    backup=path.parent/'.review-backups'/('leverage-capital-'+kst_now().strftime('%Y%m%d-%H%M%S'))
                    backup.mkdir(parents=True,exist_ok=True)
                    shutil.copy2(path,backup/'paper_portfolio.json')
                    write(path,updated);status='leverage_migrated'
            elif args.review_stop or args.resume_stop:
                from execution.split_portfolio import review_sleeve_stop
                if not args.reviewer or not args.reason:
                    raise ValueError('Reviewer and specific reason required')
                state=json.loads(path.read_text(encoding='utf-8'))
                sleeve=args.resume_stop or args.review_stop
                updated=review_sleeve_stop(state,kst_now(),sleeve,args.reviewer,args.reason,
                                           resume=bool(args.resume_stop))
                backup=path.parent/'.review-backups'/(sleeve.replace('_','-')+'-stop-'+kst_now().strftime('%Y%m%d-%H%M%S'))
                backup.mkdir(parents=True,exist_ok=True)
                shutil.copy2(path,backup/'paper_portfolio.json')
                write(path,updated)
                status=f'{sleeve}_resumed' if args.resume_stop else f'{sleeve}_reviewed'
            else: raise ValueError('Explicit operation required')
        code=1 if status=='partial' else 0
    except Exception as e:
        code=1;_log_failure('main',e)
    basket_status=None
    if args.tick:
        # Keep the comparison account independent of the primary ledger and lock.
        # A primary failure must not suppress its tick; neither tick may mutate the other account.
        try:
            from tools.etf_basket import run_tick as run_basket_tick
            basket_status=run_basket_tick()
        except Exception:
            basket_status='failed'
            code=1
    log=path.parent/'logs'; log.mkdir(exist_ok=True)
    (log/'paper_portfolio_status.json').write_text(json.dumps({'at':kst_now().isoformat(),'status':status,
        'basket_status':basket_status}),encoding='utf-8')
    print('Paper portfolio: '+status+((' | ETF basket: '+basket_status) if basket_status else ''))
    return code


if __name__=='__main__':
    raise SystemExit(main())
