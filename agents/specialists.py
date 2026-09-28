"""
분석가 세분화 (V3.9). V3.5 설계의 "Fundamental / Technical / Sentiment / Macro" 분석가를
KOSPI 맥락에 맞게 4명으로 나눈다. 핵심은 **서로 다른 데이터**를 보게 하는 것이다 —
같은 데이터를 보는 관점 에이전트를 늘리는 것과 달리, 각자의 적중률을 따로 채점하면
"어떤 정보가 실제로 맞히는가"를 알 수 있다.

  TechnicalAnalyst      : 미국 지수 등락 + RSI/MACD/볼린저 (기술적)
  MacroFlowAnalyst      : 환율·금리·유가·달러·외국인 수급 (외국인 자금 환경)
  SemiconductorAnalyst  : SOX + 반도체 관련 사실 (KOSPI 핵심 선행지표 전담)
  NewsSentimentAnalyst  : 삼성전자·SK하이닉스 최근 뉴스 제목 (심리) — 뉴스가 없으면 LLM 호출 생략

각 분석가는 {"direction","confidence","summary","notable_points"}를 내고, 코드가 다수결로
합친 통합 리포트(MarketAnalyst)를 만들어 토론에 넘긴다. 개별 판단은 기록에 남아 기여도 채점에 쓰인다.
"""
from __future__ import annotations
from datetime import date, timedelta
from typing import Optional

from agents.base import LLMClient
from data.market_data import MarketSnapshot
from rules.engine import MarketAnalysis, build_context_text

_SCHEMA = '반드시 JSON만 출력하세요: {"direction": "up 또는 down", "confidence": "낮음/보통/높음", "summary": "...", "notable_points": ["...", "..."]}'

TECHNICAL_PROMPT = f"""당신은 기술적 분석가(Technical Analyst) 에이전트입니다.
미국 주요 지수의 등락률과 이미 코드로 계산된 RSI14/MACD 히스토그램/볼린저 %B만 근거로,
다음 거래일 KOSPI 방향에 대한 기술적 관점의 판단을 내리세요. 환율·뉴스 등 다른 정보는 다루지 마세요.
과매수/과매도, 모멘텀, 추세 관점에서 사실과 해석만 간결하게. {_SCHEMA}"""

MACRO_PROMPT = f"""당신은 거시·수급 분석가(Macro & Flow Analyst) 에이전트입니다.
원/달러 환율, 미국 10년 금리, WTI 유가, 달러인덱스, 외국인 순매수만 근거로
"외국인 자금이 KOSPI에 들어올 환경인가"를 판단하세요 (원화 약세·강달러·금리 급등 = 부담).
미국 주가지수 자체나 반도체 업황은 다루지 마세요. {_SCHEMA}"""

SEMI_PROMPT = f"""당신은 반도체 분석가(Semiconductor Analyst) 에이전트입니다.
KOSPI는 삼성전자·SK하이닉스 비중이 커서 필라델피아 반도체 지수(SOX)와 방향 일치율이 가장 높습니다.
SOX의 등락·기술적 지표와 반도체 관련 사실만 근거로 판단하세요. 다른 지수·거시 지표는 다루지 마세요.
SOX가 ±1.5% 이상이면 그 방향에 무게를, ±0.5% 이내면 confidence를 낮게. {_SCHEMA}"""

NEWS_PROMPT = f"""당신은 뉴스·심리 분석가(News Sentiment Analyst) 에이전트입니다.
주어진 최근 뉴스 제목만 근거로 국내 증시(KOSPI) 시장 심리를 판단하세요. 제목은 [날짜][종목명] 형식으로
핵심 종목(삼성전자·SK하이닉스)과 시가총액 상위 종목들의 것이 섞여 있습니다. 핵심 종목과 시장 전체에
영향이 큰 뉴스(실적, 수급, 정책, 대형 계약, 사고)에 무게를 두고, 개별 소형 이슈는 낮게 보세요.
제목에 없는 사실을 추측하지 말고, 긍정/부정 제목의 비율과 무게를 근거로 하세요.
뉴스가 중립적이거나 엇갈리면 confidence를 낮게. notable_points에는 종목명을 함께 적으세요. {_SCHEMA}"""


