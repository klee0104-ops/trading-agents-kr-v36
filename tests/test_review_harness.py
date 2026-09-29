from pathlib import Path
from html.parser import HTMLParser
import re
import zipfile
import pytest

ROOT = Path(__file__).resolve().parents[1]

def test_dashboard_contract():
    class Page(HTMLParser):
        ids = []
        scripts = []
        tabs = []
        def handle_starttag(self, tag, attrs):
            a = dict(attrs)
            if 'id' in a: self.ids.append(a['id'])
            if tag == 'script': self.scripts.append(a.get('src'))
            if 'data-tab' in a: self.tabs.append(a['data-tab'])
    page = Page()
    page.feed((ROOT / 'web/static/index.html').read_text(encoding='utf-8'))
    assert '/app.js' in page.scripts
    assert len(page.ids) == len(set(page.ids))
    assert len(page.tabs) == 9
    assert "long" in page.tabs and "reports" in page.tabs
    assert all('tab-' + tab in page.ids for tab in page.tabs)
    js = (ROOT / 'web/static/app.js').read_text(encoding='utf-8')
    required = set(re.findall(r'\$\("#([\w-]+)"\)', js)) - {'ticker'}
    assert required <= set(page.ids), required - set(page.ids)

@pytest.fixture
def client(monkeypatch):
    from web import server
    from config import Config
    monkeypatch.setattr(Config, 'WEB_READONLY', False)
    return server.app.test_client(), {'Host': f'127.0.0.1:{Config.WEB_PORT}', 'X-TA-Token': server.SESSION_TOKEN}

@pytest.mark.parametrize('body', [[], ['bad'], 1, 'bad', None])
@pytest.mark.parametrize('route', ['/api/run', '/api/collect'])
def test_non_object_json_rejected(client, body, route):
    import json
    c, headers = client
    assert c.post(route, data=json.dumps(body), content_type='application/json', headers=headers).status_code == 400

def test_request_body_limit(client):
    c, headers = client
    assert c.post('/api/run', json={'mode': 'x' * 17000}, headers=headers).status_code == 413

def test_bad_days(client):
    c, headers = client
    assert c.get('/api/kospi?days=bad', headers=headers).status_code == 400

def test_paper_errors_redacted(client, monkeypatch):
    from execution.paper import PaperAccount
    from security.secret_store import register_runtime_secret
    secret = 'test-only-' + 'sensitive-value'
    register_runtime_secret(secret)
    def boom(*args, **kwargs): raise ValueError(secret)
    monkeypatch.setattr(PaperAccount, 'summary', boom)
    c, headers = client
    r = c.get('/api/paper', headers=headers)
    assert r.status_code == 500
    assert secret not in r.get_data(as_text=True)

def test_package_excludes_private_outputs(tmp_path):
    from security.scan import build_release_zip
    root = tmp_path / 'project'; root.mkdir()
    private = ['paper_account.json', 'lessons.json', 'backtest_results_v2.json', 'backtest_log.txt', 'report.pdf', 'flow_debug.html']
    for name in private: (root / name).write_text('private', encoding='utf-8')
    (root / 'cli.py').write_text('# public source', encoding='utf-8')
    result = build_release_zip(tmp_path / 'release.zip', root)
    assert result['ok']
    with zipfile.ZipFile(result['zip']) as z:
        assert z.namelist() == ['project/cli.py']
