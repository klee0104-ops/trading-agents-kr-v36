"""(V3.24) 출처 표기 강제 — 근거 자료 목록(ID·데이터원·매체·논문), 분석가 문장 폐기, QA 자동 미검증."""
from __future__ import annotations
import json

from data.market_data import IndexQuote, MarketSnapshot
from rules.citations import (build_registry, check_points, enforce_report, registry_text, public_registry,
                             parse_news_title, REFERENCES, CITATION_RULES)


def _snap():
    return MarketSnapshot(
        as_of="2026-09-28",
        us_indices=[IndexQuote("S&P500", 0.4, "up", "2026-09-28", 5000.0, 55.0, 1.2, 0.7),
                    IndexQuote("SOX", 1.1, "up", "2026-09-28", 5200.0, 61.0, 3.0, 0.8)],
        macro=[IndexQuote("원/달러", -0.35, "down", "2026-09-28", 1380.5)],
        kr_flow={"date": "2026-09-26", "foreign_net_krw_bn": -1234.5, "direction": "sell", "source": "naver"},
    )


_QUANT = {"last_close": 2600.0, "n_sessions": 250, "rsi14": 52.0,
          "macd": {"hist": 1.5, "hist_prev": 1.0}, "bb_pct_b": 0.6, "flow_z5": -0.4,
          "labels": {"rsi": "중립", "macd": "상승 모멘텀", "bb": "중립", "flow": "중립"}}
_NEWS = ["[2026-09-28][삼성전자][연합뉴스] 삼성전자 HBM 수주 기대", "[2026-09-28][SK하이닉스][매체 미상] 하이닉스 증설"]


def _reg():
    return build_registry(_snap(), _QUANT, _NEWS, "rss")


def test_registry_has_ids_sources_press_and_paper_refs():
    reg = _reg()
    ids = {e["id"] for e in reg}
    assert {"M1", "M2", "M3", "F1", "N1", "N2"} <= ids
    n1 = next(e for e in reg if e["id"] == "N1")
    assert n1["press"] == "연합뉴스" and "Google 뉴스 RSS" in n1["source"] and n1["date"] == "2026-09-28"
    m = next(e for e in reg if e["id"] == "M2")
    assert "Yahoo Finance" in m["source"] and "R1" in m["refs"]
    rsi = next(e for e in reg if e["kind"] == "Q" and e["text"].startswith("RSI"))
    assert rsi["refs"] == ["R1"]
    r1 = next(e for e in reg if e["id"] == "R1")
    assert "Wilder" in r1["text"] and "1978" in r1["text"] and "원문 인용 아님" in r1["text"]
    flow_q = next(e for e in reg if e["kind"] == "Q" and e["text"].startswith("외국인"))
    assert flow_q["refs"] == ["R9"] and "Richards" in REFERENCES["R9"]["cite"]
    txt = registry_text(reg)
    assert "[N1]" in txt and "출처: 연합뉴스" in txt and "공식 출처 R1" in txt


def test_every_reference_has_full_bibliography():
    for rid, ref in REFERENCES.items():
        assert rid.startswith("R") and "(" in ref["cite"] and ref["summary"]


def test_check_points_statuses():
    reg = _reg()
    got = {c["text"]: c["status"] for c in check_points([
        "SOX 등락 +1.10% [M2]",               # ok
        "SOX 가 강하다",                        # uncited
        "SOX 등락 +1.10% [M99]",              # unknown id
        "SOX 등락 +2.50% [M2]",               # number mismatch
        "외국인 순매도 1,234.5억원 [F1]",        # ok (콤마·단위)
        "RSI 52 중립 [Q2]",                    # 정수는 수치 대조 대상 아님 → ok
        "모멘텀 논문에 따르면 반등 [Q2]",         # 논문 언급인데 R 없음
    ], reg)}
    assert got["SOX 등락 +1.10% [M2]"] == "ok"
    assert got["SOX 가 강하다"] == "uncited"
    assert got["SOX 등락 +1.10% [M99]"] == "unknown_id"
    assert got["SOX 등락 +2.50% [M2]"] == "number_mismatch"
    assert got["외국인 순매도 1,234.5억원 [F1]"] == "ok"
    assert got["RSI 52 중립 [Q2]"] == "ok"
    assert got["모멘텀 논문에 따르면 반등 [Q2]"] == "uncited"


def test_news_analyst_must_cite_news_id():
    reg = _reg()
    rep = {"direction": "up", "confidence": "높음", "summary": "s",
           "notable_points": ["SOX 등락 +1.10% [M2]", "HBM 수주 기대 (연합뉴스) [N1]"]}
    enforce_report("NewsSentimentAnalyst", rep, reg)
    assert rep["citation_check"]["valid"] == 1 and rep["citation_check"]["rejected"][0]["reason"].startswith("N")
    assert "출처: 연합뉴스(2026-09-28)" in rep["notable_points"][0]
    assert rep["confidence"] == "높음" and rep["citation_ok"] is True


