"""(V3.14) 정량 지표 → QA 대조 → 포지션이 파이프라인에서 이어지는지 (네트워크 없음)."""
import sys, os, random
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from datetime import date
from agents.base import MockLLMClient
from data.market_data import MarketSnapshot, IndexQuote, MarketDataProvider
from memory.store import MemoryStore
from graph.pipeline import TradingAgentsPipeline


class _HistProvider(MarketDataProvider):
    def __init__(self):
        super().__init__()
        random.seed(3)
        c = [2500.0]
        for _ in range(299):
            c.append(c[-1] * (1 + 0.0005 + random.gauss(0, 0.01)))
        self._c = c

    def fetch_kospi_history(self, as_of, sessions=260):
        c = self._c[-sessions:]
        return {"dates": [str(as_of)] * len(c), "closes": c,
                "highs": [x * 1.005 for x in c], "lows": [x * 0.995 for x in c]}


def _snap():
    return MarketSnapshot(as_of=str(date.today()), us_indices=[
        IndexQuote("S&P500", 0.4, "up", str(date.today())),
        IndexQuote("SOX", 1.1, "up", str(date.today())),
        IndexQuote("VIX", 2.0, "up", str(date.today()))],
        kr_flow={"date": str(date.today()), "foreign_net_krw_bn": 1200.0, "direction": "buy"})


def test_quant_flows_into_context_qa_and_position(monkeypatch):
    monkeypatch.setenv("TA_NO_NEWS", "1")
    memory = MemoryStore(None)
    pipe = TradingAgentsPipeline(MockLLMClient(), memory, data_provider=_HistProvider(), debate_max_rounds=2)
    out = pipe.run(snapshot=_snap())
    q = out["quant"]
    assert q and q["rsi14"] is not None and q["realized_vol_20"] > 0 and "labels" in q
    assert out["market_analysis"]["quant"]["rsi14"] == q["rsi14"]
    kp = out["final_result"]["kospi_prediction"]
    assert "position" in kp and -1 <= kp["position"] <= 1 and kp["position_info"]["reason"]
    for r in out["debate_qa"]:
        assert "quant_contradicted" in r or r.get("no_evidence_cited")
    rec = memory.all_records()[0]
    assert rec["position"] == kp["position"] and rec["realized_vol"] == q["realized_vol_20"]
    assert rec["foreign_net_krw_bn"] == 1200.0


def test_pipeline_without_history_still_runs(monkeypatch):
    monkeypatch.setenv("TA_NO_NEWS", "1")

    class _NoHist(MarketDataProvider):
        def fetch_kospi_history(self, as_of, sessions=260):
            raise RuntimeError("offline")
    out = TradingAgentsPipeline(MockLLMClient(), MemoryStore(None), data_provider=_NoHist(),
                                debate_max_rounds=1).run(snapshot=_snap())
    assert out["quant"] is None
    assert "position" in out["final_result"]["kospi_prediction"]
