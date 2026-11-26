from datetime import datetime, timedelta
import json
import sys

from data.sessions import KST
from execution.etf_basket import UNIVERSE, fresh, public_summary, summarize_evidence, tick, validate_plan


NOW = datetime(2026, 10, 11, 16, 0, tzinfo=KST)
OPEN = datetime(2026, 10, 12, 10, 0, tzinfo=KST)


def evidence():
    return {code: {"date": "2026-10-08", "close": 100_000.0} for code in UNIVERSE}


def proposal():
    return {"positions": [
        {"code": "069500", "weight_pct": 35, "reason": "국내 시장 노출을 제한해 담습니다 [M701]", "source_ids": ["M701"]},
        {"code": "379800", "weight_pct": 30, "reason": "미국 시장을 별도로 분산합니다 [M702]", "source_ids": ["M702"]},
        {"code": "132030", "weight_pct": 15, "reason": "금선물 특성을 고려해 보조로 담습니다 [M703]", "source_ids": ["M703"]},
    ]}


def quotes(now=OPEN):
    return {code: {"date": str(now.date()), "value": 100_000.0,
                   "observed_at": (now - timedelta(seconds=10)).isoformat(),
                   "traded_at": (now - timedelta(minutes=2)).isoformat(),
                   "market_status": "OPEN"} for code in UNIVERSE}


def test_basket_plan_requires_individual_evidence_and_cash():
    plan = validate_plan(proposal(), evidence(), NOW)
    assert plan["status"] == "approved"
    assert plan["target_date"] == "2026-10-12"
    assert plan["cash_weight_pct"] == 20
    bad = proposal()
    bad["positions"][0]["weight_pct"] = 45
    assert validate_plan(bad, evidence(), NOW)["status"] == "held"
    bad = proposal()
    bad["positions"][1]["reason"] = "미국 시장을 별도로 분산합니다 [M701]"
    assert validate_plan(bad, evidence(), NOW)["status"] == "held"
    old = evidence()
    old["379800"]["date"] = "2026-10-07"
    assert validate_plan(proposal(), old, NOW)["status"] == "held"


def test_separate_basket_fills_once_and_compares_same_time_benchmark():
    plan = validate_plan(proposal(), evidence(), NOW)
    account = fresh(NOW)
    result = tick(account, plan, quotes(), OPEN)
    assert result["executed_plan_id"] == plan["id"]
    assert len(result["trades"]) == 3
    assert result["benchmark"]["qty"] > 0
    assert result["cash"] > 1_900_000
    assert result["valuation_status"] == "ok"
    assert abs(result["equity"] - 10_000_000) < 15_000
    later = OPEN + timedelta(minutes=5)
    second = tick(result, plan, quotes(later), later)
    assert len(second["trades"]) == 3
    public = public_summary(second, plan)
    assert public["return_pct"] is not None
    assert public["benchmark_return_pct"] is not None
    assert public["initial_cash"] == 10_000_000
    assert "source_id" not in public


def test_basket_reports_actual_cost_and_each_etf_unrealized_result():
    from web.export_site import _sanitize_etf_basket

    plan = validate_plan(proposal(), evidence(), NOW)
    filled = tick(fresh(NOW), plan, quotes(), OPEN)
    later = OPEN + timedelta(minutes=5)
    moved = quotes(later)
    moved["069500"]["value"] = 105_000.0
    moved["379800"]["value"] = 97_000.0
    updated = tick(filled, plan, moved, later)
    public = _sanitize_etf_basket(public_summary(updated, plan))
    holdings = {row["code"]: row for row in public["holdings"]}

    assert holdings["069500"]["invested_cost"] > 0
    assert holdings["069500"]["unrealized_pnl"] > 0
    assert holdings["379800"]["unrealized_pnl"] < 0
    assert all(row["valued_at"] == later.isoformat() for row in holdings.values())
    assert abs(sum(row["invested_cost"] for row in holdings.values()) + public["cash"] - 10_000_000) < 1
    assert abs(sum(row["market_value"] for row in holdings.values()) + public["cash"] - public["equity"]) < 1
    assert all(abs(row["market_value"] - row["invested_cost"] - row["unrealized_pnl"]) < 1 for row in holdings.values())


def test_basket_stale_quote_keeps_cost_but_hides_per_etf_profit():
    from web.export_site import _sanitize_etf_basket

    plan = validate_plan(proposal(), evidence(), NOW)
    filled = tick(fresh(NOW), plan, quotes(), OPEN)
    later = OPEN + timedelta(minutes=5)
    stale = quotes(later)
    stale["379800"]["traded_at"] = (later - timedelta(minutes=30)).isoformat()
    blocked = tick(filled, plan, stale, later)
    public = _sanitize_etf_basket(public_summary(blocked, plan))
    assert public["valuation_status"] == "partial"
    assert "equity" not in public
    assert all(row["invested_cost"] > 0 and "unrealized_pnl" not in row
               and "market_value" not in row for row in public["holdings"])
    assert blocked["trades"] == filled["trades"]


