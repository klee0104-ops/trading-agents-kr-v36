"""Limit scheduled commands to the KST weekday operating window."""
from __future__ import annotations

import argparse
from datetime import datetime, time, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]
KST = timezone(timedelta(hours=9))
OPEN_TIME = time(4)
CLOSE_TIME = time(16)
JOBS = {
    "long_research": ("tools.long_research", "--cycle"),
    "morning_retry": ("tools.morning_retry_once", "--execute"),
    "paper_portfolio": ("tools.paper_portfolio", "--tick"),
    "public_quotes": ("tools.refresh_public_quotes",),
    "recommendations": ("tools.research_recommendations", "--scheduled"),
    "short_review": ("tools.short_intraday_review",),
    "us_tick": ("tools.us_paper", "--tick"),
    "us_research": ("tools.us_paper", "--cycle"),
}


def run_weekday(now: datetime, job=None, batch=None, *, root=ROOT, run=None):
    if now.tzinfo is None:
        raise ValueError("Scheduler clock must include its timezone")
    kst_now = now.astimezone(KST)
    if kst_now.weekday() >= 5:
        print("Scheduled task: skipped_weekend (KST)")
        return 0
    if not OPEN_TIME <= kst_now.time() < CLOSE_TIME:
        print("Scheduled task: skipped_outside_hours (KST 04:00–16:00)")
        return 0
    if (job is None) == (batch is None):
        raise ValueError("Choose exactly one scheduled command")
    root = Path(root).resolve()
    # (V3.41) 전체 일시 정지 스위치: scheduler_pause.json {"paused": true} 이면 아무것도 실행하지 않는다
    # (예약 작업 비활성화가 안 됐거나 누가 다시 켰을 때의 안전장치)
    try:
        import json as _json
        if _json.loads((root / "scheduler_pause.json").read_text(encoding="utf-8")).get("paused") is True:
            print("Scheduled task: skipped_paused (scheduler_pause.json)")
            return 0
    except (OSError, ValueError, AttributeError):
        pass
    if batch is not None:
        path = Path(batch).resolve()
        if path.parent != root or path.suffix.lower() != ".bat" or not path.is_file():
            raise ValueError("Batch must be a project-root .bat file")
        # cmd receives a single quoted script path. No arbitrary shell text is accepted.
        command = [os.environ.get("COMSPEC", "cmd.exe"), "/d", "/c", str(path)]
    else:
        command = [sys.executable, "-B", "-m", *JOBS[job]]
    runner = run or subprocess.run
    result = runner(command, cwd=root, env=dict(os.environ, PYTHONIOENCODING="utf-8"),
                    creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    return result.returncode


def main():
    parser = argparse.ArgumentParser()
    selection = parser.add_mutually_exclusive_group(required=True)
    selection.add_argument("--job", choices=JOBS)
    selection.add_argument("--batch")
    args = parser.parse_args()
    return run_weekday(datetime.now(KST), args.job, args.batch)


if __name__ == "__main__":
    raise SystemExit(main())
