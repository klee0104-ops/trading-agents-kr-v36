"""
event_archive.db에 쌓인 과거 변동성 이벤트+뉴스 중, 오늘 상황과 비슷한 사례를
찾아주는 유사 사례 검색.

로컬 TF-IDF 코사인 유사도로 계산한다(scikit-learn). LLM이나 외부 임베딩 API를
전혀 쓰지 않으므로 토큰 비용이 없고, 네트워크 없이도 즉시 동작한다.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Optional

from memory.event_archive import get_all_cases_with_news, DEFAULT_DB_PATH


@dataclass
class SimilarCase:
    event_date: str
    stock_name: str
    change_pct: float
    direction: str
    news_titles: list[str]
    similarity: float


def find_similar_cases(query_text: str, db_path: str = DEFAULT_DB_PATH,
                        top_k: int = 5, min_similarity: float = 0.05,
                        as_of: Optional[str] = None) -> list[SimilarCase]:
    """
    as_of가 주어지면 event_date <= as_of 인 사례만 검색 대상으로 삼는다.
    백테스트에서 look-ahead(미래 이벤트/뉴스 유출)를 막기 위한 것으로,
    data/market_data.py의 backtest_mode 설계 원칙과 동일하다.
    """
    cases = get_all_cases_with_news(db_path)
    if as_of:
        cases = [c for c in cases if c["event_date"] <= as_of]
    if not cases or not query_text.strip():
        return []

    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    texts = [
        f"{c['stock_name']} {c['direction']} {c['change_pct']}% " + " ".join(c["news_titles"])
        for c in cases
    ]
    corpus = texts + [query_text]

    vectorizer = TfidfVectorizer()
    try:
        matrix = vectorizer.fit_transform(corpus)
    except ValueError:
        return []  # 어휘가 전혀 없는 경우(빈 텍스트만 있을 때 등)

    sims = cosine_similarity(matrix[-1], matrix[:-1])[0]
    ranked = sorted(zip(cases, sims), key=lambda pair: -pair[1])

    results = []
    for case, sim in ranked[:top_k]:
        if sim < min_similarity:
            continue
        results.append(SimilarCase(
            event_date=case["event_date"],
            stock_name=case["stock_name"],
            change_pct=case["change_pct"],
            direction=case["direction"],
            news_titles=case["news_titles"][:3],
            similarity=round(float(sim), 3),
        ))
    return results


def build_similar_cases_context_text(cases: list[SimilarCase]) -> str:
    if not cases:
        return ""
    lines = ["\n[참고: 과거 유사 변동성 사례 (자동 검색, 토큰 미사용)]"]
    for c in cases:
        news_part = f" — 관련뉴스: {', '.join(c.news_titles)}" if c.news_titles else ""
        lines.append(f"- {c.event_date} {c.stock_name} {c.change_pct:+.2f}%({c.direction}, 유사도 {c.similarity}){news_part}")
    lines.append("이 사례들은 자동 검색된 참고용 맥락일 뿐이며, 오늘 상황과 정확히 같지 않을 수 있습니다.")
    return "\n".join(lines)