def test_stale_or_forged_plan_never_fills():
    plan = validate_plan(proposal(), evidence(), NOW)
    stale = quotes()
    stale["379800"]["traded_at"] = (OPEN - timedelta(minutes=30)).isoformat()
    blocked = tick(fresh(NOW), plan, stale, OPEN)
    assert blocked["trades"] == []
    assert blocked["valuation_status"] == "partial"
    forged = dict(plan, positions=[dict(plan["positions"][0], weight_pct=100)])
    result = tick(fresh(NOW), forged, quotes(), OPEN)
    assert result["trades"] == []
    assert result["executed_plan_id"] is None
    assert tick(fresh(NOW), plan, quotes(), NOW)["trades"] == []


def test_public_basket_allowlist_drops_unapproved_fields():
    from web.export_site import _sanitize_etf_basket
    plan = validate_plan(proposal(), evidence(), NOW)
    safe = public_summary(fresh(NOW), plan)
    safe["private_note"] = "do not publish"
    safe["plan"]["internal_prompt"] = "do not publish"
    safe["plan"]["positions"][0]["raw_model"] = "do not publish"
    safe["plan"]["evidence_summary"] = {"069500": {"source_id": "M701", "date": "2026-10-08",
        "basis": "rechecked_after_creation", "retrieved_at": NOW.isoformat(),
        "first_close": 100, "last_close": 102.5, "change_pct": 2.5, "range_pct": 3.0,
        "up_sessions": 5, "down_sessions": 4, "secret": "do not publish"},
        "999999": {"secret": "do not publish"}}
    result = _sanitize_etf_basket(safe)
    assert "private_note" not in result
    assert "internal_prompt" not in result["plan"]
    assert "raw_model" not in result["plan"]["positions"][0]
    assert "secret" not in result["plan"]["evidence_summary"]["069500"]
    assert "999999" not in result["plan"]["evidence_summary"]


def test_evidence_summary_uses_only_ten_closes_and_labels_recheck():
    data = {code: {"date": "2026-10-08", "recent_closes": [100 + i for i in range(10)]}
            for code in UNIVERSE}
    summary = summarize_evidence(data, "rechecked_after_creation", NOW.isoformat())
    assert summary["069500"]["change_pct"] == 9.0
    assert summary["069500"]["range_pct"] == 9.0
    assert summary["069500"]["up_sessions"] == 9
    assert summary["069500"]["down_sessions"] == 0
    assert summary["069500"]["basis"] == "rechecked_after_creation"
    assert "recent_closes" not in summary["069500"]


def test_future_ai_detail_is_preserved_only_with_own_citation():
    from web.export_site import _sanitize_etf_basket

    raw = proposal()
    raw["positions"][0]["analysis"] = {
        "price_reading": "최근 종가의 상승 흐름을 관찰했습니다 [M701]",
        "allocation_thesis": "국내 주식 시장 노출의 중심으로 둡니다 [M701]",
        "countercase": "과거 종가만으로 이후 흐름을 확정할 수 없습니다 [M701]",
        "raw_prompt": "do not publish",
    }
    raw["positions"][1]["analysis"] = {
        "price_reading": "다른 ETF의 자료를 잘못 인용했습니다 [M701]",
        "allocation_thesis": "미국 주식의 축으로 배치합니다 [M702]",
        "countercase": "종가만으로 환율을 판단할 수 없습니다 [M702]",
    }
    plan = validate_plan(raw, evidence(), NOW)
    assert plan["status"] == "approved"
    assert set(plan["positions"][0]["analysis"]) == {"price_reading", "allocation_thesis", "countercase"}
    assert "analysis" not in plan["positions"][1]
    safe = _sanitize_etf_basket(public_summary(fresh(NOW), plan))
    assert "raw_prompt" not in safe["plan"]["positions"][0]["analysis"]
    assert safe["plan"]["positions"][0]["analysis"]["price_reading"].endswith("[M701]")


