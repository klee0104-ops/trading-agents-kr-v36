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
        if method=='PUT': writes.append((endpoint,payload)); return {}
        if endpoint=='repos/'+p.SOURCE: return {'private':True,'has_pages':False}
        if endpoint=='repos/'+p.DESTINATION: return {'private':False,'full_name':p.DESTINATION}
        return None
    monkeypatch.setattr(p, 'api', fake)
    assert p.publish_report(f)['changed']
    assert len(writes)==1 and writes[0][0].endswith('/contents/index.html')

def test_cli_export_calls_publisher_only_after_generation(monkeypatch):
    import cli
    from web import export_site, publish_site
    calls=[]
    monkeypatch.setattr(export_site,'export_site',lambda: calls.append('export') or {'index':'report.html'})
    monkeypatch.setattr(publish_site,'publish_if_enabled',lambda path: calls.append('publish') or None)
    cli.cmd_export_site()
    assert calls==['export','publish']
