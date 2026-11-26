import json
import pytest
from web.export_site import render_fragment
from security.public_report import PublicationBlocked, harden_report

def report(data=None):
    return '<!doctype html><html><head></head><body>' + render_fragment(data or {'generated_at': '2026-09-29'}) + '</body></html>'

def test_hardened_report_has_csp_and_refresh():
    output = harden_report(report())
    assert 'Content-Security-Policy' in output and 'sha256-' in output
    assert 'refresh" content="300"' in output
    assert 'connect-src' in output and 'noindex,nofollow' in output


def test_public_kospi_refresh_only_reloads_published_snapshot():
    output = harden_report(report())
    assert 'id="btnRefreshKospi"' in output
    assert "url.searchParams.set('refresh',String(Date.now()))" in output
    assert 'location.assign(url.href)' in output
    assert "history.replaceState(null,'',location.pathname+'#today')" in output


def test_fixed_homepage_links_and_architecture_are_part_of_safe_report():
    output = harden_report(report())
    assert 'id="homeGuideTitle"' in output
    assert 'href="#process"' in output
    assert 'id="architectureHeading"' in output
    assert output.count('class="architecture-map"') == 1
    assert 'if(location.search)history.replaceState(null,\'\',location.pathname+location.hash)' in output
    assert 'grid-template-columns:repeat(5,minmax(0,1fr))' in output

@pytest.mark.parametrize('secret', ['ghp_' + 'x'*36, 'github_pat_'+'y'*60, 'AIza'+'z'*35, 'private-custom-value'])
def test_blocks_secrets_before_upload(secret):
    with pytest.raises(PublicationBlocked):
        harden_report(report({'prediction': {'reasoning': ['TEST_MARKER']}}).replace('TEST_MARKER', secret), [secret])

def test_blocks_json_escaped_credential():
    secret = 'custom-password-example'
    encoded = ''.join('\\u%04x' % ord(c) for c in secret)
    page = report({'notes': 'PLACEHOLDER'}).replace('PLACEHOLDER', encoded)
    with pytest.raises(PublicationBlocked): harden_report(page, [secret])

@pytest.mark.parametrize('extra', ['<script>alert(1)</script>', '<script src="https://example.com/a.js"></script>', '<iframe src="https://example.com"></iframe>', '<img src=x onerror="alert(1)">'])
def test_rejects_extra_active_content(extra):
    with pytest.raises(PublicationBlocked): harden_report(report().replace('</body>', extra+'</body>'))

def test_rejects_sensitive_field():
    with pytest.raises(PublicationBlocked): harden_report(report({'access_token': 'anything'}))

def test_rejects_markup_in_interpolated_data():
    with pytest.raises(PublicationBlocked):
        harden_report(report({'prediction': {'confidence': '<img src=x onerror=alert(1)>'}}))

def test_no_remote_call_when_validation_fails(monkeypatch, tmp_path):
    from web import publish_site as p
    (tmp_path/'site').mkdir()
    f=tmp_path/'site/index.html'; f.write_text(report({'password': 'bad'}), encoding='utf-8')
    monkeypatch.setattr(p, 'ROOT', tmp_path)
    monkeypatch.setattr(p, 'configured_secrets', lambda: [])
    def unexpected(*args, **kwargs): raise AssertionError('Network called before gate')
    monkeypatch.setattr(p, 'api', unexpected)
    with pytest.raises(PublicationBlocked): p.publish_report(f)

def test_only_index_uploaded(monkeypatch, tmp_path):
    from web import publish_site as p
    (tmp_path/'site').mkdir(); f=tmp_path/'site/index.html'; f.write_text(report(), encoding='utf-8')
    (tmp_path/'site/private.json').write_text('private', encoding='utf-8')
    monkeypatch.setattr(p, 'ROOT', tmp_path)
    monkeypatch.setattr(p, 'configured_secrets', lambda: [])
    writes=[]
    def fake(endpoint, method='GET', payload=None, missing=False):
        if method in ('PUT','POST','PATCH','DELETE'): writes.append((endpoint,method,payload)); return {'sha':'x'+str(len(writes))}
        if endpoint=='repos/'+p.SOURCE: return {'private':True,'has_pages':False}
        if endpoint=='repos/'+p.DESTINATION: return {'private':False,'full_name':p.DESTINATION}
        return None
    monkeypatch.setattr(p, 'api', fake)
    assert p.publish_report(f)['changed']
    # (V3.33) blob → tree(index.html·.nojekyll 만) → 부모 없는 커밋(noreply 작성자) → main 강제 교체
    assert [w[0].rsplit('/',1)[-1] for w in writes] == ['blobs','trees','commits','main']
    tree = writes[1][2]['tree']
    assert sorted(t['path'] for t in tree) == ['.nojekyll','index.html']
    commit = writes[2][2]
    assert commit['parents'] == [] and commit['author']['email'].endswith('@users.noreply.github.com')
    assert commit['committer'] == commit['author'] and '@gmail' not in str(writes)
    assert writes[3][1]=='PATCH' and writes[3][2]['force'] is True

def test_cli_export_calls_publisher_only_after_generation(monkeypatch):
    import cli
    from web import export_site, publish_site
    calls=[]
    monkeypatch.setattr(export_site,'export_site',lambda: calls.append('export') or {'index':'report.html'})
    monkeypatch.setattr(publish_site,'publish_if_enabled',lambda path: calls.append('publish') or None)
    cli.cmd_export_site()
    assert calls==['export','publish']


def test_paused_publication_allows_one_notice_then_stops(monkeypatch, tmp_path):
    # (V3.40) 일시 중지 중에는 공지 1회만 게시하고 그 뒤로는 업로드하지 않는다
    import json as _json
    from datetime import datetime as _dt, timedelta as _td, timezone as _tz
    from web import publish_site as p
    from data import sessions
    monkeypatch.setattr(p, 'ROOT', tmp_path)
    monkeypatch.delenv('TA_HARNESS_OFFLINE', raising=False)
    monkeypatch.setattr(sessions, 'kst_now', lambda: _dt(2026, 11, 24, 9, 30, tzinfo=_tz(_td(hours=9))))
    calls = []
    monkeypatch.setattr(p, 'publish_report', lambda path: calls.append(path) or {'url': p.URL, 'changed': True})
    cfg = tmp_path / 'sharing_public.json'
    cfg.write_text(_json.dumps({'enabled': True, 'paused_until': '2026-11-25', 'notice_publish_pending': True}), encoding='utf-8')
    assert p.publish_if_enabled('x')['changed'] is True
    assert _json.loads(cfg.read_text(encoding='utf-8'))['notice_publish_pending'] is False
    assert p.publish_if_enabled('x') == {'url': p.URL, 'changed': False, 'paused': True}
    assert len(calls) == 1
    cfg.write_text(_json.dumps({'enabled': True, 'paused_until': '2026-11-24'}), encoding='utf-8')
    assert p.publish_if_enabled('x')['changed'] is True and len(calls) == 2  # 기한 지나면 자동 재개
