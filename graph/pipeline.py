"""
전체 파이프라인 (V3.6 = V3.1 계열 + V3.5 계열 병합본).

AI Multi-Agent Platform V3.5 설계("판단이 필요한 것만 LLM, 계산 가능한 것은 전부 코드")
위에, V3의 "근거 기반 토론 + QA 검증팀"을 얹은 구조다.

  RuleEngine + MarketRegimeDetection (코드)
        |
        v
  MarketAnalyst (LLM)
        |
        v
  HistoricalRetrieval/RAG (코드, TF-IDF)
        |
        v
  DebateLoop (Bull <-> Bear, 근거(evidence) 인용 필수, LLM)
        |
        v
  QA 검증팀 (하위) — 토론 근거가 분석가 리포트/유사사례에 실제로 있는지 대조 (LLM, 웹검색 없음)
        |
        v
  StrategyManager (코드 — Bull/Bear 근거검증률 기반 가중치)
        |
        v
  RiskManager (LLM, 가중치 컨텍스트 주입)
        |
        v
  Verification ① 리스크 단계 — 규칙엔진/과거사례와 상충 검사(코드) → 상충 시에만 LLM 재판단
        |
        v
  Trader (LLM, + 과거 기록/가중치/검증 결과 주입)
        |
        v
  Verification ② 최종 단계 — 규칙 기반 충돌 검사(코드) → 충돌 시에만 QA 상위 검증(LLM)
        |
        v
  MemoryStore.save()  (에이전트별 예측 + 근거검증 수치 + QA 결과 저장)

Portfolio Optimizer/Execution Simulator/Performance Analyzer는 backtest/runner.py에서
사후적으로 결합한다 (실거래를 하지 않는 정보 제공 MVP라 run() 안에 넣지 않음).
"""
from __future__ import annotations
from datetime import date
from dataclasses import asdict

from config import Config
from agents.base import LLMClient
from agents.analysts import run_market_analyst  # noqa: F401 (구버전 호환)
from agents.specialists import run_all_specialists, fetch_recent_news_titles, SPECIALISTS, LAST_NEWS_SOURCE
from agents.risk_manager import run_risk_manager
from agents.verification import run_verification
from agents.trader import run_trader
from agents.qa_verifier import verify_debate_evidence, verify_final_result, summarize_debate_qa
from agents.moderator import run_moderator, build_moderation_text
from agents.stock_moderator import run_stock_moderator
from data.stock_features import build_stock_facts, facts_text
from graph.rule_verification import detect_conflicts
from graph.debate_loop import run_debate_loop
from data.market_data import MarketDataProvider, MarketSnapshot
from data.price_history import resolve_code
from memory.store import MemoryStore, PredictionRecord, StockPrediction, AgentPrediction, next_trading_day
from memory.similarity_search import find_similar_cases, build_similar_cases_context_text
from memory.strategy_manager import compute_weights, build_weight_context_text
from rules.engine import analyze_market
from rules.baselines import baseline_predictions, BASELINES
from data.sessions import kst_now, decide_score_after, next_weekday_after


