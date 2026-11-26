from web.export_site import _news_cores, _redact_deep, _REDACTED


def test_news_title_quoted_in_evidence_is_redacted():
    titles = ["[2026-09-28][삼성전자] 삼성전자, 4분기 HBM 공급 확대 계약 체결 전망", "[2026-09-28][기아] 짧음"]
    cores = _news_cores(titles)
    assert cores == ["삼성전자, 4분기 HBM 공급 확대 계약 체결 전망"]  # 10자 미만은 제외
    data = {
        "debate": [{"evidence": ["삼성전자, 4분기 HBM 공급 확대 계약 체결 전망", "SOX +1.41% 상승"]}],
        "analysts": {"News": {"summary": "뉴스: 삼성전자, 4분기 HBM  공급 확대 계약 체결 전망 등 긍정"}},
    }
    out = _redact_deep(data, cores)
    assert out["debate"][0]["evidence"] == [_REDACTED, "SOX +1.41% 상승"]
    assert out["analysts"]["News"]["summary"] == _REDACTED  # 공백 차이 무시


def test_no_titles_no_change():
    data = {"debate": [{"evidence": ["아무 문장"]}]}
    assert _redact_deep(data, _news_cores([])) == data


def test_shared_html_includes_simulated_returns_but_not_unknown_account_fields():
    import json
    import re
    from web.export_site import render_fragment
    page = render_fragment({'paper': {'days': 2, 'equity': 10100000,
        'total_return_pct': 1.0, 'holdings': {'069500': 10}, 'private_note': 'DO_NOT_SHARE'}})
    data = json.loads(re.search(r'id="ta-data">(.*?)</script>', page, re.S).group(1))
    assert data['paper']['total_return_pct'] == 1.0
    assert data['paper']['holdings'] == {'069500': 10}
    assert 'DO_NOT_SHARE' not in page
    assert 'data-tab="paper"' in page
    assert '실제 자금 거래 없음' in page
    assert 'fonts.googleapis.com' not in page
    assert 'fetch(' not in page


def test_shared_performance_audit_is_allowlisted_and_does_not_mix_actual_account():
    import json
    import re
    from web.export_site import render_fragment

    audit = {'direction': {'total': 5, 'correct': 4, 'accuracy_pct': 80.0,
                           'wilson_95_pct': [37.6, 96.4], 'secret': 'DO_NOT_SHARE'},
             'calibration': {'total': 6, 'brier': 0.24},
             'matched_etf': {'days': 2, 'ai_net_return_pct': 0.3,
                             'always_long_net_return_pct': 0.1, 'secret': 'DO_NOT_SHARE'},
             'secret': 'DO_NOT_SHARE'}
    page = render_fragment({'performance_evaluation': audit})
    public = json.loads(re.search(r'id="ta-data">(.*?)</script>', page, re.S).group(1))
    assert public['performance_evaluation']['direction']['total'] == 5
    assert public['performance_evaluation']['matched_etf']['days'] == 2
    assert 'DO_NOT_SHARE' not in page
    assert '실제 계좌 손익 아님' in page


def test_shared_partial_account_shows_only_dated_historical_valuation():
    import json
    import re
    import subprocess
    import shutil
    from web.export_site import render_fragment, _JS

    paper = {'portfolio_mode': True, 'initial_cash': 30_000_000, 'equity': None, 'total_return_pct': None,
             'valuation_audit': {'status': 'partial', 'note': '보유 시세 확인 대기'},
             'last_complete_valuation': {'date': '2026-10-08', 'equity': 29_947_922.36,
                 'contributed_capital': 30_000_000, 'total_return_pct': -.1736},
             'private_note': 'DO_NOT_SHARE'}
    page = render_fragment({'paper': paper})
    public = json.loads(re.search(r'id="ta-data">(.*?)</script>', page, re.S).group(1))['paper']
    assert public.get('equity') is None and public.get('total_return_pct') is None
    assert public['last_complete_valuation']['date'] == '2026-10-08'
    assert 'DO_NOT_SHARE' not in page
    node = shutil.which('node')
    assert node
    source = '''const vm=require('node:vm'), assert=require('node:assert/strict');
const nodes={}; const document={getElementById(id){return nodes[id]||(nodes[id]={textContent:id==='ta-data'?JSON.stringify(DATA):'',innerHTML:''});}};
vm.runInNewContext(SOURCE,{document});
const html=nodes.paper.innerHTML;
assert.ok(html.includes('마지막 전체 시세 평가 · 현재값 아님'));
assert.ok(html.includes('2026-10-08 기준'));
assert.ok(html.includes('현재 시세가 모두 확인될 때까지'));
assert.ok(!html.includes('평가금액 29,947,922원'));
'''.replace('DATA', json.dumps({'generated_at': '2026-10-11T13:00', 'paper': public}, ensure_ascii=False)).replace('SOURCE', json.dumps(_JS))
    result = subprocess.run([node, '-'], input=source, text=True, encoding='utf-8', capture_output=True)
    assert result.returncode == 0, result.stderr


