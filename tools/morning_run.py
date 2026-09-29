"""Scheduled morning coordinator. Only main executes; tests inject all effects."""
from pathlib import Path
import json,os,subprocess,sys,time,shutil
from tools.paper_portfolio import locked,write
from data.sessions import kst_now
from data.krx_calendar import is_trading_day

ROOT=Path(__file__).resolve().parents[1]

def orchestrate(now,run,sleep,trading_day,record,archive):
    """Bounded steps; fresh time checked before every paid attempt."""
    def status(stage,result,**extra):record({'at':now().isoformat(),'stage':stage,'status':result,**extra})
    status('preflight','running')
    if not trading_day(now().date()):status('preflight','skipped_holiday');return 0
    if now().hour>=9:status('preflight','skipped_late');return 0
    status('verify','running')
    if run('verify')!=0:status('verify','failed');return 1
    for attempt in range(1,4):
        if now().hour>=9:status('analysis','skipped_late',attempt=attempt);return 1
        status('analysis','running',attempt=attempt)
        if run('run')==0:break
        status('analysis','retry_wait' if attempt<3 else 'failed',attempt=attempt)
        if attempt==3:return 1
        sleep(300)
    status('export','running')
    if run('export-site')!=0:status('export','failed');return 1
    try:archive()
    except Exception:status('archive','failed');return 1
    status('notify','running')
    notified=run('notify')==0
    status('complete','completed' if notified else 'completed_notification_warning')
    return 0

def main():
    logs=ROOT/'logs';logs.mkdir(exist_ok=True)
    state_path=logs/'morning_status.json';day=str(kst_now().date())
    log_path=logs/('morning_'+day+'.log')
    env=dict(os.environ,PYTHONIOENCODING='utf-8')
    def record(state):write(state_path,state)
    def run(step):
        command=[sys.executable,'-B','tools/notify.py','--no-open'] if step=='notify' else [sys.executable,'-B','cli.py',step]
        with log_path.open('a',encoding='utf-8') as log:
            log.write('\n'+kst_now().isoformat()+' STEP '+step+'\n');log.flush()
            try:return subprocess.run(command,cwd=ROOT,env=env,stdout=log,stderr=subprocess.STDOUT,timeout=1200 if step=='run' else 180).returncode
            except subprocess.TimeoutExpired:return 124
    def archive():
        target=ROOT/'site/archive';target.mkdir(exist_ok=True)
        shutil.copy2(ROOT/'site/index.html',target/('index_'+day+'_morning.html'))
    try:
        with locked(logs/'morning_run'):
            # A successful same-day run must not spend another analysis on a manual retry.
            if state_path.exists():
                old=json.loads(state_path.read_text(encoding='utf-8'))
                if str(old.get('at',''))[:10]==day and old.get('status') in ('completed','completed_notification_warning'):
                    print('Morning: already completed today');return 0
            code=orchestrate(kst_now,run,time.sleep,is_trading_day,record,archive)
            print('Morning: see logs/morning_status.json');return code
    except Exception:
        # Keep any recorded failed/running stage; never write provider exceptions or secrets.
        print('Morning: coordinator failed or already running');return 1

if __name__=='__main__':raise SystemExit(main())