class TradingAgentsPipeline:
    def __init__(self, llm: LLMClient, memory: MemoryStore,
                 data_provider: MarketDataProvider | None = None,
                 debate_max_rounds: int = Config.DEBATE_MAX_ROUNDS,
                 backtest_mode: bool = False,
                 event_archive_db_path: str = Config.EVENT_ARCHIVE_DB_PATH):
        self.llm = llm
        self.memory = memory
        self.data_provider = data_provider or MarketDataProvider(backtest_mode=backtest_mode)
        self.debate_max_rounds = debate_max_rounds
        self.backtest_mode = backtest_mode
        self.event_archive_db_path = event_archive_db_path
        from memory.lessons import LessonStore
        self.lessons = LessonStore(None if memory.path is None else Config.LESSONS_PATH)

    def run(self, snapshot: MarketSnapshot | None = None) -> dict:
        # 1) 데이터 확보 (주입 안 하면 실시간으로 조회)
        snapshot = snapshot or self.data_provider.fetch_us_snapshot()

        # 1-1) Rule Engine + Market Regime Detection (전부 코드, LLM 미사용)
        market_analysis = analyze_market(snapshot)
        # 1-2) (V3.14) 정량 지표: KOSPI 시계열(as_of 이전만)로 RSI/MACD/변동성/모멘텀/외국인 z-score 계산
        market_analysis.quant = self._quant_indicators(snapshot)
        quant = market_analysis.quant

        # 2) 분석가 4명 (V3.9: 기술 / 거시·수급 / 반도체 / 뉴스·심리) → 코드 다수결 통합 리포트
        news_universe = self._news_universe()
        news_titles = self._news_titles(snapshot, news_universe)
        # 2-0) (V3.24) 근거 자료 목록: 모든 데이터·지표·뉴스에 ID와 출처(데이터원·매체·공식 논문)를 붙인다.
        #      분석가·Bull/Bear·QA 는 이 ID로만 인용하고, 코드가 ID·수치를 대조해 어긋나면 폐기한다.
        from rules.citations import build_registry, public_registry
        source_registry = build_registry(snapshot, quant, news_titles, LAST_NEWS_SOURCE.get("source"))
        spec = run_all_specialists(self.llm, snapshot, market_analysis, news_titles, registry=source_registry)
        analyst_reports = spec["reports"]
        analyst_report = spec["combined"]
        analyst_report["_registry"] = source_registry  # 토론·QA 전용 (기록 전에 뺀다: 뉴스 제목 포함)
        if quant:  # 토론자(Bull/Bear)도 같은 지표를 보고 인용하게 한다
            from rules.quant import quant_text
            analyst_report["quant_context"] = quant_text(quant)

        # 2-1) 유사 변동성 사례 검색 (RAG, 토큰 비용 없음 — TF-IDF 로컬 검색)
        query_text = analyst_report.get("summary", "") + " " + \
            " ".join(analyst_report.get("notable_points", []))
        similar_cases = find_similar_cases(
            query_text, db_path=self.event_archive_db_path,
            as_of=snapshot.as_of if self.backtest_mode else None,
        )
        similar_cases_context = build_similar_cases_context_text(similar_cases)

        # 3) 토론 루프 (Bull vs Bear — 방향 고정 없이 근거 인용 필수)
        transcript = run_debate_loop(self.llm, analyst_report, max_rounds=self.debate_max_rounds)

        # 3-1) QA 검증팀 (하위) — 토론 근거가 실제 소스 자료에 있는지 대조.
        # 웹 검색 없이 "이미 가진 자료와 일치하는지"만 보므로 비용이 저렴하다.
        debate_qa = verify_debate_evidence(self.llm, transcript, analyst_report, similar_cases_context,
                                           quant=quant)
        analyst_report.pop("_registry", None)

        # 3-2) 중재자 (V3.8): 토론을 정리한다 — 확인된 근거만 남기고 쟁점/합의/소음을 분리.
        # 미해결이면 트레이더에 "확률 50 근처" 권고(중립 신호). 방향은 정하지 않는다.
        from memory.lessons import situation_tags, build_lessons_text
        today_tags = situation_tags(asdict(market_analysis), quant, None,
                                    [{"name": q.name, "direction": q.direction} for q in snapshot.us_indices])
        lessons = self.lessons.select_relevant(today_tags, n=5,
                                               as_of=self._as_of_date(snapshot) if self.backtest_mode else None)
        lessons_text = build_lessons_text(lessons)
        moderation = run_moderator(self.llm, transcript, debate_qa, extra_context=lessons_text)
        moderation_text = build_moderation_text(moderation)

        # 3-3) Strategy Manager: Bull/Bear 근거검증률 기반 가중치 (코드로 산출)
        strategy_weights = compute_weights(self.memory)
        weight_context = build_weight_context_text(strategy_weights)

        # 4) 리스크 매니저 (+ 중재자 정리 + Strategy Manager 가중치 컨텍스트)
        risk_assessment = run_risk_manager(self.llm, transcript, weight_context=weight_context,
                                           moderation_text=moderation_text)

        # 4-1) 검증 ① 리스크 단계: 규칙엔진/과거사례와 상충할 때만 LLM 재판단
        verification = run_verification(self.llm, risk_assessment, market_analysis, similar_cases)

        # 5) 과거 기록 컨텍스트 (self-learning 피드백 루프)
        history_context = self.memory.build_history_context_text()
        history_context += similar_cases_context
        history_context += weight_context
        history_context += lessons_text

        # 6) 트레이더 (최종 결정)
        # backtest 모드에서는 웹 검색을 끈다 — 과거 날짜를 검색해도 "오늘" 결과가 나와
        # look-ahead(미래 데이터 유출)가 되기 때문에, 주어진 스냅샷 데이터만으로 판단하게 한다.
        from rules.engine import build_context_text
        # (V3.11) 오늘 추첨된 관심 종목의 사실(가격 흐름·뉴스)을 트레이더에 넘기고, 목록을 강제한다
        watch = [u for u in news_universe if not u.get("core")]
        stock_facts = build_stock_facts(news_universe, self._as_of_date(snapshot), news_titles) \
            if not self._is_demo() else [{"code": u["code"], "name": u["name"], "core": u.get("core", False),
                                          "rank": u.get("rank"), "momentum": None, "news_count": 0, "news_sample": []}
                                         for u in news_universe]
        sf_text = facts_text(stock_facts)
        from data.etf_forecast import collect as collect_etf,validate as validate_etf
        import json
        etf_now=kst_now();etf_target=str(self._decide_target(snapshot)[2])
        etf_evidence=collect_etf(etf_now,etf_target) if not self._is_demo() and not self.backtest_mode else {'status':'held','sources':[]}
        source_registry.extend(etf_evidence['sources'])
        etf_context='\n[ETF 운용 전용 자료]\n'+json.dumps(etf_evidence,ensure_ascii=False)
        result = run_trader(self.llm, analyst_report, transcript, risk_assessment,
                             history_context, verification=verification,
                             use_web_search=not self.backtest_mode,
                             rule_context=build_context_text(market_analysis),
                             moderation_text=moderation_text, stock_facts_text=sf_text+etf_context,
                             watch_names=[u["name"] for u in watch] or None)

        # 6-0) 종목 중재자 (V3.11): 근거 없는 종목 신호를 보합으로 되돌린다 (AI 1회)
        if watch:
            outlook = result.get("kospi_prediction", {}).get("outlook_label") or result.get("kospi_prediction", {}).get("direction", "-")
            result = run_stock_moderator(self.llm, result, sf_text, str(outlook))

        # 6-0-0) (V3.16) 확률 보정: 과거 검증 기록에서 "AI 70% → 실제 몇 %"를 재서 되먹인다 (코드)
        kp = result.get("kospi_prediction") or {}
        kp["up_probability_raw"] = kp.get("up_probability")
        calibration_note = ""
        if Config.CALIBRATE_PROBABILITY and kp.get("up_probability") is not None:
            from rules.calibrate import fit_calibration, apply_calibration
            from rules.outlook import derive
            model = fit_calibration(self.memory.all_records(),
                                    as_of=str(self._as_of_date(snapshot)) if self.backtest_mode else None)
            cal = apply_calibration(kp["up_probability"], model)
            calibration_note = cal["note"]
            if cal["applied"]:
                kp["up_probability"] = cal["p"]
                kp.update(derive(cal["p"]))  # 방향·전망·신뢰도를 보정 확률로 다시 도출
                kp["calibration_applied"] = True
        kp["calibration_note"] = calibration_note

        # 6-0-1) (V3.14) 확률 → 포지션 (변동성 타게팅 + 하프 켈리, 코드). 수익률 채점의 기준.
        from execution.position import position_from_probability
        kp["position_info"] = position_from_probability(kp.get("up_probability"),
                                                        (quant or {}).get("realized_vol_20"))
        kp["position"] = kp["position_info"]["position"]

        # 6-1) 검증 ② 최종 단계: Rule 먼저(무료), 충돌이 감지될 때만 LLM 상위 QA 호출.
        # 검증 ①이 리스크 수준을 조정했다면 그 최종값을 기준으로 충돌을 본다.
        effective_risk = dict(risk_assessment)
        if verification.get("final_risk_level"):
            effective_risk["risk_level"] = verification["final_risk_level"]
        rule_check = detect_conflicts(effective_risk, result, debate_qa,
                                      expected_watch=len(watch) if watch else Config.WATCH_STOCK_COUNT)
        if rule_check["conflict"]:
            final_qa = verify_final_result(self.llm, result, transcript, effective_risk,
                                           source_registry=source_registry, debate_qa=debate_qa)
            final_qa["rule_conflict_reasons"] = rule_check["reasons"]
            final_qa["llm_verification_triggered"] = True
        else:
            final_qa = {"consistent": True, "issues": [], "llm_verification_triggered": False}

        result['etf_execution']=validate_etf(result.get('etf_execution'),etf_evidence,etf_now,etf_target)
        if final_qa.get('consistent') is not True:
            result['etf_execution'].update(status='held',selected_code=None,weight=0,note='최종 QA 미승인 · ETF 신규 매매 보류')

        # 7) 기록 저장 (백테스트 중에는 실거래 기록과 섞이지 않도록 호출부에서 별도 처리)
        if not self.backtest_mode:
            self._save_record(snapshot, result, analyst_report, transcript, risk_assessment,
                               debate_qa, final_qa, moderation, analyst_reports, quant=quant)

        return {
            "market_analysis": asdict(market_analysis),
            "quant": quant,
            "kospi_history": getattr(self, "_last_hist", None),  # (V3.18) 차트용 최근 80거래일 종가
            "lessons_used": lessons,
            "situation_tags": today_tags,
            "snapshot": {
                "as_of": snapshot.as_of,
                "us_indices": [{"name": q.name, "change_pct": q.change_pct, "direction": q.direction,
                                "value": q.value, "rsi14": q.rsi14} for q in snapshot.us_indices],
                "macro": [{"name": q.name, "change_pct": q.change_pct, "direction": q.direction, "value": q.value}
                          for q in (snapshot.macro or [])],
                "kr_flow": snapshot.kr_flow,
                "data_notes": list(snapshot.data_notes or []),
            },
            "analyst_report": analyst_report,
            "analyst_reports": analyst_reports,
            "news_titles": news_titles,
            "news_source": LAST_NEWS_SOURCE.get("source"),
            "source_registry": public_registry(source_registry),  # 뉴스 제목 제외, ID·출처만
            "news_universe": [{"code": u["code"], "name": u["name"], "core": u.get("core", False),
                               "rank": u.get("rank")} for u in news_universe],
            "stock_facts": stock_facts,
            "debate_transcript": transcript,
            "debate_qa": debate_qa,
            "moderation": moderation,
            "strategy_weights": strategy_weights,
            "risk_assessment": risk_assessment,
            "verification": verification,
            "similar_cases": [c.__dict__ for c in similar_cases],
            "final_result": result,
            "final_qa": final_qa,
            "target_date": str(self._decide_target(snapshot)[2]),
            "baselines": {
                k: {"label": BASELINES[k], "direction": v}
                for k, v in baseline_predictions(
                    [{"name": q.name, "direction": q.direction} for q in snapshot.us_indices]).items()
            },
        }

    @staticmethod
    def _as_of_date(snapshot: MarketSnapshot) -> date:
        try:
            return date.fromisoformat(str(snapshot.as_of)[:10])
        except (TypeError, ValueError):
            return date.today()

    def _situation_tags_for_record(self, snapshot, quant, pred) -> list[str]:
        from memory.lessons import situation_tags
        from rules.engine import analyze_market
        ma = analyze_market(snapshot)
        return situation_tags(asdict(ma), quant, pred,
                              [{"name": q.name, "direction": q.direction} for q in snapshot.us_indices])

    @staticmethod
    def _digest(snapshot, result, analyst_reports, transcript, risk_assessment, debate_qa, moderation, quant) -> dict:
        """(V3.16) 복기용 요약 — 그날 판단의 전 과정을 3천 자 안팎으로 압축해 기록에 남긴다."""
        from rules.engine import analyze_market, build_context_text
        from rules.quant import quant_text
        ma = analyze_market(snapshot)
        rejected = []
        for r in debate_qa or []:
            rejected += list(r.get("unverified") or [])
        kp = result.get("kospi_prediction") or {}
        return {
            "rule_summary": build_context_text(ma)[:600],
            "quant_text": quant_text(quant)[:700] if quant else "",
            "analysts": {n: {"direction": a.get("direction"), "confidence": a.get("confidence"),
                             "summary": str(a.get("summary", ""))[:200]}
                         for n, a in (analyst_reports or {}).items()},
            "debate": [{"stance": t.get("stance"), "round": t.get("round"),
                        "argument": str(t.get("argument", ""))[:220], "evidence": (t.get("evidence") or [])[:3]}
                       for t in (transcript or [])][:6],
            "qa_rejected": rejected[:6],
            "moderation": str((moderation or {}).get("summary", ""))[:300],
            "risk": f"{(risk_assessment or {}).get('risk_level')} / {str((risk_assessment or {}).get('summary', ''))[:250]}",
            "prediction": {"reasoning": [str(x)[:160] for x in (kp.get("reasoning") or [])][:4]},
        }

    def _quant_indicators(self, snapshot: MarketSnapshot) -> dict | None:
        """(V3.14) 데이터 제공자에게 KOSPI 시계열을 받아 지표를 계산한다. 실패하면 None(파이프라인은 계속)."""
        if self._is_demo() and self.data_provider.__class__.__name__ == "_DemoDataProvider":
            return None
        fetch = getattr(self.data_provider, "fetch_kospi_history", None)
        if fetch is None:
            return None
        try:
            hist = fetch(self._as_of_date(snapshot))
            self._last_hist = {"dates": hist["dates"][-80:], "closes": [round(c, 2) for c in hist["closes"][-80:]]}
        except Exception as e:  # noqa: BLE001
            print(f"[정량 지표 생략] {e}")
            self._last_hist = None
            return None
        try:
            from rules.quant import compute_indicators
            flows = self.memory.foreign_flow_history()
            today_flow = (snapshot.kr_flow or {}).get("foreign_net_krw_bn")
            if today_flow is not None:
                flows = flows + [float(today_flow)]
            ind = compute_indicators(hist["closes"], hist.get("highs"), hist.get("lows"), flow_history=flows)
            if ind is not None and hist.get("dates"):
                ind["history_last_date"] = str(hist["dates"][-1])[:10]  # (V3.25) 지표가 실제로 반영한 마지막 거래일
            return ind
        except Exception as e:  # noqa: BLE001
            print(f"[정량 지표 계산 실패] {e}")
            return None

    def _is_demo(self) -> bool:
        """데모 실행이거나 TA_NO_NEWS=1(테스트)이면 네트워크 뉴스 수집을 건너뛴다."""
        import os
        return self.data_provider.__class__.__name__ == "_DemoDataProvider" or os.environ.get("TA_NO_NEWS") == "1"

    def _news_universe(self) -> list[dict]:
        """(V3.10) 핵심 2 + KOSPI 시총 상위에서 고른 추가 종목. 데모/테스트에선 핵심 2만."""
        from data.universe import CORE_CODES, select_news_universe, select_watch_universe
        core = [{"code": c, "name": n, "core": True} for c, n in CORE_CODES.items()]
        if self._is_demo():
            return core + [{"code": "005380", "name": "현대차", "core": False, "rank": 5},
                           {"code": "000270", "name": "기아", "core": False, "rank": 8}]
        try:
            watch = select_watch_universe(seed_date=kst_now().date() if not self.backtest_mode else None)
            return select_news_universe(watch=watch)
        except Exception:  # noqa: BLE001
            return core

    def _news_titles(self, snapshot: MarketSnapshot, universe: list[dict]) -> list[str]:
        """뉴스·심리 분석가용 최근 뉴스 제목. 백테스트에선 as_of 이전 것만(look-ahead 방지)."""
        try:
            as_of = date.fromisoformat(str(snapshot.as_of)[:10])
        except (TypeError, ValueError):
            as_of = date.today()
        if self._is_demo():
            return []
        return fetch_recent_news_titles(as_of, universe=universe)

    @staticmethod
    def _decide_target(snapshot: MarketSnapshot):
        """(V3.6.1) 대상일: 장 시작 전 실행이면 오늘, 이후면 다음 거래일.
        휴장일이면 검증 때 "score_after 이후 실제 첫 거래일"로 자동 보정된다."""
        now = kst_now()
        us_dates = []
        for q in snapshot.us_indices:
            try:
                us_dates.append(date.fromisoformat(str(q.as_of)[:10]))
            except (TypeError, ValueError):
                pass
        score_after = decide_score_after(now, max(us_dates) if us_dates else None)
        return now, score_after, next_weekday_after(score_after)

    def _save_record(self, snapshot: MarketSnapshot, result: dict, analyst_report: dict | None = None,
                      transcript: list[dict] | None = None, risk_assessment: dict | None = None,
                      debate_qa: list[dict] | None = None, final_qa: dict | None = None,
                      moderation: dict | None = None, analyst_reports: dict | None = None,
                      quant: dict | None = None):
        pred = result.get("kospi_prediction", {})
        if not pred.get("direction"):
            return

        now, score_after, target = self._decide_target(snapshot)
        today = now.date()

        stocks = []
        for s in (result.get("core_stocks", []) + result.get("watch_stocks", [])):
            name = str(s.get("name", "")).strip()
            ticker = None
            try:
                ticker = resolve_code(name)
            except Exception:
                # LLM이 새로운/잘못된 종목명을 반환해도(또는 종목 목록 조회가 실패해도)
                # 예측 전체를 잃지 않는다. 검증 단계에서 unresolved로 남는다.
                pass

            stocks.append(asdict(StockPrediction(
                name=name,
                ticker=ticker,
                predicted_date=str(target),
                sector=s.get("sector", ""),
                related_indicator=s.get("related_us_indicator", ""),
                sensitivity=s.get("sensitivity", "mid"),
                direction=s.get("directional_signal"),
                is_core=name in ("삼성전자", "SK하이닉스"),
                moderated=bool(s.get("moderated")),
                original_direction=s.get("original_signal"),
            )))

        # 최종 Trader 외에도 각 에이전트의 독립적인 판단 품질을 보존한다.
        # Bull/Bear는 고정 방향을 주장하지 않으므로 방향 적중률이 아니라
        # QA 검증팀이 확인한 "근거 검증률"로 품질을 추적한다.
        analyst = analyst_report or {}
        risk = risk_assessment or {}
        qa_summary = summarize_debate_qa(debate_qa or [])

        def _evidence_fields(stance: str):
            s = qa_summary.get(stance, {})
            total = s.get("total", 0)
            return {"evidence_total": total, "evidence_verified": s.get("verified", 0)} if total else \
                   {"evidence_total": None, "evidence_verified": None}

        agent_predictions = [
            asdict(AgentPrediction(
                agent="MarketAnalyst", role="market_analysis",
                direction=analyst.get("direction"),
                confidence=analyst.get("confidence"),
                predicted_date=str(target),
            )),
        ] + [
            asdict(AgentPrediction(
                agent=name, role="specialist_analysis",
                direction=(analyst_reports or {}).get(name, {}).get("direction"),
                confidence=(analyst_reports or {}).get(name, {}).get("confidence"),
                predicted_date=str(target),
            )) for name, _ in SPECIALISTS
        ] + [
            asdict(AgentPrediction(
                agent="Bull", role="debate_bull",
                predicted_date=str(target),
                **_evidence_fields("bull"),
            )),
            asdict(AgentPrediction(
                agent="Bear", role="debate_bear",
                predicted_date=str(target),
                **_evidence_fields("bear"),
            )),
            asdict(AgentPrediction(
                agent="RiskManager", role="risk_assessment",
                direction=risk.get("leaning") or risk.get("direction"),
                confidence=risk.get("recommended_confidence"),
                predicted_date=str(target),
            )),
            asdict(AgentPrediction(
                agent="Trader", role="final_decision",
                direction=pred.get("direction"),
                confidence=pred.get("confidence", "보통"),
                predicted_date=str(target),
            )),
        ]

        record = PredictionRecord(
            target_date=str(target),
            run_date=str(today),
            direction=pred["direction"],
            confidence=pred.get("confidence", "보통"),
            us_indices=[{"name": q.name, "direction": q.direction} for q in snapshot.us_indices],
            stocks=stocks,
            agent_predictions=agent_predictions,
            qa_final_consistent=(final_qa or {}).get("consistent"),
            qa_final_issues=(final_qa or {}).get("issues", []) + (final_qa or {}).get("rule_conflict_reasons", []),
            score_after_date=str(score_after),
            run_at=now.isoformat(timespec="minutes"),
            up_probability=pred.get("up_probability"),
            outlook=pred.get("outlook"),
            debate_quality=(moderation or {}).get("debate_quality"),
            position=pred.get("position"),
            position_info=pred.get("position_info"),
            up_probability_raw=pred.get("up_probability_raw"),
            calibration_note=pred.get("calibration_note"),
            situation_tags=self._situation_tags_for_record(snapshot, quant, pred),
            outcome_digest=self._digest(snapshot, result, analyst_reports, transcript, risk_assessment,
                                        debate_qa, moderation, quant),
            realized_vol=((quant or {}).get("realized_vol_20") if quant else None),
            foreign_net_krw_bn=(snapshot.kr_flow or {}).get("foreign_net_krw_bn"),
        )
        self.memory.save(record)

    def verify_pending(self):
        """지난 예측들을 실제 결과와 비교해서 검증한다 (KOSPI + 종목별).

        (V3.6) 채점 기준일 = 분석 실행일(run_date) 이후 실제로 처음 열린 거래일.
        대상일이 공휴일이면 자동으로 다음 거래일로 넘어가고, 아직 장이 안 열렸으면 다음에 다시 시도한다.
        """
        now = kst_now()
        today = now.date()
        market_closed = now.hour > 15 or (now.hour == 15 and now.minute >= 40)
        for rec in self.memory.pending_records(today, include_today=market_closed):
            # 채점 기준일: V3.6.1 기록은 score_after_date, 이전 기록은 run_date(당시 규칙: 다음 거래일)
            run_date = date.fromisoformat(rec.get("score_after_date") or rec.get("run_date") or rec["target_date"])
            try:
                k = self.data_provider.fetch_kospi_session_after(run_date)
            except Exception as e:
                print(f"[검증 보류] {rec['target_date']}: {e}")
                continue
            actual = k["actual_direction"]
            session = date.fromisoformat(k["actual_date"])
            if session >= today and not market_closed:
                print(f"[채점 대기] {k['actual_date']} 장 마감 전입니다 (15:40 이후 다시 실행).")
                continue
            if k["actual_date"] != rec["target_date"]:
                print(f"[안내] {rec['target_date']}은(는) 휴장일이라 {k['actual_date']} 거래일로 채점합니다.")

            stock_actuals = {}
            for stock in rec.get("stocks", []):
                name = stock.get("name", "")
                try:
                    stock_actuals[name] = self.data_provider.fetch_stock_actual(name, session, after_date=run_date)
                except Exception as e:
                    print(f"[종목 검증 실패] {name} ({stock.get('ticker') or 'ticker 없음'}): {e}")

            etf = {}
            fetch_etf = getattr(self.data_provider, "fetch_etf_returns", None)
            if fetch_etf is not None and rec.get("position"):
                try:
                    etf = fetch_etf(session) or {}
                except Exception as e:  # noqa: BLE001
                    print(f"[ETF 수익률 조회 실패 → 지수로 근사] {e}")
            self.memory.mark_verified(rec["target_date"], actual, stock_actuals=stock_actuals,
                                      actual_session_date=k["actual_date"],
                                      actual_change_pct=k.get("change_pct"), etf_returns=etf)
            self._postmortem(rec["target_date"])
            self._paper_settle(rec, session)
        self._paper_catch_up()

    def _paper_catch_up(self) -> None:
        """(V3.22) ETF 시세가 늦게 들어와 체결 못 한 날을 다음 채점 때 다시 시도."""
        from datetime import date as _date
        for r in sorted(self.memory.all_records(), key=lambda r: r.get("target_date", ""))[-10:]:
            if r.get("verified") and r.get("position") is not None and r.get("actual_session_date"):
                try:
                    self._paper_settle(r, _date.fromisoformat(r["actual_session_date"][:10]))
                except ValueError:
                    continue

    def _paper_settle(self, rec: dict, session) -> None:
        """(V3.21) 가상 계좌: 그날 ETF 시가에 포지션대로 리밸런싱, 종가 평가. 실기록에서만(백테스트 제외)."""
        from pathlib import Path
        # The new book executes at observed prices. Never backfill the legacy
        # opening-price simulation while that book exists, even if it is paused.
        if Path(Config.PAPER_PORTFOLIO_PATH).exists():
            return
        if self.memory.path is None or rec.get("position") is None:
            return
        fetch = getattr(self.data_provider, "fetch_etf_ohlc", None)
        if fetch is None:
            return
        try:
            from execution.paper import PaperAccount
            acct = PaperAccount(Config.PAPER_ACCOUNT_PATH, Config.PAPER_INITIAL_CASH)
            if acct.already_settled(str(session)):
                return
            etf = fetch(session)
            run_at = str(rec.get("run_at") or "")
            early = len(run_at) >= 16 and run_at[:10] < str(session) or (run_at[:10] == str(session) and run_at[11:16] <= "08:40")
            day = acct.settle_day(str(session), rec.get("position") or 0.0, etf,
                                  note=f"예측 {rec.get('direction')} p={rec.get('up_probability')}",
                                  entry="prev_close" if early else "open")
            if day:
                print(f"[가상 계좌] {session} 평가금액 {day['equity']:,.0f}원 ({day['day_return_pct']:+.2f}%), 누적 {day['total_return_pct']:+.2f}%")
        except Exception as e:  # noqa: BLE001
            print(f"[가상 계좌 생략] {e}")

    def review_now(self, on: str | None = None) -> dict | None:
        """(V3.22) 관찰 복기: 날짜 on(기본 오늘, KST)의 실제 KOSPI 움직임과 '그날 참고할 수 있었던 예측'을 대조해 교훈을 남긴다.
        대상 예측 고르기: ① 대상일이 on 인 기록(정식 채점 전 미리보기) → ② on 에 실행된 기록(개장 뒤 실행이라 대상일이 다음 날)
        → ③ 저장된 마지막 분석. 정식 채점(적중률·수익률)에는 들어가지 않고 lessons.json 에 '관찰'로 저장된다."""
        from memory.outcome_store import load_last_outcome
        from agents.reviewer import run_postmortem
        from datetime import date as _date
        now = kst_now()
        if on == "yesterday":
            from datetime import timedelta as _td
            day = now.date() - _td(days=1)
            while day.weekday() >= 5:
                day -= _td(days=1)
        else:
            day = _date.fromisoformat(on) if on else now.date()
        existing = next((r for r in self.lessons.all() if r.get('target_date') == str(day)), None)
        verified = self.memory.get(str(day))
        # The close-time verification runs before this optional observation.
        # Recover a formal review from its attached record if an older run
        # replaced the lesson with an observational one; never spend another
        # model call or downgrade the formal review.
        if verified and verified.get('verified') and verified.get('postmortem'):
            if existing is None or existing.get('observational'):
                self.lessons.add({
                    'target_date': str(day), 'direction': verified.get('direction'),
                    'actual_direction': verified.get('actual_direction'),
                    'correct': verified.get('correct'),
                    'strategy_return_pct': verified.get('strategy_return_pct'),
                    'tags': verified.get('situation_tags') or [],
                    **verified['postmortem'],
                })
                print(f'[정식 복기 복구] {day}: 검증된 예측 기록에서 교훈을 복원했습니다.')
            else:
                print(f'[관찰 복기 생략] {day}: 정식 복기가 이미 있습니다.')
            return None
        if existing:
            print(f'[관찰 복기 생략] {day}: 복기가 이미 있습니다.')
            return None
        recs = self.memory.all_records() + self.memory.superseded_records()
        pick = next((r for r in recs if r.get("target_date") == str(day) and not r.get("verified")), None) \
            or next((r for r in sorted(recs, key=lambda r: r.get("run_at") or r.get("run_date") or "", reverse=True)
                     if r.get("run_date") == str(day) and r.get("target_date") != str(day)), None)
        digest = (pick or {}).get("outcome_digest")
        if pick is None or not digest:
            o = (load_last_outcome(Config.LAST_OUTCOME_PATH) or {}).get("outcome") or {}
            if pick is None:
                kp0 = (o.get("final_result") or {}).get("kospi_prediction") or {}
                if not kp0.get("direction"):
                    print("[관찰 복기] 대조할 예측이 없습니다.")
                    return None
                pick = {"target_date": o.get("target_date"), "direction": kp0.get("direction"),
                        "up_probability": kp0.get("up_probability"), "outlook": kp0.get("outlook"), "position": kp0.get("position")}
            if not digest:
                digest = self._digest_from_outcome(o) if o else {}
        try:
            h = self.data_provider.fetch_kospi_history(day, sessions=3)
        except Exception as e:  # noqa: BLE001
            print(f"[관찰 복기] KOSPI 시세 조회 실패: {e}")
            return None
        if len(h["closes"]) < 2 or h["dates"][-1] != str(day):
            print(f"[관찰 복기] {day} KOSPI 시세가 아직 없습니다.")
            return None
        chg = round((h["closes"][-1] / h["closes"][-2] - 1) * 100, 2)
        actual_dir = "up" if chg >= 0 else "down"
        intraday = day == now.date() and not (now.hour > 15 or (now.hour == 15 and now.minute >= 40))
        rec = {"target_date": str(day), "direction": pick.get("direction"), "up_probability": pick.get("up_probability"),
               "outlook": pick.get("outlook"), "position": pick.get("position"), "actual_direction": actual_dir,
               "actual_change_pct": chg, "strategy_return_pct": None, "correct": pick.get("direction") == actual_dir,
               "actual_session_date": str(day) + (" (장중)" if intraday else "")}
        pm = run_postmortem(self.llm, rec, digest)
        lesson = {"target_date": str(day), "direction": rec["direction"], "actual_direction": actual_dir,
                  "correct": rec["correct"], "strategy_return_pct": None,
                  "tags": list(pick.get("situation_tags") or []) + ["관찰"], "observational": True,
                  "reviewed_record": pick.get("target_date"),
                  "note": f"{day} KOSPI {chg:+.2f}% ({'장중' if intraday else '종가'}) vs 예측({pick.get('target_date')} 대상) — 관찰 복기, 정식 채점 아님",
                  **pm}
        self.lessons.add(lesson)
        print(f"[관찰 복기] {day}: 예측 {rec['direction']} vs 실제 {chg:+.2f}% → {pm.get('lesson')}")
        return lesson

    @staticmethod
    def _digest_from_outcome(o: dict) -> dict:
        """저장된 outcome(last_outcome.json)에서 복기용 요약을 만든다 (기록의 outcome_digest 와 같은 형식)."""
        from rules.quant import quant_text
        ma = o.get("market_analysis") or {}
        rejected = []
        for r in o.get("debate_qa") or []:
            rejected += list(r.get("unverified") or [])
        kp = (o.get("final_result") or {}).get("kospi_prediction") or {}
        return {
            "rule_summary": f"추세 {ma.get('regime_trend')} / 변동성 {ma.get('regime_volatility')} / 리스크 힌트 {ma.get('risk_level_hint')} / "
                            f"SOX 힌트 {ma.get('direction_hint')} / {ma.get('macro_summary', '')}"[:600],
            "quant_text": quant_text(o.get("quant") or {})[:700] if o.get("quant") else "",
            "analysts": {n: {"direction": a.get("direction"), "confidence": a.get("confidence"),
                             "summary": str(a.get("summary", ""))[:200]} for n, a in (o.get("analyst_reports") or {}).items()},
            "debate": [{"stance": t.get("stance"), "round": t.get("round"), "argument": str(t.get("argument", ""))[:220],
                        "evidence": (t.get("evidence") or [])[:3]} for t in (o.get("debate_transcript") or [])][:6],
            "qa_rejected": rejected[:6],
            "moderation": str((o.get("moderation") or {}).get("summary", ""))[:300],
            "risk": f"{(o.get('risk_assessment') or {}).get('risk_level')} / {str((o.get('risk_assessment') or {}).get('summary', ''))[:250]}",
            "prediction": {"reasoning": [str(x)[:160] for x in (kp.get("reasoning") or [])][:4]},
        }

    def _postmortem(self, target_date: str) -> None:
        """(V3.16) 채점 직후 복기: 빗나감/손실/크게 맞음이면 AI 1회 호출해 교훈을 lessons.json 에 남긴다."""
        if not Config.POSTMORTEM:
            return
        from agents.reviewer import run_postmortem, should_review
        rec = next((r for r in self.memory.all_records() if r.get("target_date") == target_date), None)
        prior = next((r for r in self.lessons.all() if r.get('target_date') == target_date), None)
        if not rec or not rec.get("outcome_digest") or not should_review(rec) or (prior and not prior.get('observational')):
            return
        try:
            pm = run_postmortem(self.llm, rec, rec["outcome_digest"])
        except Exception as e:  # noqa: BLE001
            print(f"[복기 생략] {target_date}: {e}")
            return
        lesson = {
            "target_date": target_date, "direction": rec.get("direction"),
            "actual_direction": rec.get("actual_direction"), "correct": rec.get("correct"),
            "strategy_return_pct": rec.get("strategy_return_pct"), "tags": rec.get("situation_tags") or [],
            **pm,
        }
        self.lessons.add(lesson)
        self.memory.attach_postmortem(target_date, pm)
        print(f"[복기] {target_date}: {pm.get('lesson')}")