def test_paper_public_uses_only_simulated_summary(monkeypatch):
    from execution.paper import PaperAccount
    from web.export_site import _paper_public
    monkeypatch.setattr(PaperAccount, 'summary', lambda self: {
        'days': 3, 'total_return_pct': 2.5, 'cash': 100, 'private_note': 'hidden'})
    assert _paper_public() == {'days': 3, 'total_return_pct': 2.5, 'cash': 100}


def test_unstarted_account_is_included_and_renders(monkeypatch):
    import json
    import shutil
    import subprocess
    from execution.paper import PaperAccount
    from web.export_site import _paper_public, _JS
    summary = PaperAccount(None, 10000000).summary()
    monkeypatch.setattr(PaperAccount, 'summary', lambda self: summary)
    public = _paper_public()
    assert public['days'] == 0 and public['cash'] == public['equity'] == 10000000
    node = shutil.which('node')
    assert node, 'Node is required to check shared report rendering'
    # Execute the actual exported renderer against an in-memory DOM.
    source = '''const vm=require('node:vm'), assert=require('node:assert/strict');
const nodes={}; const data=DATA;
const document={getElementById(id){return nodes[id]||(nodes[id]={textContent:id==='ta-data'?JSON.stringify(data):'',innerHTML:''});}};
vm.runInNewContext(SOURCE,{document});
const html=nodes.paper.innerHTML;
assert.ok(html.includes('10,000,000'));
assert.ok(html.includes('운용 시작 전'));
assert.ok(html.includes('0건'));
assert.ok(!/NaN|undefined|null%/.test(html));
'''.replace('DATA', json.dumps({'generated_at': '2026-09-28', 'paper': public}, ensure_ascii=False)).replace('SOURCE', json.dumps(_JS))
    result = subprocess.run([node, '-'], input=source, text=True, encoding='utf-8', capture_output=True)
    assert result.returncode == 0, result.stderr


def test_export_masks_loaded_arbitrary_secret(monkeypatch, tmp_path):
    from web import export_site as ex
    from security.secret_store import register_runtime_secret
    marker = 'arbitrary-' + 'private-token-for-test'
    def load(provider):
        register_runtime_secret(marker)
    monkeypatch.setattr(ex, 'get_api_key', load)
    monkeypatch.setattr(ex, 'build_snapshot_data', lambda: {'prediction': {'reasoning': [marker]}})
    ex.export_site(tmp_path)
    assert marker not in (tmp_path / 'index.html').read_text(encoding='utf-8')


def test_public_analyst_summary_uses_only_checked_points():
    from web.export_site import _public_analysts
    reports = {"TechnicalAnalyst": {
        "direction": "down", "confidence": "보통",
        "summary": "미국 지수 전체가 하락했다 [M4]",  # not checked
        "notable_points": ["SOX -1.15% [M4] — 출처: Yahoo Finance"],
        "citation_ok": True, "citation_check": {"valid": 1, "total": 2},
    }}
    public = _public_analysts(reports)["TechnicalAnalyst"]
    assert "미국 지수 전체" not in public["summary"]
    assert "SOX -1.15% [M4]" in public["summary"]
    assert public["citation_check"] == {"valid": 1, "total": 2}


def test_public_analyst_without_verified_points_is_not_directional():
    from web.export_site import _public_analysts
    reports = {"NewsSentimentAnalyst": {
        "direction": "up", "summary": "확인 안 된 호재", "notable_points": [],
        "citation_ok": False, "citation_check": {"valid": 0, "total": 2},
    }}
    public = _public_analysts(reports)["NewsSentimentAnalyst"]
    assert public["direction"] is None
    assert "확인 안 된 호재" not in public["summary"]
