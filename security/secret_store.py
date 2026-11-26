"""
API 키 로딩/보관/마스킹.

원칙
1. 키는 코드·로그·에러 메시지·웹 응답 어디에도 평문으로 나오지 않는다.
   -> SecretStr로 감싸서 print/repr/f-string으로는 마스킹된 값만 보이게 한다.
      실제 값은 SDK에 넘기는 순간에만 .reveal()로 꺼낸다.
2. 키를 찾는 우선순위 (먼저 찾은 것을 사용):
     (1) 프로세스 환경변수  — 사용자가 셸/시스템에 직접 설정한 값
     (2) OS 자격 증명 저장소 — Windows 자격 증명 관리자 / macOS 키체인 (keyring)
     (3) 프로젝트의 .env 파일 — 가장 덜 안전. 호환성 때문에만 남겨둔다.
   권장 방식은 (2): `python cli.py set-key gemini` 로 한 번 등록하면 .env에
   평문 키를 둘 필요가 없다.
3. ".env.example"에 들어있는 자리표시자(sk-ant-..., AIza... 등)는 "키 없음"으로 본다.
"""
from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# 이 앱이 OS 자격 증명 저장소에 키를 저장할 때 쓰는 서비스 이름
KEYRING_SERVICE = "TradingAgents-KR"

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DOTENV_PATH = PROJECT_ROOT / ".env"

# provider -> 환경변수 이름
KEY_NAMES = {
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "dart": "DART_API_KEY",
    "alpaca_id": "APCA_API_KEY_ID",
    "alpaca_secret": "APCA_API_SECRET_KEY",
    # 네이버 개발자센터 검색 API (뉴스). 둘 다 있어야 공식 API를 쓴다.
    "naver_id": "NAVER_CLIENT_ID",
    "naver_secret": "NAVER_CLIENT_SECRET",
    # (V3.33) 키움증권 REST API — 연결 전 미리 등록: 저장은 자격 증명 관리자, 로그·공개본·AI 입력에서 자동 가림
    "kiwoom_appkey": "KIWOOM_APP_KEY",
    "kiwoom_secret": "KIWOOM_SECRET_KEY",
    # (V3.36) 레딧 무료(비상업) API — reddit.com/prefs/apps 에서 script 앱 생성 후 저장. 없으면 레딧만 건너뜀
    "reddit_id": "REDDIT_CLIENT_ID",
    "reddit_secret": "REDDIT_CLIENT_SECRET",
}

# 알려진 API 키 형태. 로그/에러 메시지 마스킹과 비밀값 스캔에 공통으로 쓴다.
SECRET_PATTERNS = [
    re.compile(r"sk-ant-[A-Za-z0-9_\-]{16,}"),          # Anthropic
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),              # Google API key (구형)
    re.compile(r"AQ\.[0-9A-Za-z_\-\.]{20,}"),            # Google AI Studio 신형 키
    re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{32,}"),     # OpenAI 형태 (혹시 섞여 들어올 때 대비)
    # 네이버 검색 API 키는 형태가 짧고 일반적이라, 변수명과 함께 적힌 값만 잡는다
    re.compile(r"NAVER_CLIENT_(?:ID|SECRET)\s*[=:]\s*['\"]?([A-Za-z0-9_\-]{8,})"),
    # OpenDART 키는 일반적인 40자리 문자열이므로 키 이름/쿼리 인자와 함께 있을 때만 탐지한다.
    re.compile(r"(?:DART_API_KEY|crtfc_key)\s*[=:]\s*['\"]?([A-Za-z0-9]{40})", re.I),
    re.compile(r"(?:APCA_API_KEY_ID|APCA_API_SECRET_KEY)\s*[=:]\s*['\"]?([A-Za-z0-9_\-]{20,})", re.I),
    re.compile(r"(?:KIWOOM_APP_KEY|KIWOOM_SECRET_KEY|appkey|secretkey)\s*[=:\"']+\s*['\"]?([A-Za-z0-9_\-]{16,})", re.I),
]

# 예시/자리표시자 값: "sk-ant-...", "AIza...", "your-key-here", "<YOUR_KEY>", "여기에 키 입력" 등
_PLACEHOLDER_RE = re.compile(r"(\.\.\.$|\byour[_\- ]?(api[_\- ]?)?key|^<.*>$|changeme|여기에)", re.IGNORECASE)
_MIN_KEY_LEN = 20


