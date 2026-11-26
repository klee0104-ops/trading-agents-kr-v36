"""Publish only the public breaking-news banner after a detection.

This path never fetches prices, calls a model, or touches a paper ledger.
It retries on the next watcher tick when publication fails.
"""
from __future__ import annotations

from pathlib import Path
import json
import os

ROOT = Path(__file__).resolve().parents[1]


def publish_issues(issues: list[dict], root: Path = ROOT, publisher=None) -> str:
    from security.public_report import Scripts, check_data, configured_secrets, harden_report
    from web.export_site import render_fragment
    from web.publish_site import publish_if_enabled

    root = Path(root)
    settings = json.loads((root / 'sharing_public.json').read_text(encoding='utf-8'))
    if settings.get('enabled') is not True:
        return 'disabled'
    path = root / 'site' / 'index.html'
    original = path.read_bytes()
    parser = Scripts()
    parser.feed(original.decode('utf-8'))
    payloads = [text for attrs, text in parser.scripts
                if attrs.get('id') == 'ta-data' and attrs.get('type') == 'application/json']
    if len(payloads) != 1:
        raise ValueError('Invalid public report data')
    secrets = configured_secrets()
    data = json.loads(payloads[0])
    check_data(data, secrets)
    if data.get('urgent_issues') == issues:
        return 'unchanged'
    data['urgent_issues'] = issues
    check_data(data, secrets)
    page = ('<!doctype html><html lang="ko"><head><meta charset="utf-8">'
            '<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">'
            '</head><body>' + render_fragment(data) + '</body></html>')
    page = harden_report(page, secrets)
    blob = page.encode('utf-8')
    if path.read_bytes() != original:
        raise RuntimeError('Public report changed during breaking-news update')
    temp = path.with_name('breaking.pending.html')
    temp.write_bytes(blob)
    os.replace(temp, path)
    try:
        publish = publisher or publish_if_enabled
        result = publish(path)
        if result is None:
            raise RuntimeError('Public report publication is disabled')
    except Exception:
        if path.read_bytes() == blob:
            temp.write_bytes(original)
            os.replace(temp, path)
        raise
    return 'published' if result.get('changed') else 'unchanged'