def _idx_lines(quotes, names: Optional[set] = None) -> str:
    out = []
    for q in quotes:
        if names and q.name not in names:
            continue
        line = f"- {q.name}: {q.change_pct:+.2f}%"
        if q.value is not None:
            line += f", 현재 {q.value}"
        if q.rsi14 is not None:
            line += f", RSI14={q.rsi14}"
        if q.macd_hist is not None:
            line += f", MACD_hist={q.macd_hist}"
        if q.bb_percent_b is not None:
            line += f", BB%B={q.bb_percent_b}"
        out.append(line)
    return "\n".join(out) or "- (데이터 없음)"


def run_technical(llm: LLMClient, snapshot: MarketSnapshot, ma: MarketAnalysis) -> dict:
    prompt = (f"기준 시점: {snapshot.as_of}\n\n미국 지수 및 기술적 지표(코드로 계산됨):\n{_idx_lines(snapshot.us_indices)}\n"
              f"\n장세: 추세={ma.regime_trend}, 변동성={ma.regime_volatility}\n\n기술적 관점에서 판단하세요.")
    return llm.complete_json(TECHNICAL_PROMPT, prompt, max_tokens=500)


def run_macro_flow(llm: LLMClient, snapshot: MarketSnapshot, ma: MarketAnalysis) -> dict:
    from data.kr_flow import describe
    lines = _idx_lines(snapshot.macro)
    flow = describe(snapshot.kr_flow)
    notes = "; ".join(n for n in ma.notes if any(k in n for k in ("원/달러", "금리", "WTI", "달러", "외국인"))) or "없음"
    prompt = (f"기준 시점: {snapshot.as_of}\n\n거시 지표:\n{lines}\n- {flow}\n\n규칙엔진 거시 관련 메모: {notes}\n\n"
              f"외국인 자금 환경 관점에서 판단하세요.")
    return llm.complete_json(MACRO_PROMPT, prompt, max_tokens=500)


def run_semiconductor(llm: LLMClient, snapshot: MarketSnapshot, ma: MarketAnalysis) -> dict:
    hint = f"규칙엔진 방향 힌트: {ma.direction_hint or '중립'} ({ma.direction_hint_reason or '-'})"
    prompt = (f"기준 시점: {snapshot.as_of}\n\nSOX(필라델피아 반도체):\n{_idx_lines(snapshot.us_indices, {'SOX'})}\n"
              f"참고(나스닥): {_idx_lines(snapshot.us_indices, {'나스닥'})}\n{hint}\n\n반도체 관점에서 판단하세요.")
    return llm.complete_json(SEMI_PROMPT, prompt, max_tokens=500)


LAST_NEWS_SOURCE = {"source": None}  # 파이프라인이 outcome 에 기록하기 위해 마지막 사용 출처를 남긴다


def _dedupe_titles(titles: list[str], limit: int) -> list[str]:
    seen, out = set(), []
    for t in titles:
        key = t.split("] ", 1)[-1]
        if key not in seen:
            seen.add(key)
            out.append(t)
    return out[:limit]


def fetch_recent_news_titles(as_of: date, days: int = 2, universe: Optional[list[dict]] = None) -> list[str]:
    """
    (V3.10) 핵심 2종목 + KOSPI 시총 상위에서 고른 추가 종목(기본 10)의 최근 뉴스 제목.
    (V3.13) 출처 선택: NEWS_SOURCE=auto 면 네이버 검색 API 키가 있을 때 공식 검색 API(약관상 정상 경로),
    없으면 증권 모바일 API. LLM 미사용. 형식: "[날짜][종목명] 제목". 실패한 종목은 건너뛴다.
    """
    from config import Config
    try:
        from data.universe import select_news_universe
    except Exception:  # noqa: BLE001
        return []
    stocks = universe if universe is not None else select_news_universe()
    per_core, per_extra = 15, 6
    mode = Config.NEWS_SOURCE
    if mode == "auto":  # (V3.15) 우선순위: 네이버 공식 검색 API(키 있을 때) → RSS(키 불필요) → 증권 모바일 API
        try:
            from data.news_search import naver_search_available
            mode = "search" if naver_search_available() else "rss"
        except Exception:  # noqa: BLE001
            mode = "rss"

    if mode == "search":
        try:
            from data.news_search import fetch_titles_via_search
            titles = fetch_titles_via_search(stocks, as_of, days, per_core, per_extra)
            LAST_NEWS_SOURCE["source"] = "naver_search_api"
            return _dedupe_titles(titles, Config.NEWS_MAX_TITLES)
        except Exception as e:  # noqa: BLE001 — 한도/인증 실패 시 RSS로 폴백
            print(f"[뉴스] 검색 API 실패({e}) → RSS로 대체")
            mode = "rss"

    if mode == "rss":
        try:
            from data.news_rss import fetch_titles_via_rss
            titles = fetch_titles_via_rss(stocks, as_of, days, per_core, per_extra)
            if titles:
                LAST_NEWS_SOURCE["source"] = "rss"
                return _dedupe_titles(titles, Config.NEWS_MAX_TITLES)
            print("[뉴스] RSS 결과 없음 → 증권 뉴스로 대체")
        except Exception as e:  # noqa: BLE001
            print(f"[뉴스] RSS 실패({e}) → 증권 뉴스로 대체")

    try:
        from data.news_collector import fetch_news_for_range
    except Exception:  # noqa: BLE001
        return []
    titles: list[str] = []
    for st in stocks:
        pages = Config.NEWS_PAGES_CORE if st.get("core") else Config.NEWS_PAGES_EXTRA
        limit = per_core if st.get("core") else per_extra
        try:
            items = fetch_news_for_range(st["code"], as_of - timedelta(days=days), as_of, max_pages=pages)
            titles += [f"[{it.date}][{st['name']}] {it.title}" for it in items[:limit]]
        except Exception:  # noqa: BLE001
            continue
    LAST_NEWS_SOURCE["source"] = "naver_stock_mobile"
    return _dedupe_titles(titles, Config.NEWS_MAX_TITLES)


