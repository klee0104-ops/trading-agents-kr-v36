"""
저장형 백테스트 (V3.6.1).

- 대상: start~end 사이 "실제 KOSPI 거래일" T 각각. T를 예측할 때는 T 전날까지의 미국장
  데이터만 쓴다 (as_of = T-1일, look-ahead 방지). 공휴일·연휴 뒤 첫 거래일도 자연스럽게 처리된다.
- 하루 끝날 때마다 backtest_results.json에 즉시 저장 → 중간에 끊겨도(무료 요금제 일일 한도 등)
  다시 실행하면 끝난 날은 건너뛰고 이어서 한다.
- 실제 운영 기록(memory_store.json)과 완전히 분리된다. 백테스트용 메모리는 인메모리라
  과거 실적 컨텍스트/가중치도 운영 기록에 영향을 주지 않는다.

⚠️ 한계: LLM은 학습 시점까지의 시장 흐름을 알고 있을 수 있어 백테스트 적중률은 낙관적인
참고치다. 실제 성적은 운영 기록으로 판단한다.
"""
from __future__ import annotations

import json
import os
import time
from datetime import date, timedelta, datetime

from backtest.performance import compute_performance
from execution.portfolio import optimize_portfolio
from execution.simulator import simulate_day
from rules.baselines import BASELINES
from security.secret_store import redact

DEFAULT_PATH = "./backtest_results.json"


def _load(path: str) -> dict:
    if os.path.exists(path):
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"meta": {}, "days": {}}


def _save(path: str, data: dict) -> None:
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(redact(json.dumps(data, ensure_ascii=False, indent=2, default=str)))
    os.replace(tmp, path)


def _log(msg: str, log_path: str | None):
    line = f"[{datetime.now():%H:%M:%S}] {msg}"
    print(line, flush=True)
    if log_path:
        with open(log_path, "a", encoding="utf-8") as f:
            f.write(redact(line) + "\n")


