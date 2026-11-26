"""Add the optional consensus field without changing historical forecasts."""
from __future__ import annotations

import argparse
from datetime import datetime
import json
import os
from pathlib import Path
import shutil


def migrate(path: Path, *, apply: bool = False, now: datetime | None = None) -> dict:
    path = Path(path)
    data = json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(data, dict) or any(not isinstance(r, dict) for r in data.values()):
        raise ValueError('Unexpected prediction store schema')
    missing = [key for key, record in data.items() if 'consensus_score' not in record]
    if not apply or not missing:
        return {'records': len(data), 'missing': len(missing), 'applied': False, 'backup': None}
    stamp = (now or datetime.now().astimezone()).strftime('%Y%m%d-%H%M%S')
    backup = path.parent / '.review-backups' / ('consensus-' + stamp)
    backup.mkdir(parents=True, exist_ok=False)
    shutil.copy2(path, backup / path.name)
    for key in missing:
        data[key]['consensus_score'] = None
    tmp = path.with_name(path.name + '.consensus-pending')
    try:
        tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2, allow_nan=False), encoding='utf-8')
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)
    return {'records': len(data), 'missing': len(missing), 'applied': True,
            'backup': str(backup / path.name)}


def main() -> int:
    from config import Config
    parser = argparse.ArgumentParser()
    parser.add_argument('--apply', action='store_true')
    args = parser.parse_args()
    result = migrate(Path(Config.MEMORY_DB_PATH), apply=args.apply)
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
