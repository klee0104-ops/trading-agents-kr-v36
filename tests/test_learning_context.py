"""(V3.37) 하루짜리 교훈 검증 · 기준 비율 · 개장 전 공시 · 특수 상황 · 보합일 별도 집계."""
from datetime import date, datetime, timedelta, timezone

from memory.lessons import lesson_support, annotate_lessons, build_lessons_text, situation_tags
from data import base_rates as br

KST = timezone(timedelta(hours=9))


def rec(d, actual, tags):
    return {'target_date': d, 'verified': True, 'actual_direction': actual, 'situation_tags': tags}


TAGS = ['추세:횡보', '변동성:보통', 'ma:역배열', 'macd:하락 모멘텀', 'SOX:down', '예측:down']


def test_lesson_needs_repeated_similar_days_to_become_pattern():
    lesson = {'target_date': '2026-10-23', 'actual_direction': 'up', 'tags': TAGS, 'lesson': 'x'}
    assert lesson_support(lesson, [])['status'] == 'observation'
    same = [rec(f'2026-09-{10 + i}', 'up', TAGS) for i in range(3)]
    assert lesson_support(lesson, same + [rec('2026-09-20', 'down', TAGS)])['status'] == 'confirmed'
    against = [rec(f'2026-09-{10 + i}', 'down', TAGS) for i in range(4)]
    s = lesson_support(lesson, against)
    assert s['status'] == 'contradicted' and s['n'] == 4 and s['match'] == 0
    assert annotate_lessons([lesson], against) == []  # 반박된 교훈은 프롬프트에서 뺌
    txt = build_lessons_text(annotate_lessons([lesson], same))
    assert '검증된 패턴' in txt and '비슷한 과거 3일 중 3일 실제 상승' in txt


def test_lesson_ignores_prediction_tags_and_future_records():
    lesson = {'target_date': '2026-10-23', 'actual_direction': 'up', 'tags': TAGS}
    future = [rec('2026-10-30', 'up', TAGS)] * 3
    assert lesson_support(lesson, future, as_of='2026-10-24')['n'] == 0


def _series():
    # 30거래일: SOX 를 이틀마다 -5%, KOSPI 는 그 다음날 +1% (단, 갭 -2% 출발)
    sox, kc, ko = {}, {}, {}
    d0 = date(2026, 1, 5)
    s, k = 100.0, 100.0
    for i in range(60):
        d = str(d0 + timedelta(days=i))
        prev_k = k
        if i % 2 == 0:
            s *= 0.95
        else:
            s *= 1.0
        sox[d] = s
        if i % 2 == 1:
            k *= 1.01
        else:
            k *= 0.999
        kc[d] = k
        ko[d] = prev_k * (0.98 if i % 2 == 1 else 1.0)
    return {'sox': sox, 'kospi_close': kc, 'kospi_open': ko}


def test_base_rate_uses_only_past_and_counts_bucket():
    s = _series()
    out = br.base_rate(s, date(2026, 3, 1), -5.0, -3.0)
    b = out['bucket']
    assert out['sox_bucket'] == 'SOX −4% 이하 급락' and b['n'] >= 15 and b['up'] == b['n']
    assert b['gap_down_n'] == b['n'] and b['gap_down_recovered'] == b['n']
    rows = br.pair_events(s, date(2026, 2, 1))
    assert all(r['date'] < '2026-02-01' for r in rows)
    lines = br.registry_lines(out)
    assert '다음 날 코스피 상승 마감' in lines[0] and '장중 회복' in lines[0]


def test_base_rate_small_sample_is_labelled():
    s = _series()
    out = br.base_rate(s, date(2026, 1, 15), -5.0)
    assert '표본 부족' in br.registry_lines(out)[0]


def test_special_tags_flow_into_situation_tags():
    tags = br.special_tags(-6.7, -3.5, True)
    assert tags == ['특수:SOX급락', '특수:전일급락', '특수:대형주공시']
    assert '특수:SOX급락' in situation_tags({}, {'special_tags': tags}, None)


def test_premarket_disclosures_filter_routine_and_prefer_core():
    from data.premarket_disclosures import collect
    now = datetime(2026, 10, 23, 8, 10, tzinfo=KST)
    events = [{'code': '000660', 'name': 'SK하이닉스', 'title': '투자판단관련주요경영사항(솔리다임 IPO 검토 중단)', 'filed_date': '20261023'},
              {'code': '005380', 'name': '현대차', 'title': '임원ㆍ주요주주특정증권등소유상황보고서', 'filed_date': '20261023'},
              {'code': '005380', 'name': '현대차', 'title': '단일판매ㆍ공급계약체결', 'filed_date': '20261022'},
              {'code': '005380', 'name': '현대차', 'title': '오래된 공시', 'filed_date': '20261001'}]
    rows = collect(now, [{'code': '000660'}, {'code': '005380'}], 'KEY', poll=lambda *a: {'events': events})
    assert rows[0].startswith('[2026-10-23][SK하이닉스][OpenDART 공시]') and len(rows) == 2
    assert collect(now, [], None) == []


def test_registry_has_disclosure_and_base_rate_groups():
    from rules.citations import build_registry, check_points
    reg = build_registry(None, None, [], None, disclosure_titles=['[2026-10-23][SK하이닉스][OpenDART 공시] 솔리다임 IPO 검토 중단'],
                         base_rate_lines=['기준 비율 SOX −4% 이하 급락 다음 날: 과거 40일 중 다음 날 코스피 상승 마감 18일(45%)'])
    groups = {e['group']: e['id'] for e in reg}
    assert groups['disclosure'] == 'N1' and groups['base_rate'] == 'Q1'
    assert check_points(['급락 다음 날 상승 비율 45% [Q1]'], reg)[0]['status'] == 'ok'


def test_flat_actual_days_counted_separately():
    from rules.performance_evaluation import evaluate
    rows = [{'verified': True, 'actual_direction': 'up', 'up_probability': 35, 'actual_change_pct': 0.29},
            {'verified': True, 'actual_direction': 'down', 'up_probability': 35, 'actual_change_pct': -1.5}]
    d = evaluate(rows)['direction']
    assert d['total'] == 2 and d['correct'] == 1
    assert d['decisive'] == {'total': 1, 'correct': 1, 'accuracy_pct': 100.0}
    assert d['flat_actual_days'] == 1 and d['flat_actual_correct'] == 0
