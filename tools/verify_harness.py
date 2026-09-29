"""Mandatory local quality gate. No paid analysis or publishing commands."""
from pathlib import Path
import os
import shutil
import subprocess
import sys
import tempfile
import uuid

ROOT = Path(__file__).resolve().parents[1]

def main():
    node = shutil.which('node')
    if not node:
        print('FAIL: Node.js is required for frontend verification.'); return 1
    env = dict(os.environ, TA_NO_NEWS='1', TA_HARNESS_OFFLINE='1', PYTHONDONTWRITEBYTECODE='1', PYTHONIOENCODING='utf-8')
    temp_root = Path(tempfile.gettempdir()) / ('ta-harness-' + uuid.uuid4().hex)
    temp_root.mkdir(mode=0o755)
    for key, filename in {'MEMORY_DB_PATH':'memory.json', 'LESSONS_PATH':'lessons.json',
                          'PAPER_ACCOUNT_PATH':'paper.json', 'PAPER_PORTFOLIO_PATH':'portfolio.json', 'LONG_RESEARCH_PATH':'long_research.json', 'LAST_OUTCOME_PATH':'outcome.json',
                          'COMPANY_RESEARCH_PATH':'company_research.json', 'REPORT_ARCHIVE_PATH':'research_reports.json', 'EVENT_ARCHIVE_DB_PATH':'events.db'}.items():
        env[key] = str(temp_root / filename)
    commands = [[node, '--check', 'web/static/app.js'],
                [node, 'tests/frontend_harness.cjs'],
                [sys.executable, '-m', 'pytest', 'tests', '-q', '--tb=short', '-p', 'no:cacheprovider', '--basetemp', str(temp_root / 'pytest')]]
    for command in commands:
        print('VERIFY:', ' '.join(command), flush=True)
        try:
            result = subprocess.run(command, cwd=ROOT, env=env, timeout=180)
        except subprocess.TimeoutExpired:
            print('FAIL: verification timed out'); return 1
        if result.returncode:
            print('FAIL: stop; do not package or publish.'); return result.returncode
    print('PASS: all local gates passed. This is not a production security certification.')
    return 0

if __name__ == '__main__':
    raise SystemExit(main())
