"""
예측 기록 저장소. JSON 파일 기반 (프로덕션에서는 SQLite/Postgres로 교체 권장).

이전에 만든 HTML 아티팩트의 window.storage 기반 트랙 레코드 로직과
동일한 개념을 파이썬으로 옮긴 것. 종목별/지표별 적중률을 집계해서
다음 분석 프롬프트에 "참고 맥락"으로 주입하는 데 쓴다 (self-learning 루프).
"""
from __future__ import annotations
import json
import os
from dataclasses import dataclass, field, asdict
from datetime import date, timedelta
from typing import Optional

from rules.baselines import score_baselines
from rules.outlook import calibration


def stock_is_correct(s: dict) -> Optional[bool]:
    """(V3.11) up/down은 방향 일치, flat(보합)은 |실제 등락률| < STOCK_FLAT_PCT 이면 적중.
    등락률이 없으면 방향만 비교(flat은 판정 불가 → None)."""
    from config import Config
    pred = s.get("direction")
    ret = s.get("actual_return_pct")
    if pred == "flat":
        return (abs(float(ret)) < Config.STOCK_FLAT_PCT) if ret is not None else None
    if ret is not None and abs(float(ret)) < 1e-9:
        return pred == "up"
    return (s.get("actual_direction") == pred) if s.get("actual_direction") else None


def next_trading_day(d: date) -> date:
    nd = d + timedelta(days=1)
    while nd.weekday() >= 5:  # 5=토, 6=일
        nd += timedelta(days=1)
    return nd


@dataclass
class AgentPrediction:
    agent: str
    role: str
    direction: Optional[str] = None
    confidence: Optional[object] = None
    round: Optional[int] = None
    predicted_date: Optional[str] = None
    actual_direction: Optional[str] = None
    correct: Optional[bool] = None
    # Bull/Bear처럼 더 이상 고정 방향을 주장하지 않는 에이전트는 direction 기반
    # 적중률 대신, QA 검증팀이 확인한 "근거 검증률"로 품질을 추적한다.
    evidence_total: Optional[int] = None
    evidence_verified: Optional[int] = None


@dataclass
class StockPrediction:
    name: str
    sector: str = ""
    related_indicator: str = ""
    sensitivity: str = "mid"
    direction: Optional[str] = None
    ticker: Optional[str] = None
    predicted_date: Optional[str] = None
    actual_date: Optional[str] = None
    predicted_price: Optional[float] = None
    previous_close: Optional[float] = None
    actual_close: Optional[float] = None
    actual_return_pct: Optional[float] = None
    actual_direction: Optional[str] = None
    correct: Optional[bool] = None
    # (V3.11) 관심 종목 추첨·중재 기록
    is_core: bool = False
    moderated: bool = False              # 종목 중재자가 보합으로 조정했는지
    original_direction: Optional[str] = None


@dataclass
class PredictionRecord:
    target_date: str
    run_date: str
    direction: str
    confidence: str
    verified: bool = False
    actual_direction: Optional[str] = None
    correct: Optional[bool] = None
    us_indices: list = field(default_factory=list)  # [{"name":..,"direction":..}]
    stocks: list = field(default_factory=list)  # StockPrediction dict 목록
    agent_predictions: list = field(default_factory=list)  # AgentPrediction dict 목록
    qa_final_consistent: Optional[bool] = None  # 상위 QA: 최종 결론이 토론/리스크와 정합적인지
    qa_final_issues: list = field(default_factory=list)
    # (V3.6.1) 예측 대상 기준: 이 날짜 "이후 처음 열린" KOSPI 거래일로 채점한다
    score_after_date: Optional[str] = None
    run_at: Optional[str] = None  # 실행 시각 (KST)
    # (V3.6.3) 확률 예측 + 5단계 전망 (신뢰도는 확률에서 도출)
    up_probability: Optional[float] = None
    outlook: Optional[str] = None
    debate_quality: Optional[str] = None  # (V3.8) resolved|contested|unresolved — 토론 품질별 적중률 채점용
    # (V3.14) 수익률 채점: 포지션(-1~+1, 롱=KODEX200/쇼트=KODEX인버스/0=현금), 당시 실현변동성, 외국인 순매수(억원)
    position: Optional[float] = None
    position_info: Optional[dict] = None
    realized_vol: Optional[float] = None
    foreign_net_krw_bn: Optional[float] = None
    # (V3.16) 학습: 보정 전 확률, 상황 태그, 복기용 요약(그날의 전 과정 압축), 복기 결과
    up_probability_raw: Optional[float] = None
    calibration_note: Optional[str] = None
    situation_tags: list = field(default_factory=list)
    outcome_digest: Optional[dict] = None
    postmortem: Optional[dict] = None