def run_backtest_persist(pipeline, provider, start: date, end: date, path: str = DEFAULT_PATH,
                         log_path: str | None = "./backtest_log.txt", model_label: str = "",
                         run_label: str = "") -> dict:
    data = _load(path)
    data["meta"].update({"start": str(start), "end": str(end), "model": model_label,
                         "label": run_label or data["meta"].get("label", ""),
                         "note": "LLM 사전지식 영향 가능 — 낙관적 참고치"})
    sessions = provider.fetch_kospi_sessions(start, end)
    _log(f"백테스트 시작: {start}~{end}, 대상 거래일 {len(sessions)}일 "
         f"(이미 완료 {sum(1 for s in sessions if str(s['date']) in data['days'])}일)", log_path)

    for i, s in enumerate(sessions, 1):
        key = str(s["date"])
        if key in data["days"] and data["days"][key].get("predicted"):
            continue
        as_of = s["date"] - timedelta(days=1)
        t0 = time.time()
        try:
            snapshot = provider.fetch_us_snapshot(as_of_date=as_of)
            out = pipeline.run(snapshot=snapshot)
            fr = out["final_result"]
            pred = fr.get("kospi_prediction", {})
            risk_level = (out.get("verification") or {}).get("final_risk_level") \
                or (out.get("risk_assessment") or {}).get("risk_level", "보통")
            port = optimize_portfolio(fr, risk_level)
            daily_ret = simulate_day(port, s["change_pct"])
            # (V3.14) 롱/쇼트/현금 포지션 수익률 (지수 근사, 왕복 10bp 차감)
            from execution.position import strategy_return_pct
            pos = float(pred.get("position") or 0.0)
            sr = strategy_return_pct(pos, s["change_pct"]) or {"return_pct": 0.0}
            qa = {"bull": [0, 0], "bear": [0, 0]}
            for r in out.get("debate_qa") or []:
                if r.get("stance") in qa:
                    qa[r["stance"]][0] += r.get("verified_count", 0)
                    qa[r["stance"]][1] += r.get("total_count", 0)
            data["days"][key] = {
                "target_session": key,
                "us_as_of": max((q.as_of for q in snapshot.us_indices), default=None),
                "us_indices": [{"name": q.name, "direction": q.direction, "change_pct": q.change_pct}
                               for q in snapshot.us_indices],
                "macro": [{"name": q.name, "change_pct": q.change_pct, "value": q.value}
                          for q in (snapshot.macro or [])],
                "kr_flow": snapshot.kr_flow,
                "predicted": pred.get("direction"),
                "confidence": pred.get("confidence"),
                "up_probability": pred.get("up_probability"),
                "outlook": pred.get("outlook"),
                "reasoning": pred.get("reasoning", [])[:3],
                "actual": s["direction"],
                "actual_change_pct": s["change_pct"],
                "correct": pred.get("direction") == s["direction"],
                "baselines": {k: v["direction"] for k, v in (out.get("baselines") or {}).items()},
                "debate_quality": (out.get("moderation") or {}).get("debate_quality"),
                "analysts": {n: {"direction": r.get("direction"), "confidence": r.get("confidence")}
                             for n, r in (out.get("analyst_reports") or {}).items()},
                "risk_level": risk_level,
                "rule_risk_hint": (out.get("market_analysis") or {}).get("risk_level_hint"),
                "rule_direction_hint": (out.get("market_analysis") or {}).get("direction_hint"),
                "rule_flags": (out.get("market_analysis") or {}).get("risk_flags"),
                "qa_evidence": qa,
                "risk_stage_passed": (out.get("verification") or {}).get("verification_passed"),
                "final_consistent": (out.get("final_qa") or {}).get("consistent"),
                "llm_final_qa_triggered": (out.get("final_qa") or {}).get("llm_verification_triggered"),
                "daily_return": daily_ret,
                "benchmark_return": s["change_pct"] / 100.0,
                "position": pos,
                "position_reason": (pred.get("position_info") or {}).get("reason"),
                "strategy_return_pct": sr["return_pct"],
                "realized_vol": ((out.get("quant") or {}).get("realized_vol_20")),
                "quant_contradicted": sum(len(r.get("quant_contradicted") or []) for r in (out.get("debate_qa") or [])),
                "elapsed_sec": round(time.time() - t0, 1),
            }
            _save(path, data)
            d = data["days"][key]
            _log(f"({i}/{len(sessions)}) {key} 예측 {d['predicted']}({d['confidence']}) / 실제 {d['actual']} "
                 f"{'적중' if d['correct'] else '빗나감'}  [{d['elapsed_sec']}초]", log_path)
        except Exception as e:
            msg = redact(f"{type(e).__name__}: {e}")[:300]
            _log(f"({i}/{len(sessions)}) {key} 실패 — {msg}", log_path)
            if "429" in msg or "RESOURCE_EXHAUSTED" in msg or "quota" in msg.lower():
                _log("무료 요금제 한도에 걸린 것으로 보입니다. 저장된 날까지는 보존됩니다. "
                     "잠시(또는 내일) 후 같은 파일을 다시 실행하면 이어서 진행합니다.", log_path)
                break

    summary = summarize_persisted(data)
    data["summary"] = summary
    _save(path, data)
    _log("요약: " + json.dumps({k: summary[k] for k in ("total", "correct", "accuracy")}, ensure_ascii=False),
         log_path)
    return summary


