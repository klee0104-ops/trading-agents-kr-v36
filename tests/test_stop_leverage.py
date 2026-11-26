"""(V3.32) 모든 계좌 손실정지의 검토·재개 절차, 레버리지 노출 2배 기준."""
from datetime import datetime, timedelta
from pathlib import Path

import pytest

from data.sessions import KST
from execution.split_portfolio import fresh, review_sleeve_stop, LEVERAGE, LONG, INVERSE

NOW = datetime(2026, 10, 22, 10, tzinfo=KST)


@pytest.mark.parametrize('sleeve', ['short', 'short_leverage', 'long', 'medium'])
def test_every_sleeve_can_be_reviewed_then_resumed_with_new_baseline(sleeve):
    state = fresh(NOW, with_medium=True)
    if sleeve not in state['sleeves']:  # 레버리지 계좌는 추가 입금 후 생김 → 같은 형식으로 만들어 검사
        state['sleeves'][sleeve] = dict(state['sleeves']['short'])
    book = state['sleeves'][sleeve]
    book.update(paused=True, equity=900_000, cash=900_000, holdings={})
    with pytest.raises(ValueError):
        review_sleeve_stop(state, NOW, sleeve, '기획자', '검토 없이 바로 재개 시도', resume=True)
    reviewed = review_sleeve_stop(state, NOW, sleeve, '기획자', '손실 원인 확인과 재진입 위험 점검')
    resumed = review_sleeve_stop(reviewed, NOW + timedelta(minutes=5), sleeve, '기획자',
                                 '위험 한도 재확인 후 재개', resume=True)
    b = resumed['sleeves'][sleeve]
    assert b['paused'] is False and b['risk_restart_peak'] == 900_000
    assert [e['type'] for e in resumed['capital_events']] == [f'{sleeve}_stop_review', f'{sleeve}_stop_resume']


def test_resume_rules_stay_strict():
    state = fresh(NOW, with_medium=True)
    state['sleeves']['short'].update(paused=True, holdings={'069500': {'qty': 1}})
    with pytest.raises(ValueError):
        review_sleeve_stop(state, NOW, 'short', '기획자', '보유 종목이 남은 상태')
    with pytest.raises(ValueError):
        review_sleeve_stop(state, NOW, 'nope', '기획자', '없는 계좌 이름 입력')
    state['sleeves']['short'].update(holdings={})
    reviewed = review_sleeve_stop(state, NOW - timedelta(days=1), 'short', '기획자', '어제 작성한 검토 기록')
    with pytest.raises(ValueError):  # 검토는 재개 당일이어야 함
        review_sleeve_stop(reviewed, NOW, 'short', '기획자', '어제 검토로 오늘 재개 시도', resume=True)


def test_each_new_stop_requires_a_new_review_even_on_the_same_day():
    state = fresh(NOW, with_medium=True)
    state['sleeves']['short'].update(paused=True, holdings={})
    reviewed = review_sleeve_stop(state, NOW, 'short', '기획자', '첫 손실 원인과 재진입 위험 검토')
    resumed = review_sleeve_stop(reviewed, NOW + timedelta(minutes=1), 'short', '기획자',
                                 '첫 재개 전 한도 확인 완료', resume=True)
    resumed['sleeves']['short']['paused'] = True
    with pytest.raises(ValueError, match='New stop review required'):
        review_sleeve_stop(resumed, NOW + timedelta(minutes=2), 'short', '기획자',
                           '새 검토 없이 재개를 시도', resume=True)
    second_review = review_sleeve_stop(resumed, NOW + timedelta(minutes=2), 'short', '기획자',
                                       '두 번째 손실 원인과 재진입 위험 검토')
    second_resume = review_sleeve_stop(second_review, NOW + timedelta(minutes=3), 'short', '기획자',
                                       '두 번째 위험 한도 재확인', resume=True)
    assert second_resume['sleeves']['short']['paused'] is False


def test_restart_baseline_applies_to_all_sleeves_not_only_long_core():
    src = (Path(__file__).resolve().parents[1] / 'execution/split_portfolio.py').read_text(encoding='utf-8')
    assert "if key=='long_core' and number(book.get('risk_restart_peak'))" not in src
    assert "if number(book.get('risk_restart_peak')) and book['risk_restart_peak']>0" in src


def test_leverage_exposure_counts_leveraged_holdings_twice():
    from execution.leverage_contract import directional_exposure, LEVERAGE_MULTIPLIER
    assert LEVERAGE_MULTIPLIER == 2
    prices = {LEVERAGE: 10_000.0, LONG: 50_000.0, INVERSE: 1_000.0}
    state = {'sleeves': {
        'a': {'equity': 1_100_000, 'cash': 0, 'holdings': {LEVERAGE: {'qty': 100}, LONG: {'qty': 2}}},   # 100만×2 + 10만
        'b': {'equity': 500_000, 'cash': 400_000, 'holdings': {INVERSE: {'qty': 100}}},                  # 10만
        'c': {'equity': 300_000, 'cash': 100_000, 'holdings': {'005930': {'qty': 1}}},                   # 시세 없음 → 20만
    }}
    assert directional_exposure(state, prices) == 2_000_000 + 100_000 + 100_000 + 200_000


def test_leverage_exit_levels_scaled_and_day_backstop_looser_than_cycle_stop():
    from execution import leverage_contract as lc
    assert (lc.STOP_NET_PCT, lc.TARGET_NET_PCT, lc.TRAIL_ARM_PCT, lc.TRAIL_GIVEBACK_PP) == (-2.5, 3.0, 1.5, 1.0)
    # 계좌 기준 손실 = 비중 30% × ETF 손절 → 일손실 안전판(−0.9%)보다 작아야 전용 손절이 먼저 작동
    assert lc.WEIGHT * abs(lc.STOP_NET_PCT) < 0.9
    src = (Path(__file__).resolve().parents[1] / 'execution/split_portfolio.py').read_text(encoding='utf-8')
    assert "key=='short_leverage' and day_loss<=-.009" in src


def test_leverage_entry_skips_right_after_5min_spike():
    from execution import leverage_contract as lc
    src = open(lc.__file__, encoding='utf-8').read()
    assert '직전 5분 급등 · 추격 매수 보류' in src and lc.SPIKE_INDEX_PCT == .40 and lc.SPIKE_LEVERAGED_PCT == .80
