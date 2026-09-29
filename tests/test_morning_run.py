from datetime import datetime,timedelta
from data.sessions import KST
from tools.morning_run import orchestrate

def exercise(results=None,hour=7,holiday=False):
    clock=[datetime(2026,9,30,hour,30,tzinfo=KST)];calls=[];states=[];archives=[]
    def run(step):
        calls.append(step)
        values=(results or {}).get(step,[])
        return values.pop(0) if values else 0
    code=orchestrate(lambda:clock[0],run,lambda s:clock.__setitem__(0,clock[0]+timedelta(seconds=s)),lambda _:not holiday,states.append,lambda:archives.append(True))
    return code,calls,states,archives

def test_success_records_completion_without_duplicate_analysis():
    code,calls,states,archives=exercise()
    assert code==0 and calls==['verify','run','export-site','notify'] and archives
    assert states[-1]['status']=='completed'

def test_failures_never_publish_or_claim_success():
    assert exercise({'verify':[1]})[1]==['verify']
    code,calls,states,_=exercise({'run':[1,1,1]})
    assert code==1 and calls.count('run')==3 and 'export-site' not in calls
    assert states[-1]['status']=='failed'
    code,calls,states,archives=exercise({'export-site':[1]})
    assert code==1 and not archives and states[-1]['stage']=='export'

def test_holiday_late_and_notification_failure():
    assert exercise(hour=9)[1]==[] and exercise(holiday=True)[1]==[]
    assert exercise({'notify':[1]})[2][-1]['status']=='completed_notification_warning'

def test_retry_can_recover_without_rerunning_verify():
    code,calls,states,_=exercise({'run':[1,0]})
    assert code==0 and calls==['verify','run','run','export-site','notify']
