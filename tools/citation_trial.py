"""
(V3.29) 출처 표기 강제 실전 시험 — 실제 데이터 + 실제 LLM 으로 분석가 4명과 Bull/Bear 1라운드만 돌려
근거 번호가 얼마나 지켜지는지(폐기율)를 잰다.

- 예측 기록·교훈·모의계좌·공유 페이지를 전혀 건드리지 않는다 (MemoryStore(None), 게시 없음).
- LLM 호출 최대 8회: 분석가 4(뉴스 없으면 3) + Bull 1 + Bear 1 + QA 최대 2.
- 결과는 logs/citation_trial_YYYYMMDD_HHMM.json 에 저장(로컬 전용, 공개 안 함). 키·헤더는 기록하지 않는다.
사용: python -m tools.citation_trial        (실제 Gemini, 무료 한도 사용)
     python -m tools.citation_trial --mock (Mock LLM, 호출 비용 없음 — 동작 확인용)
"""
from __future__ import annotations
import argparse
import json
from collections import Counter
from pathlib import Path


def run_trial(llm, pipe, max_rounds: int = 1) -> dict:
    from agents.specialists import run_all_specialists, LAST_NEWS_SOURCE, SPECIALISTS
    from agents.qa_verifier import verify_debate_evidence
    from graph.debate_loop import run_debate_loop
    from rules.engine import analyze_market
    from rules.citations import build_registry
    snapshot = pipe.data_provider.fetch_us_snapshot()
    ma = analyze_market(snapshot)
    quant = pipe._quant_indicators(snapshot)
    ma.quant = quant
    universe = pipe._news_universe()
    titles = pipe._news_titles(snapshot, universe)
    reg = build_registry(snapshot, quant, titles, LAST_NEWS_SOURCE.get("source"))
    spec = run_all_specialists(llm, snapshot, ma, titles, registry=reg)
    report = spec["combined"]
    report["_registry"] = reg
    transcript = run_debate_loop(llm, report, max_rounds=max_rounds)
    qa = verify_debate_evidence(llm, transcript, report, "", quant=quant)
    report.pop("_registry", None)

    label = dict(SPECIALISTS)
    analysts = {}
    reasons = Counter()
    for name, r in spec["reports"].items():
        cc = r.get("citation_check") or {}
        for x in cc.get("rejected", []):
            reasons[x["reason"].split(" ")[0] if x["reason"].startswith("수치") else x["reason"]] += 1
        analysts[label.get(name, name)] = {
            "skipped": bool(r.get("skipped")), "direction": r.get("direction"), "confidence": r.get("confidence"),
            "valid": cc.get("valid"), "total": cc.get("total"), "citation_ok": r.get("citation_ok"),
            "kept_points": r.get("notable_points", []),
            "rejected": cc.get("rejected", []),
        }
    debate = []
    for t, q in zip(transcript, qa):
        for x in q.get("citation_rejected", []):
            reasons["(토론) " + x["reason"]] += 1
        debate.append({"stance": t.get("stance"), "evidence": t.get("evidence", []),
                       "verified": q.get("verified_count"), "total": q.get("total_count"),
                       "qa_unmatched": q.get("qa_unmatched"),
                       "citation_rejected": q.get("citation_rejected", [])})
    a_total = sum(v["total"] or 0 for v in analysts.values())
    a_valid = sum(v["valid"] or 0 for v in analysts.values())
    d_total = sum(d["total"] or 0 for d in debate)
    d_bad = sum(len(d["citation_rejected"]) for d in debate)
    return {
        "as_of": snapshot.as_of, "news_titles": len(titles), "registry_size": len(reg),
        "registry_kinds": dict(Counter(e["kind"] for e in reg)),
        "combined_direction": report.get("direction"), "votes": report.get("votes"),
        "analyst_points": {"total": a_total, "valid": a_valid,
                           "reject_rate": round(1 - a_valid / a_total, 3) if a_total else None},
        "debate_evidence": {"total": d_total, "citation_rejected": d_bad,
                            "reject_rate": round(d_bad / d_total, 3) if d_total else None},
        "reject_reasons": dict(reasons), "analysts": analysts, "debate": debate,
        "llm_calls": len(getattr(llm, "call_log", [])) or None,
    }


def main(argv=None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--mock", action="store_true")
    args = ap.parse_args(argv)
    from config import Config
    from memory.store import MemoryStore
    from graph.pipeline import TradingAgentsPipeline
    from data.sessions import kst_now
    if args.mock:
        from agents.base import MockLLMClient
        llm = MockLLMClient()
    else:
        from cli import _real_llm
        llm = _real_llm()
    pipe = TradingAgentsPipeline(llm, MemoryStore(None), debate_max_rounds=1)  # 기록 저장 안 함
    out = run_trial(llm, pipe)
    out["mode"] = "mock" if args.mock else f"real:{Config.LLM_PROVIDER}"
    out["run_at"] = kst_now().isoformat(timespec="seconds")
    Path("logs").mkdir(exist_ok=True)
    path = Path("logs") / f"citation_trial_{kst_now():%Y%m%d_%H%M}.json"
    path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    a, d = out["analyst_points"], out["debate_evidence"]
    print(f"[출처 시험] {out['mode']} · 기준 {out['as_of']} · 근거목록 {out['registry_size']}개 {out['registry_kinds']}")
    print(f"  분석가 문장 {a['valid']}/{a['total']} 통과 (폐기율 {a['reject_rate']})")
    for k, v in out["analysts"].items():
        print(f"   - {k}: {'생략' if v['skipped'] else str(v['valid']) + '/' + str(v['total'])} "
              f"{'다수결 제외' if v['citation_ok'] is False else ''}")
    print(f"  토론 근거 {d['total'] - d['citation_rejected']}/{d['total']} 출처 통과 (폐기율 {d['reject_rate']})")
    print(f"  폐기 사유: {out['reject_reasons']}")
    print(f"  저장: {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
