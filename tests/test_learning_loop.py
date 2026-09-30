"""(V3.16) 복기 → 교훈 저장 → 다음 분석 주입, 확률 보정, 분석가 수익 가중치."""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from agents.base import MockLLMClient
from data.market_data import MarketSnapshot, IndexQuote, MarketDataProvider
from memory.store import MemoryStore
from memory.lessons import LessonStore, situation_tags, build_lessons_text
from graph.pipeline import TradingAgentsPipeline


class _P(MarketDataProvider):
    def __init__(self):
        super().__init__(); random.seed(5)
        c = [2500.0]
        for _ in range(299): c.append(c[-1] * (1 + random.gauss(0, 0.012)))
        self._c = c
    def fetch_kospi_history(self, as_of, sessions=260):
        c = self._c[-sessions:]; return {"dates": [str(as_of)] * len(c), "closes": c, "highs": [x * 1.004 for x in c], "lows": [x * 0.996 for x in c]}
    def fetch_kospi_session_after(self, after_date):
        return {"actual_date": "2026-09-26", "change_pct": -1.8, "actual_direction": "down"}
    def fetch_stock_actual(self, name, target, after_date=None):
        raise RuntimeError("skip")
    def fetch_etf_returns(self, d):
        return {}


def _snap():
    return MarketSnapshot(as_of=str(date.today()), us_indices=[
        IndexQuote("S&P500", 0.4, "up", str(date.today())), IndexQuote("SOX", 1.1, "up", str(date.today())),
        IndexQuote("VIX", 2.0, "up", str(date.today()))])


def test_postmortem_creates_lesson_and_next_run_uses_it(monkeypatch):
    monkeypatch.setenv("TA_NO_NEWS", "1")
    memory = MemoryStore(None)
    pipe = TradingAgentsPipeline(MockLLMClient(), memory, data_provider=_P(), debate_max_rounds=1)
    out = pipe.run(snapshot=_snap())
    rec = memory.all_records()[0]
    assert rec["outcome_digest"]["analysts"] and rec["situation_tags"]
    assert rec["up_probability_raw"] == out["final_result"]["kospi_prediction"]["up_probability_raw"]
    assert out["lessons_used"] == []
    # 채점(강제로 pending 처리): score_after 를 과거로
    data = memory._read(); k = list(data)[0]
    data[k]["score_after_date"] = "2026-09-25"; data[k]["target_date"] = "2026-09-26"
    data["2026-09-26"] = data.pop(k); memory._write(data)
    pipe.verify_pending()
    rec = memory.all_records()[0]
    assert rec["verified"] and rec["strategy_return_pct"] is not None
    lessons = pipe.lessons.all()
    if rec["correct"] is False or (rec["strategy_return_pct"] or 0) < 0 or (rec["strategy_return_pct"] or 0) > 1:
        assert len(lessons) == 1 and lessons[0]["lesson"] and rec["postmortem"]["cause"]
        out2 = pipe.run(snapshot=_snap())
        assert out2["lessons_used"], "비슷한 상황이므로 교훈이 주입되어야 한다"
    else:
        assert lessons == []


def test_lesson_selection_and_text():
    st = LessonStore(None)
    st.add({"target_date": "2026-09-10", "tags": ["추세:상승", "rsi:중립", "예측:up"], "lesson": "A", "correct": False,
            "actual_direction": "down", "direction": "up", "strategy_return_pct": -1.2})
    st.add({"target_date": "2026-09-11", "tags": ["추세:하락"], "lesson": "B", "correct": True,
            "actual_direction": "up", "direction": "up", "strategy_return_pct": 0.5})
    sel = st.select_relevant(["추세:상승", "rsi:중립"], n=5)
    assert [r["lesson"] for r in sel] == ["A"]
    assert st.select_relevant(["추세:상승"], as_of=date(2026, 9, 10)) == []   # look-ahead 차단
    txt = build_lessons_text(sel)
    assert "복기 교훈" in txt and "A" in txt


def test_situation_tags_vocab():
    tags = situation_tags({"regime_trend": "상승", "regime_volatility": "높음", "direction_hint": "up"},
                          {"labels": {"rsi": "과매수", "flow": "강한 순매도"}}, {"direction": "down", "outlook": "약세"},
                          [{"name": "SOX", "direction": "up"}])
    assert tags == ["추세:상승", "변동성:높음", "SOX힌트:up", "rsi:과매수", "flow:강한 순매도", "예측:down", "전망:약세", "SOX:up"]