def run_news_sentiment(llm: LLMClient, snapshot: MarketSnapshot, titles: list[str]) -> dict:
    if not titles:
        return {"direction": None, "confidence": "낮음", "summary": "최근 뉴스 없음 — 판단 보류 (LLM 호출 생략)",
                "notable_points": [], "skipped": True}
    prompt = (f"기준 시점: {snapshot.as_of}\n\n최근 뉴스 제목 {len(titles)}건:\n" + "\n".join(f"- {t}" for t in titles)
              + "\n\n시장 심리 관점에서 판단하세요.")
    return llm.complete_json(NEWS_PROMPT, prompt, max_tokens=500)


SPECIALISTS = [
    ("TechnicalAnalyst", "기술적"),
    ("MacroFlowAnalyst", "거시·수급"),
    ("SemiconductorAnalyst", "반도체"),
    ("NewsSentimentAnalyst", "뉴스·심리"),
]


def run_all_specialists(llm: LLMClient, snapshot: MarketSnapshot, ma: MarketAnalysis,
                        news_titles: Optional[list[str]] = None) -> dict:
    """4명 실행 → {"reports": {name: report}, "combined": 통합 리포트}."""
    reports = {
        "TechnicalAnalyst": run_technical(llm, snapshot, ma),
        "MacroFlowAnalyst": run_macro_flow(llm, snapshot, ma),
        "SemiconductorAnalyst": run_semiconductor(llm, snapshot, ma),
        "NewsSentimentAnalyst": run_news_sentiment(llm, snapshot, news_titles or []),
    }
    return {"reports": reports, "combined": combine_reports(reports)}


def combine_reports(reports: dict) -> dict:
    """코드 다수결로 통합 리포트를 만든다 (토론·QA 소스 자료). 동률이면 반도체 분석가 우선."""
    dirs = {n: r.get("direction") for n, r in reports.items() if r.get("direction") in ("up", "down")}
    ups = sum(1 for d in dirs.values() if d == "up")
    downs = len(dirs) - ups
    if not dirs:
        direction, confidence = None, "낮음"
    elif ups == downs:
        direction = dirs.get("SemiconductorAnalyst") or next(iter(dirs.values()))
        confidence = "낮음"
    else:
        direction = "up" if ups > downs else "down"
        confidence = "높음" if min(ups, downs) == 0 and len(dirs) >= 3 else "보통"
    label = dict(SPECIALISTS)
    summary = " / ".join(f"[{label[n]}] {r.get('summary', '')}" for n, r in reports.items() if r.get("summary"))
    points = []
    for n, r in reports.items():
        for p in r.get("notable_points") or []:
            points.append(f"({label[n]}) {p}")
    votes = ", ".join(f"{label[n]}={d}" for n, d in dirs.items())
    return {"direction": direction, "confidence": confidence, "summary": summary,
            "notable_points": points, "votes": votes, "up_votes": ups, "down_votes": downs}
