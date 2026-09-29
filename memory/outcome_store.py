"""
마지막 실제 분석 결과(파이프라인 outcome 전체)를 파일로 보관한다.
웹 대시보드가 페이지를 열자마자 "재실행 없이"(=API 과금 없이) 최신 결과를 보여주기 위함.
키/비밀값은 outcome에 들어가지 않지만, 혹시 몰라 저장 전에 redact를 한 번 거친다.
"""
from __future__ import annotations
import json
import os
from datetime import datetime

from security.secret_store import redact


def save_last_outcome(outcome: dict, path: str, mode: str = "real") -> None:
    data = {"saved_at": datetime.now().isoformat(timespec="seconds"), "mode": mode, "outcome": outcome}
    text = redact(json.dumps(data, ensure_ascii=False, indent=2, default=str))
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        f.write(text)
    os.replace(tmp, path)
    if mode == "real":
        from pathlib import Path
        from memory.research_reports import archive_short
        archive_short(json.loads(text), Path(path).parent / 'research_reports.json')


def load_last_outcome(path: str) -> dict | None:
    if not os.path.exists(path):
        return None
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)
