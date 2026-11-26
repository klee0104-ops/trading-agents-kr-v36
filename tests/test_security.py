"""
API 키 보안 회귀 테스트.

1. SecretStr: print/repr/f-string/json 어디로도 평문이 새지 않는다
2. 키 우선순위: 시스템 환경변수 > 자격 증명 저장소 > .env, 자리표시자는 "없음" 취급
3. redact(): 로그/에러 메시지에서 알려진 키 형태와 실제 로드된 키 값을 가린다
4. .env의 키는 os.environ에 올라가지 않는다 (자식 프로세스 상속 방지)
5. 웹 서버: Host 헤더/Origin/세션 토큰 검사, 에러 응답 마스킹, 키 미노출
6. 공유용 zip에 .env / DB / 기록이 포함되지 않는다
"""
import os
import sys
import json
import zipfile
import logging
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pytest

from security import secret_store as ss
from security.secret_store import SecretStr, mask, redact, is_placeholder

FAKE_GEMINI = "AQ.Ab8RN6" + "x" * 44
FAKE_ANTHROPIC = "sk-ant-api03-" + "y" * 40
FAKE_DART = "0123456789abcdef" * 2 + "01234567"


# ---------------------------------------------------------------- SecretStr
def test_secretstr_never_prints_plaintext():
    s = SecretStr(FAKE_GEMINI, "keyring")
    for rendered in (str(s), repr(s), f"{s}", "%s" % s, format(s, "")):
        assert FAKE_GEMINI not in rendered
        assert FAKE_GEMINI[-4:] in rendered  # 끝 4자리만 보인다
    assert s.reveal() == FAKE_GEMINI
    with pytest.raises(TypeError):
        json.dumps({"k": s})
    import pickle
    with pytest.raises(TypeError):
        pickle.dumps(s)


def test_placeholders_are_treated_as_missing():
    for v in ["", "sk-ant-...", "AIza...", "your-key-here", "<YOUR_KEY>", "short"]:
        assert is_placeholder(v)
    assert not is_placeholder(FAKE_GEMINI)


# ---------------------------------------------------------------- 우선순위
@pytest.fixture
def isolated(monkeypatch, tmp_path):
    env_file = tmp_path / ".env"
    monkeypatch.setattr(ss, "DOTENV_PATH", env_file)
    monkeypatch.setattr(ss, "_read_dotenv", lambda path=env_file: ss.__dict__["_orig_read"](env_file))
    monkeypatch.setattr(ss, "_PROCESS_ENV_SNAPSHOT", {})
    store = {}
    monkeypatch.setattr(ss, "_keyring_get", lambda name: store.get(name))
    return env_file, store


ss.__dict__.setdefault("_orig_read", ss._read_dotenv)


def test_priority_env_over_keyring_over_dotenv(isolated, monkeypatch):
    env_file, store = isolated
    env_file.write_text(f"GEMINI_API_KEY={FAKE_GEMINI}DOTENV\n", encoding="utf-8")
    assert ss.get_api_key("gemini").source == ".env"

    store["GEMINI_API_KEY"] = FAKE_GEMINI + "KEYRING"
    assert ss.get_api_key("gemini").source == "keyring"

    monkeypatch.setattr(ss, "_PROCESS_ENV_SNAPSHOT", {"GEMINI_API_KEY": FAKE_GEMINI + "ENV"})
    k = ss.get_api_key("gemini")
    assert k.source == "environment" and k.reveal().endswith("ENV")


def test_placeholder_in_dotenv_means_no_key(isolated):
    env_file, _ = isolated
    env_file.write_text("ANTHROPIC_API_KEY=sk-ant-...\n", encoding="utf-8")
    k = ss.get_api_key("anthropic")
    assert not k and k.source == "none"


def test_status_warns_about_plaintext_in_dotenv(isolated):
    env_file, store = isolated
    env_file.write_text(f"GEMINI_API_KEY={FAKE_GEMINI}\n", encoding="utf-8")
    store["GEMINI_API_KEY"] = FAKE_GEMINI
    st = ss.key_status("gemini")
    assert st.source == "keyring" and st.dotenv_has_plaintext is True
    assert FAKE_GEMINI not in st.masked


