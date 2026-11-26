from dataclasses import asdict
import json
from memory.store import MemoryStore, PredictionRecord, AgentPrediction


def _rec(target, direction, p, pos, vol=15.0, us=None):
    return PredictionRecord(
        target_date=target, run_date=target, direction=direction, confidence="보통",
        us_indices=us or [{"name": "SOX", "direction": direction}, {"name": "S&P500", "direction": "up"}],
        agent_predictions=[asdict(AgentPrediction(agent="Trader", role="final_decision", direction=direction,
                                                  confidence="보통", predicted_date=target)),
                           asdict(AgentPrediction(agent="MarketAnalyst", role="market_analysis",
                                                  direction="up", confidence="보통", predicted_date=target))],
        up_probability=p, position=pos, realized_vol=vol, foreign_net_krw_bn=1000.0,
    )


def test_mark_verified_scores_returns_and_stats():
    m = MemoryStore(None)
    m.save(_rec("2026-09-21", "up", 70, 0.4))
    m.save(_rec("2026-09-22", "down", 30, -0.4))
    m.save(_rec("2026-09-23", "up", 52, 0.0))
    m.mark_verified("2026-09-21", "up", actual_change_pct=1.0, etf_returns={"long": 0.9})
    m.mark_verified("2026-09-22", "up", actual_change_pct=2.0)   # 쇼트인데 상승 → 손실, 지수 근사
    m.mark_verified("2026-09-23", "down", actual_change_pct=-1.0)  # 현금 → 0
    recs = {r["target_date"]: r for r in m.all_records()}
    assert recs["2026-09-21"]["strategy_return_pct"] == round(0.4 * 0.9 - 0.04, 4)
    assert recs["2026-09-21"]["return_proxy"] is False
    assert recs["2026-09-22"]["strategy_return_pct"] == round(-0.4 * 2.0 - 0.04, 4)
    assert recs["2026-09-22"]["return_proxy"] is True
    assert recs["2026-09-23"]["strategy_return_pct"] == 0.0
    trader = [a for a in recs["2026-09-21"]["agent_predictions"] if a["agent"] == "Trader"][0]
    assert trader["strategy_return_pct"] == round(1.0 * 1.0 - 0.10, 4)   # 방향 100% 포지션, 지수 근사

    st = m.compute_stats()
    rb = st["returns"]
    assert rb["days"] == 3 and rb["ai"]["traded_days"] == 2
    assert rb["buy_hold"]["total_return_pct"] != rb["ai"]["total_return_pct"]
    assert "sox" in rb["baselines"] and "MarketAnalyst" in rb["by_agent"]
    assert isinstance(rb["ai_beats_buy_hold"], bool)
    assert m.foreign_flow_history() == [1000.0, 1000.0, 1000.0]
    assert "수익률(포지션 기준" in m.build_history_context_text()


def test_winning_only_history_is_strict_json_for_dashboard(tmp_path, monkeypatch):
    from config import Config
    from web.server import app

    path = tmp_path / "memory.json"
    monkeypatch.setattr(Config, "MEMORY_DB_PATH", str(path))
    monkeypatch.setattr(Config, "LESSONS_PATH", str(tmp_path / "lessons.json"))
    memory = MemoryStore(str(path))
    memory.save(_rec("2026-09-21", "up", 70, 0.4))
    memory.mark_verified("2026-09-21", "up", actual_change_pct=1.0,
                         etf_returns={"long": 0.9})

    response = app.test_client().get("/api/history", headers={"Host": f"127.0.0.1:{Config.WEB_PORT}"})
    assert response.status_code == 200

    def reject_constant(value):
        raise ValueError(f"Non-JSON number: {value}")

    body = json.loads(response.get_data(as_text=True), parse_constant=reject_constant)
    assert body["stats"]["returns"]["ai"]["profit_factor"] is None
