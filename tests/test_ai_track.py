"""(V3.27) 공유 페이지: AI 예측 확률이 지수 등락률과 헷갈리지 않게 별도 표기·별도 트랙으로 그린다."""
from __future__ import annotations
import json
import shutil
import subprocess
import re

_RECS = [
    {"target_date": "2026-09-25", "verified": True, "correct": True, "up_probability": 64, "actual_direction": "up",
     "actual_session_date": "2026-09-25", "actual_change_pct": 0.8, "strategy_return_pct": 0.3},
    {"target_date": "2026-09-28", "verified": True, "correct": False, "up_probability": 62, "actual_direction": "down",
     "actual_session_date": "2026-09-28", "actual_change_pct": -2.7, "strategy_return_pct": -0.9},
    {"target_date": "2026-09-29", "verified": False, "up_probability": 38},
    {"target_date": "2026-09-30", "verified": False, "up_probability": None},       # 확률 없는 기록은 건너뜀
    {"target_date": "2026-10-01", "verified": False, "up_probability": "NaN"},      # 비정상 값도 건너뜀
]


def _render(records=None, with_history=True, quote=None, extra=None):
    from web.export_site import _JS
    node = shutil.which("node")
    assert node, "Node is required"
    dates = [f"2026-09-{d:02d}" for d in (14, 15, 16, 17, 18, 21, 22, 23, 24, 25, 28)]
    data = {"generated_at": "2026-09-29T07:40", "prediction": {"direction": "down", "up_probability": 38},
            "records": _RECS if records is None else records,
            "kospi_history": {"dates": dates, "closes": [2600 + i for i in range(len(dates))]} if with_history else None}
    if quote:
        data['public_quote'] = quote
    data.update(extra or {})
    src = '''const vm=require('node:vm');
const nodes={}, events={}; const data=DATA;
const document={getElementById(id){return nodes[id]||(nodes[id]={textContent:id==='ta-data'?JSON.stringify(data):'',innerHTML:'',className:'',addEventListener(ev,fn){if(id==='aiTrack')events[ev]=fn;}});}};
vm.runInNewContext(SOURCE,{document});
const interactions={};
for(const ev of ['click','mouseover','focusin','keydown']) {
  if(events[ev]) {events[ev]({key:'Enter',preventDefault(){},target:{closest(){return {getAttribute(){return 'selected '+ev;}};}}});interactions[ev]=nodes.aiTip.textContent;}
}
process.stdout.write(JSON.stringify({k:(nodes.kChart||{}).innerHTML||'',ai:nodes.aiTrack.innerHTML,tag:nodes.kAi.innerHTML,board:nodes.board.innerHTML,returns:nodes.ret.innerHTML,core:nodes.core.innerHTML,pred:nodes.pred.innerHTML,date:nodes.kDate?.textContent,notice:nodes.quoteNotice?.textContent,interactions}));
'''.replace("DATA", json.dumps(data, ensure_ascii=False)).replace("SOURCE", json.dumps(_JS))
    r = subprocess.run([node, "-"], input=src, text=True, encoding="utf-8", capture_output=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_price_chart_marker_has_no_percentage():
    out = _render()
    assert "AI ▼" in out["k"] and "38%" not in out["k"]  # 지수 차트 위에 % 를 붙이지 않는다
    assert "상승확률 38%" in out["tag"] and "지수 등락률 아님" in out["tag"]


def test_direction_label_stays_inside_plot_without_covering_axis():
    chart = _render()["k"]
    assert '<circle' not in chart
    marker = re.search(r'class="ax ai-direction" x="([\d.]+)" y="([\d.]+)"', chart)
    assert marker
    assert float(marker[1]) < 720 - 62
    assert 28 <= float(marker[2]) <= 226
    from web.export_site import _BODY
    assert '실시간 시세 조회는 하지 않습니다' in _BODY


def test_ai_track_plots_probability_with_outcomes():
    ai = _render()["ai"]
    assert "AI 예측 기록" in ai and "KOSPI 등락률이 아닙니다" in ai and "50% 중립" in ai
    assert ai.count("data-tip=") == 3  # None·NaN 기록 제외
    assert "m-hit" in ai and "m-miss" in ai and "m-wait" in ai
    assert "실제 하락 -2.70% · 빗나감 · 전략 -0.90%" in ai and "채점 대기" in ai
    assert "NaN" not in ai and "undefined" not in ai


def test_share_records_carry_return_fields():
    import inspect
    from web import export_site
    src = inspect.getsource(export_site.build_snapshot_data)
    for f in ("actual_change_pct", "position", "strategy_return_pct", "up_probability"):
        assert f'"{f}"' in src


def test_quote_shows_intraday_and_separate_source_time():
    out = _render(quote={'date':'2026-09-28','value':2610,'prev':2600,'change_pct':0.38,
                         'traded_at':'2026-09-28T14:00:00','checked_at':'2026-09-28T14:01:00',
                         'intraday':True,'source':'네이버 증권'})
    assert '장중 (미확정)' in out['date']
    assert '14:01:00' in out['notice'] and '5분마다' in out['notice']
    assert '시세 기준 2026-09-28T14:00:00' in out['board']
    assert '참고 미국 지수 · 분석 당시' in out['board']


def test_live_flow_has_priority_without_mislabeling_old_zero():
    out=_render(extra={'inputs':{'kr_flow':{'date':'2026-09-29','foreign_net_krw_bn':0}},
        'public_flow':{'date':'2026-09-29','foreign_net_krw_bn':-1234,'checked_at':'14:00','source':'네이버 증권'},
        'public_flow_status':'unavailable'})
    assert '순매도' in out['board'] and '1,234억원' in out['board']
    assert '최신 조회 실패, 이전 값 표시' in out['board']


def test_returns_are_labeled_as_historical_and_prediction_precedes_chart():
    perf={'total_return_pct':1,'days':2,'equity_curve_pct':[0,1]}
    out=_render(extra={'returns':{'days':2,'proxy_days':2,'dates':['2026-07-29','2026-07-30'],
        'ai':perf,'buy_hold':perf}})
    assert '2026-07-29 ~ 2026-07-30' in out['returns']
    assert '실제 실현손익 아님' in out['returns'] and '2일은 ETF 대신 지수로 근사' in out['returns']
    from web.export_site import _BODY
    assert _BODY.index('id="pred"')<_BODY.index('id="kChart"')
    from pathlib import Path
    local=(Path(__file__).resolve().parents[1]/'web/static/index.html').read_text(encoding='utf-8')
    assert local.index('id="predictionCard"')<local.index('id="chartCard"')


def test_stock_prediction_comparison_uses_same_date_and_does_not_score_intraday():
    q={'status':'ok','date':'2026-09-29','value':98,'change_pct':-2,'intraday':True,'checked_at':'14:00'}
    data={'target_date':'2026-09-29','core_stocks':[{'name':'A','directional_signal':'down'}],'stock_quotes':{'A':q}}
    core=_render(extra=data)['core']
    assert '예측 ▼ 하락' in core and '-2.00%' in core and '현재 방향 일치' in core
    assert '확정 채점 아님' in core and '적중' not in core
    data['core_stocks'][0]['directional_signal']='up'
    assert '현재 방향 불일치' in _render(extra=data)['core']
    data['target_date']='2026-09-30'
    assert '시세 날짜가 달라 비교 보류' in _render(extra=data)['core']
    q['status']='unavailable'
    assert '최신 조회 실패' in _render(extra=data)['core']
    data['stock_quotes']={}
    assert '조회 대기 · 비교 불가' in _render(extra=data)['core']


def test_flat_comparison_uses_explicit_threshold_and_strict_boundary():
    q={'status':'ok','date':'2026-09-29','value':100,'change_pct':0.7,'intraday':True}
    d={'target_date':'2026-09-29','stock_flat_threshold_pct':0.7,
       'core_stocks':[{'name':'A','directional_signal':'flat'}],'stock_quotes':{'A':q}}
    assert '현재 보합 범위 이탈' in _render(extra=d)['core']
    q['change_pct']=-0.69
    assert '현재 보합 범위 내' in _render(extra=d)['core']


def test_track_works_without_market_history_and_supports_touch_and_keyboard():
    out = _render(with_history=False)
    assert out["ai"].count('data-tip=') == 3
    assert out["interactions"] == {ev: 'selected '+ev for ev in ('click', 'mouseover', 'focusin', 'keydown')}
    assert 'tabindex="0"' in out["ai"] and 'aria-live="polite"' in out["ai"]


def test_recent_twenty_are_chronological_and_missing_probability_is_explicit():
    rows = [{"target_date": f"2026-09-{day:02d}", "up_probability": 50} for day in range(1, 26)]
    rows[-1]["up_probability"] = None
    ai = _render(list(reversed(rows)))["ai"]
    assert ai.count('data-tip=') == 19
    assert '최근 20건 중 상승확률이 저장된 19건 표시' in ai
    assert '2026-09-05 AI' not in ai
    assert ai.index('2026-09-06 AI') < ai.index('2026-09-24 AI')
    assert '50% (중립 예측)' in ai


def test_invalid_values_and_incomplete_grades_are_not_misrepresented():
    bad = [{"target_date": "2026-09-28", "up_probability": v} for v in (-1, 101, '', True, '38', None)]
    ai = _render(bad)["ai"]
    assert 'data-tip=' not in ai and '표시할 상승확률 기록이 없습니다' in ai
    ai = _render([{"target_date": "2026-09-29", "up_probability": 38,
                   "verified": True, "correct": None}])["ai"]
    assert 'm-wait' in ai and 'm-miss' not in ai
    assert '→ 채점 대기' in ai


def test_tooltip_escapes_data_and_does_not_invent_actual_direction():
    ai = _render([{"target_date": '<svg/onload=x>', "up_probability": 62,
                   "verified": True, "correct": False}])["ai"]
    assert '<svg/onload' not in ai
    assert '실제 방향 미기록' in ai and '실제 하락' not in ai


def test_toss_layer_present_and_safe_without_dom():
    """(V3.29) 토스 스타일 정리 레이어: 큰 글씨 CSS 와 '자세히' 접기 후처리가 들어 있고,
    DOM 이 없는 환경(검사용 Node VM)에서는 아무 것도 하지 않는다."""
    from web.export_site import _CSS, _JS
    assert "details.more" in _CSS and "font-size:16px" in _CSS and ".why-dir{font-size:42px" in _CSS
    assert "typeof document.querySelector!=='function'" in _JS
    assert "자세히 보기" in _JS and "검증 · QA 과정 보기" in _JS and "AI는 오늘 코스피가 조금 내릴 것 같다고 봤어요" in _JS
    # 원본 렌더 결과는 그대로 나온다 (기존 계약 유지)
    out = _render()
    assert "AI ▼" in out["k"] and "상승확률 38%" in out["tag"]