def test_enforce_drops_uncited_and_excludes_vote():
    from agents.specialists import combine_reports
    reg = _reg()
    bad = {"direction": "down", "confidence": "높음", "summary": "근거 없음", "notable_points": ["분위기 나쁨"]}
    good = {"direction": "up", "confidence": "보통", "summary": "ok", "notable_points": ["SOX +1.10% [M2]"]}
    enforce_report("MacroFlowAnalyst", bad, reg)
    enforce_report("SemiconductorAnalyst", good, reg)
    assert bad["citation_ok"] is False and bad["notable_points"] == [] and bad["confidence"] == "낮음"
    assert "Yahoo Finance" in good["notable_points"][0]
    r = combine_reports({"MacroFlowAnalyst": bad, "SemiconductorAnalyst": good})
    assert r["direction"] == "up" and r["down_votes"] == 0


def test_half_uncited_downgrades_confidence():
    reg = _reg()
    rep = {"direction": "up", "confidence": "높음", "summary": "s",
           "notable_points": ["SOX +1.10% [M2]", "a", "b"]}
    enforce_report("TechnicalAnalyst", rep, reg)
    assert rep["confidence"] == "보통" and rep["citation_check"] == {
        "valid": 1, "total": 3, "rejected": [{"point": "a", "reason": "출처 ID 없음"},
                                             {"point": "b", "reason": "출처 ID 없음"}]}


def test_qa_marks_uncited_evidence_unverified_even_if_llm_says_ok():
    from agents.base import MockLLMClient
    from agents.qa_verifier import verify_debate_evidence
    reg = _reg()
    transcript = [{"stance": "bull", "round": 1, "argument": "a",
                   "evidence": ["SOX 등락 +1.10% [M2]", "반도체 좋다", "SOX 등락 +9.90% [M2]"]}]
    out = verify_debate_evidence(MockLLMClient(), transcript, {"summary": "s", "_registry": reg}, "")
    r = out[0]
    assert r["total_count"] == 3 and r["verified_count"] == 1
    reasons = {c["evidence"]: c["reason"] for c in r["citation_rejected"]}
    assert reasons["반도체 좋다"] == "출처 ID 없음" and "9.9" in reasons["SOX 등락 +9.90% [M2]"]


def test_prompts_carry_citation_rules():
    from agents import specialists, researchers
    for p in (specialists.TECHNICAL_PROMPT, specialists.MACRO_PROMPT, specialists.SEMI_PROMPT,
              specialists.NEWS_PROMPT):
        assert CITATION_RULES in p and "[N" in CITATION_RULES and "[R" in CITATION_RULES
    assert "[매체]" in specialists.NEWS_PROMPT
    for p in (researchers.BULL_SYSTEM_PROMPT, researchers.BEAR_SYSTEM_PROMPT):
        assert "출처 필수" in p and "[N번]" in p and "[R번]" in p


def test_public_registry_hides_news_titles():
    pub = public_registry(_reg())
    blob = json.dumps(pub, ensure_ascii=False)
    assert "HBM 수주 기대" not in blob and "하이닉스 증설" not in blob
    assert any(e["id"] == "N1" and "연합뉴스" in e["source"] for e in pub)


def test_parse_news_title_old_and_new_format():
    assert parse_news_title("[2026-09-28][삼성전자][한국경제] 제목")["press"] == "한국경제"
    old = parse_news_title("[2026-09-28][삼성전자] 제목")
    assert old["press"] == "" and old["title"] == "제목"


def test_rss_press_feed_items_get_press_name(monkeypatch):
    from datetime import date
    from data import news_rss
    xml = ("<rss><channel><item><title>삼성전자 실적 발표</title><link>http://a</link>"
           "<pubDate>Mon, 28 Sep 2026 09:00:00 +0900</pubDate></item></channel></rss>")
    monkeypatch.setattr(news_rss, "fetch_press_feeds", lambda timeout=10: {"연합뉴스 경제": xml})
    monkeypatch.setattr(news_rss, "fetch_google_news", lambda *a, **k: [])
    titles = news_rss.fetch_titles_via_rss([{"code": "005930", "name": "삼성전자", "core": True}],
                                          date(2026, 9, 28), 2, 5, 3)
    assert titles and titles[0].startswith("[2026-09-28][삼성전자][연합뉴스] ")


def test_pipeline_records_public_registry_and_cited_points(monkeypatch):
    """전체 파이프라인(Mock): 분석가 문장에 출처가 붙고, Bull/Bear 근거가 ID로 검증되며,
    기록에는 뉴스 제목 없는 레지스트리만 남고 내부 _registry 는 저장되지 않는다."""
    from datetime import date
    from agents.base import MockLLMClient
    from memory.store import MemoryStore
    from graph.pipeline import TradingAgentsPipeline
    monkeypatch.setenv("TA_NO_NEWS", "1")
    snap = MarketSnapshot(as_of=str(date.today()), us_indices=[
        IndexQuote("S&P500", 0.4, "up", str(date.today())),
        IndexQuote("SOX", 1.1, "up", str(date.today())),
        IndexQuote("VIX", 2.0, "up", str(date.today()))])
    out = TradingAgentsPipeline(MockLLMClient(), MemoryStore(None), debate_max_rounds=2).run(snapshot=snap)
    assert "_registry" not in out["analyst_report"]
    assert {e["id"] for e in out["source_registry"]} >= {"M1", "M2", "M3"}
    semi = out["analyst_reports"]["SemiconductorAnalyst"]
    assert semi["citation_ok"] and "[M2]" in semi["notable_points"][0] and "Yahoo Finance" in semi["notable_points"][0]
    for r in out["debate_qa"]:
        assert r["citation_rejected"] == [] and r["verified_count"] == r["total_count"] > 0