class MemoryStore:
    def __init__(self, path: str | None):
        """
        path=None 이면 디스크에 쓰지 않는 순수 인메모리 모드로 동작한다.
        (백테스트가 실거래 기록 파일과 섞이지 않도록 하기 위함)
        """
        self.path = path
        self._in_memory_data: dict = {}
        if self.path is not None and not os.path.exists(self.path):
            self._write({})

    def _read(self) -> dict:
        if self.path is None:
            return self._in_memory_data
        with open(self.path, "r", encoding="utf-8") as f:
            return json.load(f)

    def _write(self, data: dict):
        if self.path is None:
            self._in_memory_data = data
            return
        with open(self.path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    def save(self, record: PredictionRecord):
        data = self._read()
        data[record.target_date] = asdict(record)
        self._write(data)

    def get(self, target_date: str) -> Optional[dict]:
        return self._read().get(target_date)

    def all_records(self) -> list[dict]:
        return list(self._read().values())

    def pending_records(self, today: date, include_today: bool = False) -> list[dict]:
        """미채점 기록. include_today=True 면 대상일이 오늘인 것도 포함(장 마감 후 즉시 채점용)."""
        return [
            r for r in self.all_records()
            if not r["verified"] and (r["target_date"] < str(today) or (include_today and r["target_date"] == str(today)))
        ]

    def superseded_records(self) -> list[dict]:
        """(V3.22) 같은 대상일의 더 새로운 예측으로 대체된 옛 기록 (통계·채점 제외, 복기용 보관)."""
        if self.path is None:
            return []
        p = os.path.splitext(self.path)[0] + "_superseded.json"
        if not os.path.exists(p):
            return []
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)

    def recompute_legacy_returns(self) -> int:
        """(V3.22) 포지션 없이 채점돼 수익률 0.0 으로 저장된 옛 기록을 방향 100% 규칙으로 다시 계산. 바뀐 건수 반환."""
        from execution.position import direction_position, strategy_return_pct
        data = self._read(); n = 0
        for k, r in data.items():
            if r.get("verified") and r.get("position") is None and r.get("actual_change_pct") is not None:
                etf = r.get("etf_returns") or {}
                sr = strategy_return_pct(direction_position(r.get("direction"), r.get("realized_vol")), r["actual_change_pct"],
                                         long_etf_ret_pct=etf.get("long"), short_etf_ret_pct=etf.get("short"))
                if sr and sr["return_pct"] != r.get("strategy_return_pct"):
                    r["strategy_return_pct"], r["strategy_leg"], r["return_proxy"] = sr["return_pct"], sr["leg"], sr["proxy"]
                    r["position_legacy"] = True; n += 1
            # 에이전트별 수익률이 없는 옛 기록도 같은 규칙으로 채운다 (방향 100% × 변동성 스케일, 지수 근사)
            if r.get("verified") and r.get("actual_change_pct") is not None:
                for a in r.get("agent_predictions", []):
                    if a.get("direction") in ("up", "down") and a.get("strategy_return_pct") is None:
                        sa = strategy_return_pct(direction_position(a["direction"], r.get("realized_vol")), r["actual_change_pct"])
                        a["strategy_return_pct"] = sa["return_pct"] if sa else None; n += 1
        if n:
            self._write(data)
        return n

    def attach_postmortem(self, target_date: str, pm: dict) -> None:
        data = self._read()
        if target_date in data:
            data[target_date]["postmortem"] = pm
            self._write(data)

    def foreign_flow_history(self, limit: int = 40) -> list[float]:
        """(V3.14) 기록에 남은 외국인 순매수(억원)를 실행일 순으로. 정량 지표의 z-score 입력."""
        rows = sorted(self.all_records(), key=lambda r: r.get("run_date", ""))
        vals = [r.get("foreign_net_krw_bn") for r in rows if r.get("foreign_net_krw_bn") is not None]
        return [float(v) for v in vals][-limit:]

    def mark_verified(self, target_date: str, actual_direction: str,
                       stock_actuals: Optional[dict] = None,
                       actual_session_date: Optional[str] = None,
                       actual_change_pct: Optional[float] = None,
                       etf_returns: Optional[dict] = None):
        data = self._read()
        rec = data.get(target_date)
        if not rec:
            return
        rec["actual_direction"] = actual_direction
        if actual_session_date:
            rec["actual_session_date"] = actual_session_date  # 실제 채점한 거래일 (공휴일이면 target과 다름)
        if actual_change_pct is not None:
            rec["actual_change_pct"] = actual_change_pct
        rec["correct"] = (actual_direction == rec["direction"])
        rec["verified"] = True
        # (V3.14) 수익률 채점: 포지션 × 실제 수익률 − 비용. ETF 실제 수익률이 있으면 그걸, 없으면 지수로 근사.
        if actual_change_pct is not None:
            from execution.position import strategy_return_pct
            etf = etf_returns or {}
            from execution.position import direction_position
            pos = rec.get("position")
            if pos is None:  # (V3.22) 포지션 도입 전 기록: 0(현금)이 아니라 방향 100%로 소급 (통계와 같은 규칙)
                pos = direction_position(rec.get("direction"), rec.get("realized_vol"))
                rec["position_legacy"] = True
            sr = strategy_return_pct(pos, actual_change_pct,
                                     long_etf_ret_pct=etf.get("long"), short_etf_ret_pct=etf.get("short"))
            if sr:
                rec["strategy_return_pct"] = sr["return_pct"]
                rec["strategy_leg"] = sr["leg"]
                rec["return_proxy"] = sr["proxy"]
                if etf:
                    rec["etf_returns"] = etf
        if stock_actuals:
            for s in rec.get("stocks", []):
                actual = stock_actuals.get(s["name"])
                if not actual:
                    continue
                # V2는 상세 검증 결과(dict)를 저장한다.
                # 기존 문자열("up"/"down") 입력도 하위 호환한다.
                if isinstance(actual, dict):
                    s["ticker"] = actual.get("ticker", s.get("ticker"))
                    s["actual_date"] = actual.get("actual_date")
                    s["previous_close"] = actual.get("previous_close")
                    s["actual_close"] = actual.get("actual_close")
                    s["actual_return_pct"] = actual.get("actual_return_pct")
                    s["actual_direction"] = actual.get("actual_direction")
                else:
                    s["actual_direction"] = actual
                s["correct"] = stock_is_correct(s)

        # 에이전트별 예측도 같은 실제 KOSPI 방향으로 검증한다.
        # 단, Bull/Bear처럼 direction 자체가 없는 에이전트(방향 고정 제거 이후)는
        # 방향 적중률 채점 대상에서 제외한다 — 이들은 evidence_verified/evidence_total로
        # 별도 추적된다 (QA 검증팀 결과, _save_record에서 이미 채워짐).
        for a in rec.get("agent_predictions", []):
            direction = a.get("direction")
            if not direction:
                continue
            a["actual_direction"] = actual_direction
            a["correct"] = (direction == actual_direction)
            if actual_change_pct is not None:
                from execution.position import direction_position, strategy_return_pct
                pos = direction_position(direction, rec.get("realized_vol"))
                sr = strategy_return_pct(pos, actual_change_pct)
                a["strategy_return_pct"] = sr["return_pct"] if sr else None
        data[target_date] = rec
        self._write(data)

    def compute_stats(self, include_unverified_evidence: bool = False) -> dict:
        """
        include_unverified_evidence=True면, 근거검증률(evidence_*)만큼은 KOSPI 실제 결과
        검증 여부와 무관하게 모든 기록에서 집계한다. 근거검증은 실행 즉시 확정되는
        값이라 다음날 결과를 기다릴 필요가 없기 때문 (Strategy Manager가 사용).
        """
        all_records = self.all_records()
        records = [r for r in all_records if r["verified"]]
        total = len(records)
        correct = sum(1 for r in records if r["correct"])

        by_conf: dict[str, dict] = {}
        for c in ["낮음", "보통", "높음"]:
            subset = [r for r in records if r["confidence"] == c]
            by_conf[c] = {
                "total": len(subset),
                "correct": sum(1 for r in subset if r["correct"]),
            }

        # (V3.11) 종목 중재자 효과: 조정된 종목의 원래 신호가 맞았을지 vs 보합이 맞았는지
        stock_mod = {"adjusted": 0, "flat_correct": 0, "original_would_be_correct": 0,
                     "watch_total": 0, "watch_correct": 0, "core_total": 0, "core_correct": 0}
        for r in records:
            for s in r.get("stocks", []):
                if s.get("correct") is None:
                    continue
                bucket = "core" if s.get("is_core") else "watch"
                stock_mod[f"{bucket}_total"] += 1
                stock_mod[f"{bucket}_correct"] += int(bool(s["correct"]))
                if s.get("moderated"):
                    stock_mod["adjusted"] += 1
                    stock_mod["flat_correct"] += int(bool(s["correct"]))
                    if s.get("original_direction") and s.get("actual_direction"):
                        stock_mod["original_would_be_correct"] += int(s["original_direction"] == s["actual_direction"])

        by_dq: dict[str, dict] = {}
        for r in records:
            if r.get("debate_quality"):
                b = by_dq.setdefault(r["debate_quality"], {"total": 0, "correct": 0})
                b["total"] += 1
                b["correct"] += int(bool(r.get("correct")))

        by_outlook: dict[str, dict] = {}
        for r in records:
            if not r.get("outlook"):
                continue
            b = by_outlook.setdefault(r["outlook"], {"total": 0, "correct": 0})
            b["total"] += 1
            b["correct"] += int(bool(r.get("correct")))

        by_stock: dict[str, dict] = {}
        for r in records:
            for s in r.get("stocks", []):
                if s.get("correct") is None:
                    continue
                b = by_stock.setdefault(s["name"], {"total": 0, "correct": 0, "accuracy": None, "avg_return_pct": None})
                b["total"] += 1
                if s["correct"]:
                    b["correct"] += 1
                if s.get("actual_return_pct") is not None:
                    returns = b.setdefault("_returns", [])
                    returns.append(float(s["actual_return_pct"]))

        by_indicator: dict[str, dict] = {}
        for r in records:
            if not r.get("actual_direction"):
                continue
            for idx in r.get("us_indices", []):
                b = by_indicator.setdefault(idx["name"], {"total": 0, "match": 0})
                b["total"] += 1
                if idx["direction"] == r["actual_direction"]:
                    b["match"] += 1

        for b in by_stock.values():
            returns = b.pop("_returns", [])
            b["accuracy"] = (b["correct"] / b["total"]) if b["total"] else None
            b["avg_return_pct"] = (sum(returns) / len(returns)) if returns else None

        by_agent: dict[str, dict] = {}
        evidence_source = all_records if include_unverified_evidence else records
        for r in evidence_source:
            for a in r.get("agent_predictions", []):
                name = a.get("agent", "unknown")
                has_direction_score = a.get("correct") is not None and r.get("verified")
                has_evidence_score = a.get("evidence_total") is not None

                if not has_direction_score and not has_evidence_score:
                    continue

                b = by_agent.setdefault(name, {
                    "role": a.get("role", ""),
                    "total": 0, "correct": 0, "accuracy": None,
                    "by_confidence": {},
                    "evidence_total": 0, "evidence_verified": 0, "evidence_verify_rate": None,
                    # (V3.9) 기여도: 트레이더 최종 결론과 비교
                    "agree_with_trader": 0,        # 트레이더와 같은 방향을 낸 횟수
                    "correct_but_ignored": 0,      # 이 에이전트는 맞았는데 트레이더가 반대로 가서 틀림 (가점 근거)
                    "wrong_but_overruled": 0,      # 이 에이전트는 틀렸는데 트레이더가 무시하고 맞힘 (트레이더 가점)
                    "contribution": None,          # correct_but_ignored - wrong_but_overruled (검증 표본 기준)
                })

                if has_direction_score:
                    b["total"] += 1
                    if a["correct"]:
                        b["correct"] += 1
                    trader_dir = r.get("direction")
                    if trader_dir in ("up", "down") and name != "Trader":
                        if a.get("direction") == trader_dir:
                            b["agree_with_trader"] += 1
                        elif a["correct"] and not r.get("correct"):
                            b["correct_but_ignored"] += 1
                        elif (not a["correct"]) and r.get("correct"):
                            b["wrong_but_overruled"] += 1
                    conf = a.get("confidence")
                    if conf is not None:
                        key = str(conf)
                        c = b["by_confidence"].setdefault(key, {"total": 0, "correct": 0})
                        c["total"] += 1
                        if a["correct"]:
                            c["correct"] += 1

                if has_evidence_score:
                    b["evidence_total"] += a.get("evidence_total", 0)
                    b["evidence_verified"] += a.get("evidence_verified", 0)

        for b in by_agent.values():
            b["accuracy"] = (b["correct"] / b["total"]) if b["total"] else None
            if b["total"]:
                b["contribution"] = b["correct_but_ignored"] - b["wrong_but_overruled"]
            for c in b["by_confidence"].values():
                c["accuracy"] = c["correct"] / c["total"] if c["total"] else None
            b["evidence_verify_rate"] = (
                b["evidence_verified"] / b["evidence_total"] if b["evidence_total"] else None
            )

        returns_block = self._returns_block(records)

        return {
            "total": total,
            "correct": correct,
            "accuracy": (correct / total) if total else None,
            "returns": returns_block,
            "by_confidence": by_conf,
            "by_stock": by_stock,
            "by_indicator": by_indicator,
            "by_agent": by_agent,
            "baseline_compare": score_baselines(records),
            "by_debate_quality": by_dq,
            "stock_moderation": stock_mod,
            "calibration": calibration([{"up_probability": r.get("up_probability"),
                                         "actual": r.get("actual_direction")} for r in records]),
            "by_outlook": by_outlook,
        }

    @staticmethod
    def _returns_block(records: list[dict]) -> dict:
        """(V3.14) 수익률 기준 성과: AI 포지션 vs 바이앤홀드 vs 베이스라인 vs 에이전트별. 같은 표본(검증+등락률 있음)."""
        from execution.position import performance, direction_position, strategy_return_pct
        from rules.baselines import baseline_predictions, BASELINES
        rows = [r for r in records if r.get("actual_change_pct") is not None]
        rows.sort(key=lambda r: r.get("actual_session_date") or r.get("target_date", ""))
        if not rows:
            return {"days": 0}
        dates = [r.get("actual_session_date") or r.get("target_date") for r in rows]
        ai = []
        for r in rows:
            v = r.get("strategy_return_pct")
            if v is None:  # 포지션 도입 전 기록: 방향 100% 포지션으로 소급 근사
                sr = strategy_return_pct(direction_position(r.get("direction"), r.get("realized_vol")),
                                         r["actual_change_pct"])
                v = sr["return_pct"] if sr else 0.0
            ai.append(v)
        out = {
            "days": len(rows), "dates": dates, "cost_bps_roundtrip": 10,
            "ai": performance(ai, positions=[r.get("position") if r.get("position") is not None else
                                             direction_position(r.get("direction"), r.get("realized_vol")) for r in rows]),
            "buy_hold": performance([r["actual_change_pct"] for r in rows]),
            "baselines": {}, "by_agent": {},
            "proxy_days": sum(1 for r in rows if r.get("return_proxy", True)),
        }
        for k, label in BASELINES.items():
            rets = []
            for r in rows:
                d = baseline_predictions(r.get("us_indices", [])).get(k)
                if d is None:
                    rets.append(None); continue
                sr = strategy_return_pct(direction_position(d, r.get("realized_vol")), r["actual_change_pct"])
                rets.append(sr["return_pct"])
            vals = [x for x in rets if x is not None]
            if vals:
                out["baselines"][k] = {"label": label, **performance(vals)}
        agents: dict[str, list] = {}
        for r in rows:
            for a in r.get("agent_predictions", []):
                if a.get("strategy_return_pct") is None:
                    continue
                agents.setdefault(a["agent"], []).append(a["strategy_return_pct"])
        for name, rets in agents.items():
            out["by_agent"][name] = performance(rets)
        # 요약: AI가 바이앤홀드·최고 베이스라인보다 나은가
        best_b = max(out["baselines"].items(), key=lambda kv: kv[1].get("total_return_pct", -1e9), default=(None, None))
        out["best_baseline"] = best_b[0]
        out["ai_beats_buy_hold"] = out["ai"]["total_return_pct"] > out["buy_hold"]["total_return_pct"]
        out["ai_beats_best_baseline"] = (best_b[1] is None) or \
            out["ai"]["total_return_pct"] > best_b[1].get("total_return_pct", -1e9)
        return out

    def build_history_context_text(self) -> str:
        """다음 분석 프롬프트에 주입할 과거 실적 요약 텍스트를 만든다."""
        stats = self.compute_stats()
        if not stats["total"]:
            return ""

        pct = round(stats["accuracy"] * 100)
        stock_lines = ", ".join(
            f"{name} {s['correct']}/{s['total']}({round(s['correct']/s['total']*100)}%)"
            for name, s in sorted(stats["by_stock"].items(), key=lambda kv: -kv[1]["total"])[:12]
        )
        indicator_lines = ", ".join(
            f"{name} {s['match']}/{s['total']}({round(s['match']/s['total']*100)}%)"
            for name, s in sorted(stats["by_indicator"].items(), key=lambda kv: -kv[1]["total"])
        )
        agent_lines = []
        for name, s in sorted(stats.get("by_agent", {}).items(), key=lambda kv: -(kv[1]["total"] + kv[1]["evidence_total"])):
            parts = []
            if s["total"]:
                parts.append(f"방향적중 {s['correct']}/{s['total']}({round(s['accuracy']*100)}%)")
            if s["evidence_total"]:
                parts.append(f"근거검증률 {s['evidence_verified']}/{s['evidence_total']}({round(s['evidence_verify_rate']*100)}%)")
            if parts:
                agent_lines.append(f"{name} " + " · ".join(parts))
        agent_lines = ", ".join(agent_lines)

        text = (
            f"\n[참고: 과거 예측 적중 기록 — 검증된 것만 집계]\n"
            f"KOSPI 방향 예측 전체 적중률: {stats['correct']}/{stats['total']} ({pct}%)\n"
        )
        rb = stats.get("returns") or {}
        if rb.get("days"):
            ai, bh = rb["ai"], rb["buy_hold"]
            text += (f"수익률(포지션 기준, 비용 차감) 누적 {ai['total_return_pct']:+.2f}% vs 바이앤홀드 {bh['total_return_pct']:+.2f}%"
                     f" · 샤프 {ai.get('sharpe_annual')} · 최대낙폭 {ai['max_drawdown_pct']:.2f}% · 거래일 {ai['traded_days']}/{ai['days']}\n")
        if stock_lines:
            text += f"종목별 방향성 신호 적중률: {stock_lines}\n"
        if indicator_lines:
            text += f"지표별 선행지표 신뢰도(그날 방향=다음날 실제 KOSPI 방향 일치율): {indicator_lines}\n"
        if agent_lines:
            text += f"에이전트별 예측 적중률: {agent_lines}\n"
        text += (
            "이 기록은 참고용 맥락일 뿐입니다. 반드시 오늘 새로 확인한 데이터를 "
            "우선 판단 근거로 삼되, 지표 간 신호가 엇갈릴 경우 과거 신뢰도가 높았던 "
            "지표에 조금 더 비중을 두세요."
        )
        return text