def test_remove_key_from_dotenv_keeps_other_settings(isolated):
    env_file, _ = isolated
    env_file.write_text(f"LLM_PROVIDER=gemini\nGEMINI_API_KEY={FAKE_GEMINI}\nDEBATE_MAX_ROUNDS=3\n",
                        encoding="utf-8")
    assert ss.remove_key_from_dotenv("gemini", env_file)
    text = env_file.read_text(encoding="utf-8")
    assert FAKE_GEMINI not in text
    assert "LLM_PROVIDER=gemini" in text and "DEBATE_MAX_ROUNDS=3" in text


def test_unknown_provider_rejected():
    with pytest.raises(ValueError):
        ss.get_api_key("openai")


def test_dart_key_is_separate_and_url_is_redacted(isolated):
    _, store = isolated
    store["DART_API_KEY"] = FAKE_DART
    key = ss.get_api_key("dart")
    assert key.source == "keyring" and key.reveal() == FAKE_DART
    assert not ss.get_api_key("gemini")
    assert ss.key_status("dart").env_name == "DART_API_KEY"
    assert FAKE_DART not in ss.redact("https://opendart.fss.or.kr/api/company.json?crtfc_key=" + FAKE_DART)


def test_dart_key_requires_forty_alphanumeric_characters():
    with pytest.raises(ValueError):
        ss.store_in_keyring("dart", "short-key")
    with pytest.raises(ValueError):
        ss.store_in_keyring("dart", "!" * 40)


def test_dart_cli_never_prints_key(monkeypatch, capsys):
    import cli
    monkeypatch.setattr("getpass.getpass", lambda _: FAKE_DART)
    monkeypatch.setattr(ss, "keyring_available", lambda: True)
    monkeypatch.setattr(ss, "store_in_keyring", lambda provider, value: None)
    monkeypatch.setattr(ss, "key_status", lambda provider: ss.KeyStatus(provider, "DART_API_KEY", True, "keyring", "hidden", False))
    cli.cmd_set_key(["dart"])
    cli.cmd_key_status()
    out = capsys.readouterr().out
    assert "DART_API_KEY" in out
    assert FAKE_DART not in out and FAKE_DART[:4] not in out and FAKE_DART[-4:] not in out


# ---------------------------------------------------------------- redaction
def test_redact_known_patterns_and_runtime_secrets():
    weird = "custom-secret-value-123456"
    ss.register_runtime_secret(weird)
    msg = (f"401 Unauthorized key={FAKE_GEMINI} header x-api-key: {FAKE_ANTHROPIC} "
           f"url=https://x.googleapis.com/v1?key=ABCDEF123 other={weird}")
    out = redact(msg)
    for secret in (FAKE_GEMINI, FAKE_ANTHROPIC, "ABCDEF123", weird):
        assert secret not in out


def test_logging_filter_masks_keys(caplog):
    ss.install_global_redaction()
    logger = logging.getLogger("ta.test")
    logger.addFilter(ss.RedactingFilter())
    with caplog.at_level(logging.INFO, logger="ta.test"):
        logger.info("calling with %s", FAKE_GEMINI)
    assert FAKE_GEMINI not in caplog.text


def test_llm_errors_are_redacted_and_unchained():
    """SDK 예외에 키가 섞여 있어도 LLMCallError 메시지/traceback에는 나오지 않아야 한다."""
    from agents.base import GeminiLLMClient, LLMCallError

    client = GeminiLLMClient.__new__(GeminiLLMClient)
    client.model = "m"
    client.web_search_enabled = False

    def boom(config, user):
        raise RuntimeError(f"bad request for key {FAKE_GEMINI}")
    client._call_with_retry = boom

    pytest.importorskip("google.genai")
    with pytest.raises(LLMCallError) as ei:
        client.complete("s", "u")
    assert FAKE_GEMINI not in str(ei.value)
    assert ei.value.__suppress_context__ is True