class SecretStr:
    """평문이 우발적으로 출력되지 않도록 감싼 문자열."""

    __slots__ = ("_value", "source")

    def __init__(self, value: str, source: str = "unknown"):
        self._value = value
        self.source = source

    def reveal(self) -> str:
        """SDK 클라이언트를 만들 때만 호출할 것. 로그/출력에 절대 쓰지 말 것."""
        return self._value

    def masked(self) -> str:
        return mask(self._value)

    def __bool__(self) -> bool:
        return bool(self._value)

    def __len__(self) -> int:
        return len(self._value)

    def __repr__(self) -> str:
        return f"SecretStr('{self.masked()}', source={self.source!r})"

    __str__ = masked

    def __format__(self, spec: str) -> str:
        return self.masked()

    def __eq__(self, other) -> bool:  # 상수시간 비교
        import hmac
        o = other._value if isinstance(other, SecretStr) else other
        return isinstance(o, str) and hmac.compare_digest(self._value, o)

    def __hash__(self):
        return hash(("SecretStr", self.masked()))

    def __reduce__(self):
        # pickle/직렬화로 평문이 새어나가는 것을 막는다
        raise TypeError("SecretStr은 직렬화할 수 없습니다")


def mask(value: Optional[str]) -> str:
    """앞 몇 글자와 끝 4글자만 남기고 가린다. 짧은 값은 전부 가린다."""
    if not value:
        return "(없음)"
    if len(value) < 12:
        return "****"
    head = value[:4] if not value.startswith("sk-ant-") else "sk-ant-"
    return f"{head}…{value[-4:]}"


_SHORT_ID_PROVIDERS = {"reddit_id": 10}  # (V3.36) 레딧 앱 ID 는 14~22자라 일반 최소 길이(20)보다 짧을 수 있다


def is_placeholder(value: Optional[str], min_len: int = _MIN_KEY_LEN) -> bool:
    if not value:
        return True
    v = value.strip()
    return len(v) < min_len or bool(_PLACEHOLDER_RE.search(v))


# ---------------------------------------------------------------- .env 읽기
def _read_dotenv(path: Path = DOTENV_PATH) -> dict:
    """os.environ을 건드리지 않고 .env 값만 읽는다 (우선순위를 직접 통제하기 위함)."""
    if not path.exists():
        return {}
    try:
        from dotenv import dotenv_values
        return {k: v for k, v in dotenv_values(path).items() if v is not None}
    except ImportError:
        values = {}
        for line in path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            values[k.strip()] = v.strip().strip('"').strip("'")
        return values


# ---------------------------------------------------------------- keyring
def keyring_available() -> bool:
    try:
        import keyring
        from keyring.backends import fail
        return not isinstance(keyring.get_keyring(), fail.Keyring)
    except Exception:
        return False


def _keyring_get(name: str) -> Optional[str]:
    if not keyring_available():
        return None
    try:
        import keyring
        return keyring.get_password(KEYRING_SERVICE, name)
    except Exception:
        return None


def store_in_keyring(provider: str, value: str) -> None:
    name = _key_name(provider)
    if is_placeholder(value, _SHORT_ID_PROVIDERS.get(provider.strip().lower(), _MIN_KEY_LEN)):
        raise ValueError("유효한 API 키 형태가 아닙니다 (너무 짧거나 예시 값입니다).")
    if provider.strip().lower() == "dart" and not re.fullmatch(r"[A-Za-z0-9]{40}", value.strip()):
        raise ValueError("OpenDART 인증키는 영문·숫자 40자리여야 합니다.")
    if not keyring_available():
        raise RuntimeError(
            "OS 자격 증명 저장소를 사용할 수 없습니다. `pip install keyring` 후 다시 시도하거나, "
            "시스템 환경변수로 키를 설정하세요."
        )
    import keyring
    keyring.set_password(KEYRING_SERVICE, name, value.strip())
    register_runtime_secret(value.strip())


def delete_from_keyring(provider: str) -> bool:
    name = _key_name(provider)
    if not keyring_available():
        return False
    import keyring
    try:
        keyring.delete_password(KEYRING_SERVICE, name)
        return True
    except Exception:
        return False


# ---------------------------------------------------------------- 조회
@dataclass
class KeyStatus:
    provider: str
    env_name: str
    found: bool
    source: str          # "environment" | "keyring" | ".env" | "none"
    masked: str
    dotenv_has_plaintext: bool  # .env에 평문 키가 남아있는지 (경고용)


def _key_name(provider: str) -> str:
    p = (provider or "").strip().lower()
    if p not in KEY_NAMES:
        raise ValueError(f"지원하지 않는 provider입니다: {provider} ({', '.join(KEY_NAMES)})")
    return KEY_NAMES[p]


# load_dotenv() 이전의 "진짜" 프로세스 환경변수 스냅샷. config.py가 import 시점에
# 이 모듈을 먼저 불러서, .env가 os.environ에 섞이기 전 상태를 기록해 둔다.
_PROCESS_ENV_SNAPSHOT = dict(os.environ)


