"""(V3.33) 외부로 나가는 정보 점검: 공개 커밋 이메일, AI 입력 개인정보, 키움 키, 공개본 개인정보."""
import pytest

from security.public_report import check_data, check_text, PublicationBlocked


def test_outbound_ai_prompt_scrubs_personal_and_account_data_but_keeps_market_numbers():
    from agents.base import scrub_outbound
    t = scrub_outbound("메일 someone@gmail.com 계좌 12345678-01 전화 010-1234-5678 경로 C:\\Users\\aica_\\Desktop "
                       "KOSPI 5720.16 -0.27% 외국인 6,462.0억원 2026-10-22")
    for bad in ("someone@gmail.com", "12345678-01", "010-1234-5678", "aica_"):
        assert bad not in t
    for keep in ("5720.16", "-0.27%", "6,462.0억원", "2026-10-22"):
        assert keep in t


def test_llm_clients_scrub_before_sending():
    from pathlib import Path
    src = (Path(__file__).resolve().parents[1] / "agents/base.py").read_text(encoding="utf-8")
    assert src.count("system, user = scrub_outbound(system), scrub_outbound(user)") == 2


@pytest.mark.parametrize("text", ["연락 someone@gmail.com", "C:\\\\Users\\\\aica_\\\\x", "계좌 12345678-01",
                                  "KIWOOM_APP_KEY=" + "abcdEFGH12" * 2])  # 스캐너가 소스의 가짜 키도 잡으므로 실행 시 조립
def test_public_report_blocks_personal_data(text):
    with pytest.raises(PublicationBlocked):
        check_text(text)


def test_public_report_allows_press_emails_and_dates():
    check_text("기자 reporter@yna.co.kr 2026-10-22 09:51 KOSPI 5720.16")


@pytest.mark.parametrize("field", ["account_no", "cano", "kiwoom_balance", "계좌번호", "app_key"])
def test_public_report_blocks_account_fields(field):
    with pytest.raises(PublicationBlocked):
        check_data({field: "x"}, [])


def test_canonical_like_fields_not_blocked():
    check_data({"canonical": "x", "acntless": 1}, [])


def test_kiwoom_keys_are_registered_secrets():
    from security.secret_store import KEY_NAMES
    assert KEY_NAMES["kiwoom_appkey"] == "KIWOOM_APP_KEY" and KEY_NAMES["kiwoom_secret"] == "KIWOOM_SECRET_KEY"


def test_publisher_uses_noreply_identity_and_orphan_commit():
    from web import publish_site as p
    assert p.PUBLISH_IDENTITY["email"].endswith("@users.noreply.github.com")
    src = open(p.__file__, encoding="utf-8").read()
    assert "'parents': []" in src and "'force': True" in src and "contents/index.html', 'PUT'" not in src