def test_review_now_observational_lesson(monkeypatch, tmp_path):
    from config import Config
    from memory.outcome_store import save_last_outcome
    from datetime import date as _d
    monkeypatch.setenv("TA_NO_NEWS", "1")
    memory = MemoryStore(None)
    pipe = TradingAgentsPipeline(MockLLMClient(), memory, data_provider=_P(), debate_max_rounds=1)
    out = pipe.run(snapshot=_snap())
    path = str(tmp_path / "lo.json")
    save_last_outcome(out, path)
    monkeypatch.setattr(Config, "LAST_OUTCOME_PATH", path)

    class _Today(_P):
        def fetch_kospi_history(self, as_of, sessions=260):
            from data.sessions import kst_now
            t = kst_now().date()
            return {"dates": [str(t - __import__("datetime").timedelta(days=1)), str(t)],
                    "closes": [7000.0, 6811.0], "highs": [7000, 6900], "lows": [6800, 6800]}
    pipe.data_provider = _Today()
    r = pipe.review_now()
    assert r and r["observational"] and "관찰" in r["tags"] and r["lesson"]
    assert pipe.lessons.all()[-1]["observational"] is True


def test_close_review_restores_formal_lesson_without_another_model_call():
    memory = MemoryStore(None)
    memory._write({'2026-09-30': {
        'target_date': '2026-09-30', 'verified': True, 'direction': 'up',
        'actual_direction': 'down', 'correct': False, 'strategy_return_pct': -0.3,
        'situation_tags': ['변동성:높음'], 'postmortem': {'lesson': '수급 악화 때 과신하지 않는다',
                                                       'cause': '외국인 매도를 과소평가'},
    }})
    class NoCall:
        def complete_json(self, *args, **kwargs):
            raise AssertionError('Formal review already exists: no extra LLM call')
    pipe = TradingAgentsPipeline(NoCall(), memory, data_provider=_P())
    pipe.lessons.add({'target_date': '2026-09-30', 'observational': True,
                      'lesson': '임시 관찰', 'tags': ['관찰']})
    assert pipe.review_now('2026-09-30') is None
    lesson = pipe.lessons.all()[0]
    assert lesson['lesson'] == '수급 악화 때 과신하지 않는다'
    assert lesson['tags'] == ['변동성:높음']
    assert not lesson.get('observational')
    assert pipe.review_now('2026-09-30') is None


def test_verified_postmortem_replaces_earlier_intraday_observation():
    memory = MemoryStore(None)
    memory._write({'2026-09-30': {
        'target_date': '2026-09-30', 'verified': True, 'direction': 'up',
        'actual_direction': 'down', 'correct': False, 'strategy_return_pct': -0.3,
        'outcome_digest': {'prediction': {'reasoning': ['합성 판단']}},
        'situation_tags': ['변동성:높음'],
    }})
    pipe = TradingAgentsPipeline(MockLLMClient(), memory, data_provider=_P())
    pipe.lessons.add({'target_date': '2026-09-30', 'observational': True,
                      'lesson': '임시 관찰', 'tags': ['관찰']})
    pipe._postmortem('2026-09-30')
    lesson = pipe.lessons.all()[0]
    assert lesson['lesson'] != '임시 관찰'
    assert not lesson.get('observational')
    assert memory.get('2026-09-30')['postmortem']


def test_pending_today_only_after_close():
    m = MemoryStore(None)
    from datetime import date as _d
    m.save(_rec_simple(str(_d.today())))
    assert m.pending_records(_d.today()) == []
    assert len(m.pending_records(_d.today(), include_today=True)) == 1


def _rec_simple(t):
    from memory.store import PredictionRecord
    return PredictionRecord(target_date=t, run_date=t, direction="up", confidence="보통")


def test_legacy_record_scored_with_direction_not_cash():
    from memory.store import PredictionRecord
    m = MemoryStore(None)
    m.save(PredictionRecord(target_date="2026-07-29", run_date="2026-07-28", direction="down", confidence="보통"))
    m.mark_verified("2026-07-29", "down", actual_change_pct=-2.0)
    r = m.all_records()[0]
    assert r["strategy_return_pct"] == round(2.0 - 0.10, 4) and r["position_legacy"] is True


def test_recompute_legacy_returns():
    m = MemoryStore(None)
    m._write({"2026-07-29": {"target_date": "2026-07-29", "direction": "down", "verified": True, "position": None,
                             "actual_change_pct": -2.0, "strategy_return_pct": 0.0}})
    assert m.recompute_legacy_returns() == 1
    assert m.all_records()[0]["strategy_return_pct"] == 1.9
