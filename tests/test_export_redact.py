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


def test_shared_html_excludes_account_and_external_fonts():
    from web.export_site import render_fragment
    secret_account = 'PRIVATE_ACCOUNT_SENTINEL'
    page = render_fragment({'paper': {'holdings': {secret_account: 123}}})
    assert secret_account not in page
    assert 'data-tab="paper"' not in page
    assert 'fonts.googleapis.com' not in page
    assert 'fetch(' not in page


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
