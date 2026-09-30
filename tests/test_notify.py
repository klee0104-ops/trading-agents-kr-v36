import json
from tools import notify


def test_morning_notice_distinguishes_risk_check_from_failed_final_qa(tmp_path,monkeypatch):
    path=tmp_path/'synthetic_outcome.json'
    path.write_text(json.dumps({'saved_at':'2026-09-30T07:30:00+09:00','outcome':{
        'target_date':'2026-09-30','verification':{'verification_passed':True},
        'final_qa':{'consistent':False},'risk_assessment':{'risk_level':'높음'},
        'final_result':{'kospi_prediction':{'direction':'up','up_probability':55,'confidence':'보통'}}}}),encoding='utf-8')
    monkeypatch.setattr(notify,'OUTCOME',path)
    short,long=notify.build_summary()
    assert '검증① 리스크 단계 통과' in short
    assert '검증② 최종 QA 불일치' in short
    assert '신규 모의 진입 보류' in short and '검증: 통과' not in long
