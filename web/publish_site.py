"""Upload ONLY a validated HTML blob to a dedicated public Pages repository."""
from __future__ import annotations
import base64
import json
import os
from pathlib import Path
import shutil
import subprocess
from security.public_report import configured_secrets, harden_report, PublicationBlocked

ROOT = Path(__file__).resolve().parents[1]
DESTINATION = 'klee0104-ops/trading-agents-kr-report'
SOURCE = 'klee0104-ops/trading-agents-kr-v36'
URL = 'https://klee0104-ops.github.io/trading-agents-kr-report/'

def api(endpoint, method='GET', payload=None, missing=False):
    gh = shutil.which('gh')
    if not gh: raise PublicationBlocked('GitHub CLI is required for publication')
    args = [gh, 'api', '--hostname', 'github.com', endpoint, '--method', method]
    if payload is not None: args += ['--input', '-']
    try:
        proc = subprocess.run(args, input=json.dumps(payload) if payload is not None else None,
                              capture_output=True, text=True, encoding='utf-8', timeout=90)
    except subprocess.TimeoutExpired:
        raise PublicationBlocked('GitHub publication timed out') from None
    try: result = json.loads(proc.stdout or '{}')
    except ValueError: raise PublicationBlocked('Invalid GitHub response') from None
    if proc.returncode:
        if missing and str(result.get('status')) in {'404', '409'}: return None
        # Provider output might include request details. Never echo it to logs.
        raise PublicationBlocked('GitHub request failed; check login, permissions and network')
    return result

def publish_report(index_path):
    path = Path(index_path).resolve()
    if path != (ROOT / 'site/index.html').resolve() or Path(index_path).is_symlink():
        raise PublicationBlocked('Only site/index.html may be published')
    page = path.read_text(encoding='utf-8')
    blob = harden_report(page, configured_secrets()).encode('utf-8')
    # Checks must finish before any external write.
    private = api('repos/' + SOURCE)
    public = api('repos/' + DESTINATION)
    if private.get('private') is not True or private.get('has_pages'):
        raise PublicationBlocked('Source repository must remain private with Pages disabled')
    if public.get('private') is not False or public.get('full_name') != DESTINATION:
        raise PublicationBlocked('Incorrect public destination')
    entries = api('repos/' + DESTINATION + '/contents', missing=True) or []
    if any(e.get('type') != 'file' or e.get('name') not in {'index.html', '.nojekyll'} for e in entries):
        raise PublicationBlocked('Unexpected files in public repository')
    previous = api('repos/' + DESTINATION + '/contents/index.html', missing=True)
    if previous and base64.b64decode(previous.get('content', '')) == blob:
        return {'url': URL, 'changed': False}
    data = {'message': 'Update validated public simulation report', 'content': base64.b64encode(blob).decode(), 'branch': 'main'}
    if previous: data['sha'] = previous['sha']
    api('repos/' + DESTINATION + '/contents/index.html', 'PUT', data)
    return {'url': URL, 'changed': True}

def publish_if_enabled(index_path):
    config_path = ROOT / 'sharing_public.json'
    if not config_path.exists(): return None
    config = json.loads(config_path.read_text(encoding='utf-8'))
    if config.get('enabled') is not True: return None
    if os.environ.get('TA_HARNESS_OFFLINE') == '1':
        raise PublicationBlocked('Publication disabled during tests')
    return publish_report(index_path)
