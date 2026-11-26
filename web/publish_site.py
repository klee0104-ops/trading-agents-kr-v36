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
# GitHub 이 제공하는 비공개 이메일(noreply). 실제 이메일을 공개 저장소에 남기지 않는다.
PUBLISH_IDENTITY = {'name': 'signal-lab-publisher', 'email': '310007092+klee0104-ops@users.noreply.github.com'}

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
    # (V3.33) 개인정보·과거 공개본 노출 방지:
    #  ① 작성자를 GitHub noreply 주소로 고정 — 예전엔 계정 기본 이메일(Gmail)이 공개 커밋에 남았다.
    #  ② 매번 부모 없는 커밋 1개로 main 을 교체 — 공개 저장소에 지난 공개본 이력이 쌓이지 않는다.
    blob_sha = api('repos/' + DESTINATION + '/git/blobs', 'POST',
                   {'content': base64.b64encode(blob).decode(), 'encoding': 'base64'})['sha']
    tree_sha = api('repos/' + DESTINATION + '/git/trees', 'POST', {'tree': [
        {'path': 'index.html', 'mode': '100644', 'type': 'blob', 'sha': blob_sha},
        {'path': '.nojekyll', 'mode': '100644', 'type': 'blob', 'content': ''},
    ]})['sha']
    commit_sha = api('repos/' + DESTINATION + '/git/commits', 'POST', {
        'message': 'Update validated public simulation report', 'tree': tree_sha, 'parents': [],
        'author': dict(PUBLISH_IDENTITY), 'committer': dict(PUBLISH_IDENTITY)})['sha']
    api('repos/' + DESTINATION + '/git/refs/heads/main', 'PATCH', {'sha': commit_sha, 'force': True})
    return {'url': URL, 'changed': True}

def publish_if_enabled(index_path):
    config_path = ROOT / 'sharing_public.json'
    if not config_path.exists(): return None
    config = json.loads(config_path.read_text(encoding='utf-8'))
    if config.get('enabled') is not True: return None
    if os.environ.get('TA_HARNESS_OFFLINE') == '1':
        raise PublicationBlocked('Publication disabled during tests')
    # (V3.40) 일시 중지: paused_until(KST 날짜) 전까지 업로드하지 않는다.
    # notice_publish_pending 이 true 면 공지가 담긴 다음 게시 1회만 허용하고 스스로 false 로 바꾼다.
    paused_until = str(config.get('paused_until') or '')
    if paused_until:
        from data.sessions import kst_now
        if str(kst_now().date()) < paused_until:
            if config.get('notice_publish_pending') is True:
                result = publish_report(index_path)
                config['notice_publish_pending'] = False
                config['notice_published_at'] = kst_now().isoformat(timespec='seconds')
                tmp = config_path.with_name(config_path.name + '.tmp')
                tmp.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding='utf-8')
                os.replace(tmp, config_path)
                return result
            return {'url': URL, 'changed': False, 'paused': True}
    return publish_report(index_path)
