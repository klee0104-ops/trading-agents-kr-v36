"""Optional 08:20 missed-analysis retry. Registration is intentionally separate."""
from __future__ import annotations

import argparse
from datetime import time
import json
from pathlib import Path


def same_day_analysis(path: Path, today: str) -> bool:
    try:
        envelope = json.loads(Path(path).read_text(encoding='utf-8'))
        return (envelope.get('mode') == 'real' and
                str(envelope.get('saved_at', ''))[:10] == today and
                (envelope.get('outcome') or {}).get('target_date') == today)
    except (OSError, ValueError, TypeError, KeyError):
        return False


def should_retry(now, outcome_path: Path, morning_status_path: Path) -> tuple[bool, str]:
    from data.krx_calendar import is_trading_day

    if not is_trading_day(now.date()):
        return False, '휴장일'
    if not time(8, 20) <= now.time().replace(tzinfo=None) < time(8, 45):
        return False, '08:20~08:45 창 밖'
    today = str(now.date())
    if same_day_analysis(outcome_path, today):
        return False, '당일 실제 분석 이미 저장됨'
    try:
        status = json.loads(morning_status_path.read_text(encoding='utf-8'))
        if str(status.get('at', ''))[:10] == today and status.get('status') in ('completed', 'completed_notification_warning'):
            return False, '아침 작업 이미 완료됨'
    except (OSError, ValueError, TypeError):
        pass
    return True, '당일 실제 분석 없음 · 한 번 재시도 가능'


def main() -> int:
    from config import Config
    from data.sessions import kst_now
    from tools.paper_portfolio import locked, write

    parser = argparse.ArgumentParser()
    parser.add_argument('--execute', action='store_true', help='Run paid morning analysis if missed')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    logs = root / 'logs'
    now = kst_now()
    try:
        with locked(logs / 'morning_retry_once'):
            due, reason = should_retry(now, Path(Config.LAST_OUTCOME_PATH), logs / 'morning_status.json')
            if not due or not args.execute:
                print('Morning retry: ' + reason + (' · 점검만' if due else ''))
                return 0
            receipt = logs / ('morning_retry_' + str(now.date()) + '.json')
            if receipt.exists():
                print('Morning retry: already attempted today')
                return 0
            write(receipt, {'at': now.isoformat(), 'status': 'reserved'})
            from tools.morning_run import main as morning_main
            result = morning_main()
            write(receipt, {'at': now.isoformat(), 'status': 'completed' if result == 0 else 'failed'})
            return result
    except (OSError, ValueError):
        print('Morning retry: lock or state error')
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