def test_backfill_rechecks_with_mock_and_preserves_original_plan(tmp_path, monkeypatch):
    from tools import etf_basket

    plan_path = tmp_path / "basket-plan.json"
    original = validate_plan(proposal(), evidence(), NOW)
    plan_path.write_text(json.dumps(original), encoding="utf-8")
    monkeypatch.setattr(etf_basket, "paths", lambda: (tmp_path / "basket.json", plan_path))
    fresh_data = {code: {"date": "2026-10-08", "recent_closes": [100 + i for i in range(10)]}
                  for code in UNIVERSE}
    assert etf_basket.backfill_evidence(fetch=lambda now: fresh_data) == "evidence_rechecked"
    updated = json.loads(plan_path.read_text(encoding="utf-8"))
    assert updated["id"] == original["id"]
    assert updated["positions"] == original["positions"]
    assert updated["evidence_summary"]["069500"]["basis"] == "rechecked_after_creation"
    assert json.loads((tmp_path / "basket-plan.json.before-evidence-backfill").read_text(encoding="utf-8")) == original
    assert etf_basket.backfill_evidence(fetch=lambda now: None) == "already_recorded"


def test_live_tick_checks_quotes_after_collection_not_before(tmp_path, monkeypatch):
    from tools import etf_basket

    account_path = tmp_path / "basket.json"
    plan_path = tmp_path / "basket_plan.json"
    account_path.write_text(json.dumps(fresh(NOW)), encoding="utf-8")
    plan = validate_plan(proposal(), evidence(), NOW)
    plan_path.write_text(json.dumps(plan), encoding="utf-8")
    monkeypatch.setattr(etf_basket, "paths", lambda: (account_path, plan_path))
    finished = OPEN + timedelta(seconds=3)
    clock = iter((OPEN, finished))
    monkeypatch.setattr(etf_basket, "kst_now", lambda: next(clock))

    def fetch(codes, requested_at):
        assert requested_at == OPEN
        result = quotes(finished)
        for row in result.values():
            row["observed_at"] = finished.isoformat()
        return {code: result[code] for code in codes}

    assert "모의 진입" in etf_basket.run_tick(fetch_quotes=fetch)
    result = json.loads(account_path.read_text(encoding="utf-8"))
    assert result["executed_at"] == finished.isoformat()
    assert result["last_tick"] == finished.isoformat()
    assert len(result["trades"]) == 3


def test_public_status_explains_pre_fill_quote_failure():
    state = fresh(NOW)
    state.update(last_tick=OPEN.isoformat(), valuation_status="partial", status="시세 지연으로 체결 보류")
    assert public_summary(state, validate_plan(proposal(), evidence(), NOW))["status"] == "시세 지연으로 체결 보류"


def test_scheduled_paper_tick_runs_isolated_basket_and_records_status(tmp_path, monkeypatch):
    from config import Config
    from memory import outcome_store
    from tools import etf_basket, paper_portfolio

    primary = tmp_path / "paper_portfolio.json"
    primary.write_text('{"sentinel":"untouched"}', encoding="utf-8")
    monkeypatch.setattr(Config, "PAPER_PORTFOLIO_PATH", str(primary))
    monkeypatch.setattr(outcome_store, "load_last_outcome", lambda path: None)
    monkeypatch.setattr(paper_portfolio, "run_once", lambda path, envelope: "market_closed")
    calls = []
    monkeypatch.setattr(etf_basket, "run_tick", lambda: calls.append("basket") or "market_closed")
    monkeypatch.setattr(sys, "argv", ["paper_portfolio", "--tick"])

    assert paper_portfolio.main() == 0
    assert calls == ["basket"]
    assert json.loads(primary.read_text(encoding="utf-8")) == {"sentinel": "untouched"}
    status = json.loads((tmp_path / "logs" / "paper_portfolio_status.json").read_text(encoding="utf-8"))
    assert status["status"] == status["basket_status"] == "market_closed"


def test_scheduled_basket_tick_survives_primary_failure(tmp_path, monkeypatch):
    from config import Config
    from memory import outcome_store
    from tools import etf_basket, paper_portfolio

    monkeypatch.setattr(Config, "PAPER_PORTFOLIO_PATH", str(tmp_path / "paper_portfolio.json"))
    monkeypatch.setattr(outcome_store, "load_last_outcome", lambda path: None)
    monkeypatch.setattr(paper_portfolio, "run_once", lambda path, envelope: (_ for _ in ()).throw(RuntimeError("primary error")))
    calls = []
    monkeypatch.setattr(etf_basket, "run_tick", lambda: calls.append("basket") or "plan_pending")
    monkeypatch.setattr(sys, "argv", ["paper_portfolio", "--tick"])

    assert paper_portfolio.main() == 1
    assert calls == ["basket"]
    status = json.loads((tmp_path / "logs" / "paper_portfolio_status.json").read_text(encoding="utf-8"))
    assert status["status"] == "failed"
    assert status["basket_status"] == "plan_pending"
