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
            overlap = len(today & set(r.get("tags") or []))
            if overlap == 0 and today:
                continue
            loss = abs(float(r.get("strategy_return_pct") or 0.0))
            score = overlap * 2 + min(loss, 3.0) + (0.5 if not r.get("correct", True) else 0.0)
            scored.append((score, r.get("target_date", ""), r))
        scored.sort(key=lambda x: (x[0], x[1]), reverse=True)
        return [r for _, _, r in scored[:n]]


def build_lessons_text(lessons: list[dict]) -> str:
    if not lessons:
        return ""
    lines = ["\n[복기 교훈 — 과거에 비슷한 상황에서 틀렸거나 손실 난 날의 기록. 같은 실수를 반복하지 마세요]"]
    for r in lessons:
        res = "적중" if r.get("correct") else "빗나감"
        ret = r.get("strategy_return_pct")
        ret_txt = f", 수익 {ret:+.2f}%" if isinstance(ret, (int, float)) else ""
        lines.append(f"- {r.get('target_date')} 예측 {r.get('direction')} → 실제 {r.get('actual_direction')} ({res}{ret_txt}): "
                     f"{r.get('lesson', '')}"
                     + (f" [원인: {r['cause']}]" if r.get("cause") else ""))
    lines.append("교훈은 참고용입니다. 오늘 데이터가 다르면 오늘 데이터를 우선하세요.")
    return "\n".join(lines)