def test_client_repr_hides_key():
    from agents.base import AnthropicLLMClient
    c = AnthropicLLMClient.__new__(AnthropicLLMClient)
    c.model = "claude"
    assert "sk-ant" not in repr(c)


# ---------------------------------------------------------------- config
def test_dotenv_keys_not_exported_to_os_environ():
    import config  # noqa: F401  (import 시 .env 로드)
    for name in ss.KEY_NAMES.values():
        if name in ss._PROCESS_ENV_SNAPSHOT:
            continue  # 사용자가 시스템 환경변수로 직접 설정한 경우는 예외
        assert name not in os.environ


# ---------------------------------------------------------------- web server
@pytest.fixture
def web(monkeypatch, tmp_path):
    flask = pytest.importorskip("flask")
    from web import server
    from config import Config
    monkeypatch.setattr(Config, "MEMORY_DB_PATH", str(tmp_path / "mem.json"))
    monkeypatch.chdir(tmp_path)  # demo_memory.json이 프로젝트 폴더를 더럽히지 않게
    server.app.config["TESTING"] = True
    return server, server.app.test_client(), f"127.0.0.1:{Config.WEB_PORT}"


def test_web_rejects_foreign_host_header(web):
    server, client, host = web
    r = client.get("/api/health", headers={"Host": "evil.example.com"})
    assert r.status_code == 403


def test_web_post_requires_token(web):
    server, client, host = web
    r = client.post("/api/run", json={"mode": "demo"}, headers={"Host": host})
    assert r.status_code == 403


def test_web_post_rejects_cross_origin(web):
    server, client, host = web
    r = client.post("/api/run", json={"mode": "demo"},
                    headers={"Host": host, "Origin": "https://evil.example.com",
                             "X-TA-Token": server.SESSION_TOKEN})
    assert r.status_code == 403


def test_web_post_rejects_non_json_form_csrf(web):
    server, client, host = web
    r = client.post("/api/collect", data="stock_name=x", content_type="text/plain",
                    headers={"Host": host, "X-TA-Token": server.SESSION_TOKEN})
    assert r.status_code == 415


def test_web_demo_run_with_token_succeeds(web):
    server, client, host = web
    r = client.post("/api/run", json={"mode": "demo"},
                    headers={"Host": host, "Origin": f"http://{host}", "X-TA-Token": server.SESSION_TOKEN})
    assert r.status_code == 200
    assert "final_result" in r.get_json()


def test_web_index_embeds_token_and_security_headers(web):
    server, client, host = web
    r = client.get("/", headers={"Host": host})
    body = r.get_data(as_text=True)
    assert server.SESSION_TOKEN in body
    assert "__TA_SESSION_TOKEN__" not in body
    assert r.headers["X-Frame-Options"] == "DENY"
    assert "default-src 'self'" in r.headers["Content-Security-Policy"]


def test_web_health_never_returns_key(web, monkeypatch):
    server, client, host = web
    from config import Config
    monkeypatch.setattr(Config, "api_key", staticmethod(lambda p=None: SecretStr(FAKE_GEMINI, "keyring")))
    r = client.get("/api/health", headers={"Host": host})
    body = r.get_data(as_text=True)
    assert FAKE_GEMINI not in body and FAKE_GEMINI[-4:] not in body
    assert r.get_json()["key_source"] == "keyring"


def test_web_error_response_is_redacted(web, monkeypatch):
    server, client, host = web

    class Boom:
        def __init__(self, *a, **k):
            pass

        def run(self):
            raise RuntimeError(f"upstream said key={FAKE_GEMINI}")
    monkeypatch.setattr(server, "TradingAgentsPipeline", Boom)
    r = client.post("/api/run", json={"mode": "demo"},
                    headers={"Host": host, "X-TA-Token": server.SESSION_TOKEN})
    assert r.status_code == 500
    assert FAKE_GEMINI not in r.get_data(as_text=True)


def test_web_static_only_serves_known_files(web):
    server, client, host = web
    assert client.get("/app.js", headers={"Host": host}).status_code == 200
    for p in ("/../.env", "/.env", "/config.py", "/memory_store.json"):
        assert client.get(p, headers={"Host": host}).status_code in (403, 404)


