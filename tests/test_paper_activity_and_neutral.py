"""A neutral probability is not an accuracy win; no-trade reasons stay visible."""
from datetime import datetime, timezone, timedelta

from execution.paper_activity import summarize
from memory.lessons import LessonStore, build_lessons_text
from memory.store import MemoryStore, PredictionRecord


def test_fifty_percent_is_excluded_from_direction_hits_but_kept_for_calibration():
    memory = MemoryStore(None)
    for day, probability, actual, correct in (
        ("2026-09-29", 50, "up", True),
        ("2026-09-30", 55, "down", False),
        ("2026-10-01", 38, "down", True),
        ("2026-10-02", None, "up", True),
    ):
        memory.save(PredictionRecord(target_date=day, run_date=day,
                                     direction="down" if probability == 38 else "up",
                                     confidence="보통", up_probability=probability,
                                     verified=True, actual_direction=actual, correct=correct))
    stats = memory.compute_stats()
    assert (stats["total"], stats["correct"], stats["neutral_probability_count"]) == (3, 2, 1)
    assert (stats["probability_total"], stats["probability_correct"], stats["legacy_direction_count"]) == (2, 1, 1)
    assert stats["baseline_compare"]["ai"]["total"] == 3
    assert stats["calibration"]["n"] == 3
    assert memory.get("2026-09-29")["correct"] is True  # immutable original audit fact


def test_activity_explains_preopen_qa_and_missing_long_allocation():
    now = datetime(2026, 9, 30, 8, 30, tzinfo=timezone(timedelta(hours=9)))
    outcome = {"target_date": "2026-09-30", "final_qa": {"consistent": False},
               "final_result": {"etf_execution": {"status": "held", "forecasts": [
                   {"code": "069500", "up_probability": 55},
                   {"code": "114800", "up_probability": 48}]}}}
    paper = {"last_tick": now.isoformat(), "trades": 2, "recent_trades": [{"at": "2026-09-29T15:21:00+09:00"}],
             "valuation_audit": {"status": "ok"}, "sleeves": [
                 {"key": "short", "status": "장외 · 평가만 갱신"},
                 {"key": "long", "status": "장외 · 평가만 갱신"}]}
    result = summarize(outcome, paper, {"status": "approved", "allocations": []}, now)
    assert "QA" in result["short_reason"]
    assert "0건" in result["long_reason"]
    assert result["etf_best_probability"] == 55
    assert result["total_trades"] == 2
    assert "09:00" in result["next_check"]


def test_fifty_percent_observation_is_not_reused_as_a_hit_lesson():
    lesson = {"target_date": "2026-10-01", "direction": "up", "actual_direction": "up",
              "correct": True, "up_probability": 50, "strategy_return_pct": None,
              "tags": ["관찰"], "lesson": "중립 유지"}
    store = LessonStore(None)
    store.add(lesson)
    assert store.select_relevant(["관찰"]) == []
    rendered = build_lessons_text([lesson])
    assert "중립·방향 채점 제외" in rendered
    assert "적중" not in rendered


def test_independent_short_qa_status_does_not_inherit_broad_qa_failure():
    now = datetime(2026, 10, 2, 8, 30, tzinfo=timezone(timedelta(hours=9)))
    outcome = {"target_date": "2026-10-02", "final_qa": {"consistent": False},
               "verification": {"verification_passed": False},
               "final_result": {"etf_execution": {"schema": 3, "status": "approved",
                   "etf_qa": {"passed": True}, "selected_code": "069500", "weight": .10,
                   "forecasts": [{"code": "069500", "up_probability": 55}]}}}
    result = summarize(outcome, {}, {"status": "held"}, now)
    assert "ETF 전용 QA 승인" in result["short_reason"]
    assert result["etf_entry_probability_required"] == 50
    assert result["etf_entry_probability_strict"] is True


def test_activity_preserves_known_qa_reason_and_labels_old_unknown_reason():
    now = datetime(2026, 10, 6, 9, 25, tzinfo=timezone(timedelta(hours=9)))
    outcome = {"target_date": "2026-10-06", "source_registry": [
        {"id": "M901"}, {"id": "M902"}, {"id": "M903"}],
        "final_result": {"etf_execution": {"schema": 4, "status": "held",
                         "etf_qa": {"passed": False}}}}
    legacy = summarize(outcome, {}, {}, now)
    assert "세부 탈락 항목 미기록" in legacy["etf_qa_reason"]
    assert "과거시세 근거는 수집됨" in legacy["etf_qa_reason"]
    assert "첫 진입 시간 종료" in legacy["next_check"]
    outcome["final_result"]["etf_execution"]["etf_qa"] = {
        "passed": False, "reason_code": "citation_invalid", "reason": "종목별 근거 ID 검증에 실패함", "instrument": "114800"}
    detailed = summarize(outcome, {}, {}, now)
    assert detailed["etf_qa_reason_code"] == "citation_invalid"
    assert "114800" in detailed["etf_qa_reason"]