def summarize_persisted(data: dict) -> dict:
    days = [d for d in sorted(data.get("days", {}).values(), key=lambda x: x["target_session"])
            if d.get("predicted")]
    total = len(days)
    correct = sum(1 for d in days if d["correct"])
    base = {}
    for k, label in BASELINES.items():
        rows = [d for d in days if k in (d.get("baselines") or {})]
        c = sum(1 for d in rows if d["baselines"][k] == d["actual"])
        base[k] = {"label": label, "total": len(rows), "correct": c, "accuracy": (c / len(rows)) if rows else None}
    by_conf = {}
    for d in days:
        b = by_conf.setdefault(d.get("confidence") or "?", {"total": 0, "correct": 0})
        b["total"] += 1
        b["correct"] += int(d["correct"])
    ev = {"bull": [0, 0], "bear": [0, 0]}
    for d in days:
        for st in ev:
            ev[st][0] += d["qa_evidence"][st][0]
            ev[st][1] += d["qa_evidence"][st][1]
    from rules.outlook import calibration
    calib = calibration([{"up_probability": d.get("up_probability"), "actual": d["actual"]} for d in days])
    by_analyst = {}
    for d in days:
        for n, a in (d.get("analysts") or {}).items():
            if a.get("direction") not in ("up", "down"):
                continue
            b = by_analyst.setdefault(n, {"total": 0, "correct": 0, "correct_but_ignored": 0, "wrong_but_overruled": 0})
            b["total"] += 1
            ok = a["direction"] == d["actual"]
            b["correct"] += int(ok)
            if a["direction"] != d["predicted"]:
                if ok and not d["correct"]:
                    b["correct_but_ignored"] += 1
                elif (not ok) and d["correct"]:
                    b["wrong_but_overruled"] += 1
    for b in by_analyst.values():
        b["accuracy"] = b["correct"] / b["total"] if b["total"] else None
        b["contribution"] = b["correct_but_ignored"] - b["wrong_but_overruled"]
    by_dq = {}
    for d in days:
        if d.get("debate_quality"):
            b = by_dq.setdefault(d["debate_quality"], {"total": 0, "correct": 0})
            b["total"] += 1
            b["correct"] += int(d["correct"])
    by_outlook = {}
    for d in days:
        if d.get("outlook"):
            b = by_outlook.setdefault(d["outlook"], {"total": 0, "correct": 0})
            b["total"] += 1
            b["correct"] += int(d["correct"])
    returns = [d["daily_return"] for d in days]
    bench = [d["benchmark_return"] for d in days]
    return {
        "total": total, "correct": correct, "accuracy": (correct / total) if total else None,
        "baseline_compare": base, "by_confidence": by_conf,
        "evidence": {st: {"verified": v[0], "total": v[1]} for st, v in ev.items()},
        "final_qa_llm_triggered": sum(1 for d in days if d.get("llm_final_qa_triggered")),
        "final_inconsistent": sum(1 for d in days if d.get("final_consistent") is False),
        "calibration": calib,
        "by_outlook": by_outlook,
        "by_debate_quality": by_dq,
        "by_analyst": by_analyst,
        "performance": _perf_with_benchmark(returns, bench),
        "returns": _returns_block(days),
    }


def _returns_block(days: list[dict]) -> dict:
    """(V3.14) 수익률 기준: AI 포지션 vs 바이앤홀드 vs 베이스라인 vs 분석가별 (모두 같은 날, 지수 근사, 10bp)."""
    from execution.position import performance, direction_position, strategy_return_pct
    rows = [d for d in days if d.get("actual_change_pct") is not None]
    if not rows:
        return {"days": 0}
    def _ret(direction, d):
        sr = strategy_return_pct(direction_position(direction, d.get("realized_vol")), d["actual_change_pct"])
        return sr["return_pct"] if sr else None
    ai = []
    for d in rows:
        v = d.get("strategy_return_pct")
        ai.append(v if v is not None else _ret(d.get("predicted"), d))   # 포지션 도입 전 결과는 방향 100%로 소급
    out = {"days": len(rows), "dates": [d["target_session"] for d in rows], "cost_bps_roundtrip": 10,
           "ai": performance(ai, positions=[d.get("position", 1.0 if d.get("predicted") else 0.0) for d in rows]),
           "buy_hold": performance([d["actual_change_pct"] for d in rows]),
           "baselines": {}, "by_agent": {}}
    for k, label in BASELINES.items():
        vals = [_ret(d["baselines"][k], d) for d in rows if k in (d.get("baselines") or {})]
        vals = [v for v in vals if v is not None]
        if vals:
            out["baselines"][k] = {"label": label, **performance(vals)}
    names = {n for d in rows for n in (d.get("analysts") or {})}
    for n in sorted(names):
        vals = [_ret(d["analysts"][n].get("direction"), d) for d in rows if n in (d.get("analysts") or {})]
        vals = [v for v in vals if v is not None]
        if vals:
            out["by_agent"][n] = performance(vals)
    best = max(out["baselines"].items(), key=lambda kv: kv[1].get("total_return_pct", -1e9), default=(None, None))
    out["best_baseline"] = best[0]
    out["ai_beats_buy_hold"] = out["ai"]["total_return_pct"] > out["buy_hold"]["total_return_pct"]
    out["ai_beats_best_baseline"] = best[1] is None or out["ai"]["total_return_pct"] > best[1]["total_return_pct"]
    return out


def _perf_with_benchmark(returns, bench) -> dict:
    if not returns:
        return {}
    p = compute_performance(returns, bench)
    eq = 1.0
    for b in bench:
        eq *= (1 + b)
    p["benchmark_total_return"] = round(eq - 1, 4)  # 같은 기간 KOSPI 그냥 보유했을 때
    return p