def test_web_collect_validates_input(web):
    server, client, host = web
    h = {"Host": host, "X-TA-Token": server.SESSION_TOKEN}
    r = client.post("/api/collect", json={"stock_name": "<script>", "start": "2026-01-01", "end": "2026-01-02"},
                    headers=h)
    assert r.status_code == 400
    r = client.post("/api/collect", json={"stock_name": "삼성전자", "start": "2020-01-01", "end": "2026-01-02"},
                    headers=h)
    assert r.status_code == 400


# ---------------------------------------------------------------- packaging / scan
def test_release_zip_excludes_secrets_and_private_data(tmp_path):
    from security.scan import build_release_zip
    proj = tmp_path / "proj"
    (proj / "agents").mkdir(parents=True)
    (proj / "agents" / "a.py").write_text("print('hi')\n", encoding="utf-8")
    (proj / ".env").write_text(f"GEMINI_API_KEY={FAKE_GEMINI}\n", encoding="utf-8")
    (proj / ".env.example").write_text("GEMINI_API_KEY=\n", encoding="utf-8")
    (proj / "memory_store.json").write_text("{}", encoding="utf-8")
    (proj / "event_archive.db").write_bytes(b"x")
    (proj / ".claude").mkdir()
    (proj / ".claude" / "settings.local.json").write_text("{}", encoding="utf-8")
    (proj / "venv").mkdir()
    (proj / "venv" / "x.py").write_text("", encoding="utf-8")

    r = build_release_zip(tmp_path / "out.zip", root=proj)
    assert r["ok"]
    names = zipfile.ZipFile(r["zip"]).namelist()
    assert any(n.endswith("agents/a.py") for n in names)
    assert any(n.endswith(".env.example") for n in names)
    for bad in ("/.env", "memory_store.json", ".db", ".claude", "venv/"):
        assert not any(n.endswith(bad) or bad in n.replace(".env.example", "") for n in names), bad


def test_release_zip_aborts_when_key_hardcoded(tmp_path):
    from security.scan import build_release_zip
    proj = tmp_path / "proj"
    proj.mkdir()
    (proj / "leak.py").write_text(f'KEY = "{FAKE_ANTHROPIC}"\n', encoding="utf-8")
    r = build_release_zip(tmp_path / "out.zip", root=proj)
    assert not r["ok"]
    assert not (tmp_path / "out.zip").exists()


def test_project_source_has_no_hardcoded_keys():
    """실제 프로젝트 파일(.env 제외)에 키가 하드코딩되어 있지 않아야 한다."""
    from security.scan import scan_for_secrets
    danger = [f for f in scan_for_secrets(include_dotenv=False) if f.severity == "위험"]
    # 이 테스트 파일의 가짜 키는 런타임 조립이라 정규식에 안 걸린다
    assert danger == [], danger


def test_web_readonly_blocks_all_posts(web, monkeypatch):
    server, client, host = web
    from config import Config
    monkeypatch.setattr(Config, "WEB_READONLY", True)
    h = {"Host": host, "X-TA-Token": server.SESSION_TOKEN, "Origin": f"http://{host}"}
    assert client.post("/api/run", json={"mode": "real"}, headers=h).status_code == 403
    assert client.post("/api/run", json={"mode": "demo"}, headers=h).status_code == 403
    assert client.post("/api/collect", json={"stock_name": "삼성전자", "start": "2026-01-01",
                                              "end": "2026-01-10"}, headers=h).status_code == 403
    body = client.get("/", headers={"Host": host}).get_data(as_text=True)
    assert server.SESSION_TOKEN not in body
    assert client.get("/api/health", headers={"Host": host}).get_json()["readonly"] is True


def test_export_site_has_no_run_path(tmp_path):
    from web.export_site import render_fragment
    html = render_fragment({"generated_at": "2026-09-28T09:00", "analysis_at": None, "prediction": {},
                            "qa": {}, "stats": {}, "records": []})
    for bad in ("/api/run", "fetch(", "X-TA-Token", "<form"):
        assert bad not in html
