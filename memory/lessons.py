"""
(V3.16) 복기 교훈 저장소 — "왜 틀렸나"를 상황 태그와 함께 저장하고, 비슷한 날에 다시 꺼내 준다.

파일: lessons.json (기록 옆). 한 건 = 한 날의 복기.
태그 = 그날의 상황(추세·변동성·SOX 방향·RSI·외국인·예측 방향·전망 단계). 오늘 태그와 겹치는
교훈을 손실 크기·최신순으로 골라 중재자·트레이더 프롬프트에 3~5개 넣는다.
"""
from __future__ import annotations
import json
import os
from datetime import date
from typing import Optional

DEFAULT_PATH = "./lessons.json"


def _support_text(s) -> str:
    if not isinstance(s, dict):
        return ""
    d = {"up": "상승", "down": "하락"}.get(s.get("direction"), "")
    base = f"비슷한 과거 {s.get('n', 0)}일 중 {s.get('match', 0)}일 실제 {d}" if s.get("n") else "비슷한 과거 사례 없음"
    return f" [{'검증된 패턴' if s.get('status') == 'confirmed' else '관찰(규칙 아님)'} · {base}]"


def situation_tags(market_analysis: dict | None, quant: dict | None, prediction: dict | None,
                   us_indices: list[dict] | None = None) -> list[str]:
    """상황 태그. 저장 때와 검색 때 같은 함수를 써서 어휘를 맞춘다."""
    tags: list[str] = []
    ma = market_analysis or {}
    if ma.get("regime_trend"):
        tags.append(f"추세:{ma['regime_trend']}")
    if ma.get("regime_volatility"):
        tags.append(f"변동성:{ma['regime_volatility']}")
    if ma.get("direction_hint"):
        tags.append(f"SOX힌트:{ma['direction_hint']}")
    lab = (quant or {}).get("labels") or {}
    for k in ("rsi", "macd", "ma", "flow", "momentum"):
        if lab.get(k):
            tags.append(f"{k}:{lab[k]}")
    tags += [t for t in ((quant or {}).get("special_tags") or []) if isinstance(t, str)]  # (V3.37) 특수 상황
    p = prediction or {}
    if p.get("direction"):
        tags.append(f"예측:{p['direction']}")
    if p.get("outlook"):
        tags.append(f"전망:{p['outlook']}")
    for q in us_indices or []:
        if q.get("name") == "SOX" and q.get("direction"):
            tags.append(f"SOX:{q['direction']}")
    return tags


class LessonStore:
    def __init__(self, path: Optional[str] = DEFAULT_PATH):
        self.path = path
        self._mem: list[dict] = []
        if path and not os.path.exists(path):
            self._write([])

    def _read(self) -> list[dict]:
        if self.path is None:
            return self._mem
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (OSError, ValueError):
            return []

    def _write(self, rows: list[dict]) -> None:
        if self.path is None:
            self._mem = rows
            return
        tmp = self.path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=2)
        os.replace(tmp, self.path)

    def all(self) -> list[dict]:
        return self._read()

    def has(self, target_date: str) -> bool:
        return any(r.get("target_date") == target_date for r in self._read())

    def add(self, lesson: dict) -> None:
        rows = [r for r in self._read() if r.get("target_date") != lesson.get("target_date")]
        rows.append(lesson)
        rows.sort(key=lambda r: r.get("target_date", ""))
        self._write(rows[-400:])  # 상한

    def select_relevant(self, today_tags: list[str], n: int = 5, as_of: Optional[date] = None) -> list[dict]:
        """태그 겹침 + 손실 크기 + 최신순. as_of 이전 것만(백테스트 look-ahead 방지)."""
        today = set(today_tags or [])
        scored = []
        for r in self._read():
            if as_of is not None and r.get("target_date", "") >= str(as_of):
                continue
            # A 50% forecast carried no directional conviction. Keep its
            # record, but do not recycle a no-trade observation as a win.
            if type(r.get("up_probability")) in (int, float) and r["up_probability"] == 50 and r.get("strategy_return_pct") is None:
                continue
            overlap = len(today & set(r.get("tags") or []))
            if overlap == 0 and today:
                continue
            loss = abs(float(r.get("strategy_return_pct") or 0.0))
            score = overlap * 2 + min(loss, 3.0) + (0.5 if not r.get("correct", True) else 0.0)
            scored.append((score, r.get("target_date", ""), r))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [r for _, _, r in scored[:n]]


_PRED_TAGS = ("예측:", "전망:")


def lesson_support(lesson: dict, records: list[dict], as_of: Optional[str] = None) -> dict:
    """(V3.37) 하루짜리 교훈이 '규칙'인지 검사: 그날과 상황이 비슷한 다른 검증일 중 실제 결과가
    교훈이 가리키는 방향(그날 실제 방향)과 같았던 비율. 예측·전망 태그는 비교에서 뺀다."""
    key = {t for t in (lesson.get("tags") or []) if not t.startswith(_PRED_TAGS)}
    want = lesson.get("actual_direction")
    if not key or want not in ("up", "down"):
        return {"n": 0, "match": 0, "status": "observation"}
    need = max(3, -(-len(key) * 6 // 10))  # 핵심 태그 60% 이상 겹침(최소 3개)
    similar = [r for r in records or [] if r.get("verified") and r.get("actual_direction") in ("up", "down")
               and r.get("target_date") != lesson.get("target_date")
               and (as_of is None or str(r.get("target_date", "")) < str(as_of))
               and len(key & set(r.get("situation_tags") or [])) >= need]
    n = len(similar)
    m = sum(r["actual_direction"] == want for r in similar)
    status = ("confirmed" if n >= 3 and m / n >= 0.6 else "contradicted" if n >= 3 and m / n <= 0.4
              else "observation")
    return {"n": n, "match": m, "status": status, "direction": want}


def annotate_lessons(lessons: list[dict], records: list[dict], as_of: Optional[str] = None) -> list[dict]:
    """교훈마다 근거 표본을 붙이고, 반박된 교훈은 뺀다."""
    out = []
    for r in lessons or []:
        s = lesson_support(r, records, as_of)
        if s["status"] == "contradicted":
            continue
        out.append({**r, "support": s})
    return out


def build_lessons_text(lessons: list[dict]) -> str:
    if not lessons:
        return ""
    lines = ["\n[복기 교훈 — 과거에 비슷한 상황에서 틀렸거나 손실 난 날의 기록. 같은 실수를 반복하지 마세요]",
             "(V3.37) 교훈마다 '비슷한 과거 n일 중 m일'이 붙어 있습니다. '관찰'은 한두 번뿐인 사례라 규칙으로 쓰지 말고 참고만 하세요."]
    for r in lessons:
        neutral = type(r.get("up_probability")) in (int, float) and r["up_probability"] == 50
        res = "중립·방향 채점 제외" if neutral else ("적중" if r.get("correct") else "빗나감")
        ret = r.get("strategy_return_pct")
        ret_txt = f", 수익 {ret:+.2f}%" if isinstance(ret, (int, float)) else ""
        lines.append(f"- {r.get('target_date')} 예측 {r.get('direction')} → 실제 {r.get('actual_direction')} ({res}{ret_txt}): "
                     f"{r.get('lesson', '')}"
                     + (f" [원인: {r['cause']}]" if r.get("cause") else "")
                     + _support_text(r.get("support")))
    lines.append("교훈은 참고용입니다. 오늘 데이터가 다르면 오늘 데이터를 우선하세요.")
    return "\n".join(lines)
