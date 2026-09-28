import os
import sys
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from datetime import date
from memory.store import MemoryStore, PredictionRecord


def test_stock_verification_stores_detailed_actuals():
    memory = MemoryStore(None)
    memory.save(PredictionRecord(
        target_date="2026-07-29",
        run_date="2026-07-28",
        direction="up",
        confidence="높음",
        stocks=[{
            "name": "삼성전자",
            "ticker": "005930",
            "direction": "up",
            "predicted_date": "2026-07-29",
        }]
    ))

    memory.mark_verified(
        "2026-07-29",
        "up",
        stock_actuals={
            "삼성전자": {
                "ticker": "005930",
                "actual_date": "2026-07-29",
                "previous_close": 100000,
                "actual_close": 102000,
                "actual_return_pct": 2.0,
                "actual_direction": "up",
            }
        },
    )

    rec = memory.get("2026-07-29")
    stock = rec["stocks"][0]
    assert stock["ticker"] == "005930"
    assert stock["actual_return_pct"] == 2.0
    assert stock["actual_direction"] == "up"
    assert stock["correct"] is True


def test_stock_stats_include_accuracy_and_average_return():
    memory = MemoryStore(None)
    memory.save(PredictionRecord(
        target_date="2026-07-29", run_date="2026-07-28",
        direction="up", confidence="높음", verified=True,
        actual_direction="up", correct=True,
        stocks=[{
            "name": "삼성전자", "direction": "up",
            "actual_direction": "up", "correct": True,
            "actual_return_pct": 2.0
        }]
    ))
    memory.save(PredictionRecord(
        target_date="2026-07-30", run_date="2026-07-29",
        direction="down", confidence="보통", verified=True,
        actual_direction="up", correct=False,
        stocks=[{
            "name": "삼성전자", "direction": "down",
            "actual_direction": "up", "correct": False,
            "actual_return_pct": -1.0
        }]
    ))

    stats = memory.compute_stats()
    stock = stats["by_stock"]["삼성전자"]
    assert stock["total"] == 2
    assert stock["correct"] == 1
    assert stock["accuracy"] == 0.5
    assert stock["avg_return_pct"] == 0.5
