from data.market_data import IndexQuote
from rules.consensus import compute_consensus


def test_unanimous_verified_analysts_and_bull_evidence():
    reports = {name: {"direction": "up", "confidence": "보통", "citation_ok": True}
               for name in ("technical", "macro", "semi", "news")}
    qa = [{"stance": "bull", "verified_count": 3, "total_count": 3}]
    result = compute_consensus(reports, qa, [IndexQuote("SOX", 1.5, "up", "2026-10-21")])
    assert result["score"] == 1.0 and result["included_analysts"] == 4


def test_split_votes_and_uncited_analyst_excluded():
    reports = {"a": {"direction": "up", "confidence": "보통", "citation_ok": True},
               "b": {"direction": "up", "confidence": "보통", "citation_ok": True},
               "c": {"direction": "down", "confidence": "보통", "citation_ok": True},
               "d": {"direction": "down", "confidence": "보통", "citation_ok": True},
               "uncited": {"direction": "up", "confidence": "높음", "citation_ok": False}}
    result = compute_consensus(reports, [], [])
    assert result["score"] == 0 and result["analyst"] == 0
    assert result["included_analysts"] == 4 and result["excluded_analysts"] == ["uncited"]


def test_missing_or_invalid_evidence_does_not_create_direction():
    result = compute_consensus({}, [{"stance": "bull", "verified_count": 3, "total_count": 1}],
                               [{"name": "SOX", "change_pct": float("nan")}])
    assert result["score"] == 0 and result["verified_bull"] == 0