def get_api_key(provider: str) -> SecretStr:
    """우선순위(환경변수 > OS 자격 증명 저장소 > .env)에 따라 키를 찾는다. 없으면 빈 SecretStr."""
    name = _key_name(provider)
    min_len = _SHORT_ID_PROVIDERS.get(provider.strip().lower(), _MIN_KEY_LEN)

    v = _PROCESS_ENV_SNAPSHOT.get(name)
    if v and not is_placeholder(v, min_len):
        register_runtime_secret(v)
        return SecretStr(v.strip(), "environment")

    v = _keyring_get(name)
    if v and not is_placeholder(v, min_len):
        register_runtime_secret(v)
        return SecretStr(v.strip(), "keyring")

    v = _read_dotenv().get(name)
    if v and not is_placeholder(v, min_len):
        register_runtime_secret(v)
        return SecretStr(v.strip(), ".env")

    return SecretStr("", "none")


def key_status(provider: str) -> KeyStatus:
    name = _key_name(provider)
    key = get_api_key(provider)
    dotenv_val = _read_dotenv().get(name)
    return KeyStatus(
        provider=provider, env_name=name, found=bool(key), source=key.source,
        masked=key.masked() if key else "(없음)",
        dotenv_has_plaintext=bool(dotenv_val) and not is_placeholder(dotenv_val),
    )


def remove_key_from_dotenv(provider: str, path: Path = DOTENV_PATH) -> bool:
    """.env에서 해당 키 줄의 값을 비운다 (줄 자체는 남겨서 설정 구조는 유지)."""
    name = _key_name(provider)
    if not path.exists():
        return False
    lines = path.read_text(encoding="utf-8").splitlines()
    changed = False
    out = []
    for line in lines:
        if line.strip().startswith(f"{name}="):
            out.append(f"{name}=")
            changed = True
        else:
            out.append(line)
    if changed:
        path.write_text("\n".join(out) + "\n", encoding="utf-8")
    return changed


# ---------------------------------------------------------------- 마스킹(redaction)
_RUNTIME_SECRETS: set[str] = set()


def register_runtime_secret(value: str) -> None:
    """패턴에 안 걸리는 형태의 키도 로그/에러에서 가릴 수 있도록 실제 값을 등록해 둔다."""
    if value and len(value) >= 8:
        _RUNTIME_SECRETS.add(value.strip())


def redact(text) -> str:
    """문자열에서 API 키로 보이는 부분을 전부 마스킹한다."""
    if text is None:
        return ""
    s = str(text)
    for secret in sorted(_RUNTIME_SECRETS, key=len, reverse=True):
        if secret in s:
            s = s.replace(secret, mask(secret))
    for pat in SECRET_PATTERNS:
        s = pat.sub(lambda m: mask(m.group(0)), s)
    # URL 쿼리스트링의 key=... 형태 (Google API가 쓰는 방식)
    s = re.sub(r"([?&](?:key|api_key|apikey|crtfc_key)=)[^&\s\"']+", r"\1****", s, flags=re.IGNORECASE)
    return s


class RedactingFilter:
    """logging.Filter 호환 객체 — 모든 로그 레코드에서 키를 가린다."""

    def filter(self, record) -> bool:
        try:
            record.msg = redact(record.getMessage())
            record.args = ()
        except Exception:
            pass
        return True


_INSTALLED = False


def install_global_redaction() -> None:
    """
    프로세스 전체에 마스킹을 건다.
    - logging: 루트/모든 핸들러에 RedactingFilter 부착
    - 처리되지 않은 예외: traceback 출력 전에 마스킹 (SDK 예외 메시지에 요청 정보가
      섞여 나오는 경우 대비)
    """
    global _INSTALLED
    if _INSTALLED:
        return
    _INSTALLED = True

    import logging
    import sys
    import traceback

    flt = RedactingFilter()
    root = logging.getLogger()
    root.addFilter(flt)
    for h in root.handlers:
        h.addFilter(flt)
    # httpx/urllib3 등 SDK 하위 라이브러리의 DEBUG 로그는 요청 헤더를 찍을 수 있어 끈다
    for noisy in ("httpx", "httpcore", "urllib3", "anthropic", "google_genai", "google.genai"):
        lg = logging.getLogger(noisy)
        lg.addFilter(flt)
        if lg.level == logging.NOTSET or lg.level < logging.WARNING:
            lg.setLevel(logging.WARNING)

    def _excepthook(exc_type, exc, tb):
        text = "".join(traceback.format_exception(exc_type, exc, tb))
        sys.stderr.write(redact(text))

    sys.excepthook = _excepthook
