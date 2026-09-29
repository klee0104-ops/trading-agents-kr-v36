"""
전역 설정.

⚠️ 보안: API 키는 여기서 평문 문자열 속성으로 들고 있지 않는다.
   Config.api_key(provider)가 SecretStr을 돌려주며, 우선순위는
   (1) 시스템 환경변수 > (2) OS 자격 증명 저장소(keyring) > (3) .env 파일 이다.
   자세한 내용은 SECURITY.md 참고.

또한 .env의 "비밀이 아닌 설정값"(모델명, 라운드 수 등)만 os.environ에 올린다.
키까지 os.environ에 올리면 이 프로세스가 띄우는 모든 자식 프로세스에 키가
상속되기 때문이다.
"""
import os

# 반드시 .env를 읽기 "전에" import 해야 한다 — 진짜 시스템 환경변수 스냅샷을 먼저 뜬다.
from security.secret_store import (
    KEY_NAMES, DOTENV_PATH, SecretStr, get_api_key, _read_dotenv, install_global_redaction,
)

for _k, _v in _read_dotenv(DOTENV_PATH).items():
    if _k in KEY_NAMES.values():
        continue  # 비밀값은 os.environ에 올리지 않는다
    os.environ.setdefault(_k, _v)

install_global_redaction()


class Config:
    # 사용할 LLM 프로바이더: "anthropic" 또는 "gemini"
    LLM_PROVIDER = os.environ.get("LLM_PROVIDER", "anthropic").strip().lower()

    ANTHROPIC_MODEL = os.environ.get("ANTHROPIC_MODEL", "claude-sonnet-4-6")

    GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-flash-lite-latest")
    # Search grounding에 쿼터/결제가 필요한 계정이 있어 끌 수 있게 분리
    GEMINI_WEB_SEARCH_ENABLED = os.environ.get("GEMINI_WEB_SEARCH_ENABLED", "true").strip().lower() != "false"

    # 토론(Bull vs Bear) 최대 라운드 수. 너무 크면 토큰/시간 낭비이므로 기본값은 보수적으로.
    DEBATE_MAX_ROUNDS = int(os.environ.get("DEBATE_MAX_ROUNDS", "2"))

    # 메모리(과거 기록) 저장 위치
    MEMORY_DB_PATH = os.environ.get("MEMORY_DB_PATH", "./memory_store.json")
    LESSONS_PATH = os.environ.get("LESSONS_PATH", "./lessons.json")        # (V3.16) 복기 교훈
    PAPER_ACCOUNT_PATH = os.environ.get("PAPER_ACCOUNT_PATH", "./paper_account.json")  # (V3.21) 가상 계좌
    PAPER_INITIAL_CASH = float(os.environ.get("PAPER_INITIAL_CASH", "10000000"))       # 시작 자금(원)
    PAPER_PORTFOLIO_PATH = os.environ.get("PAPER_PORTFOLIO_PATH", os.path.join(os.path.dirname(PAPER_ACCOUNT_PATH), "paper_portfolio.json"))
    LONG_RESEARCH_PATH = os.environ.get("LONG_RESEARCH_PATH", os.path.join(os.path.dirname(PAPER_PORTFOLIO_PATH), "long_research.json"))
    REPORT_ARCHIVE_PATH = os.environ.get("REPORT_ARCHIVE_PATH", os.path.join(os.path.dirname(LONG_RESEARCH_PATH), "research_reports.json"))
    POSTMORTEM = os.environ.get("POSTMORTEM", "true").lower() in ("1", "true", "yes")  # 채점 후 복기 AI 호출
    CALIBRATE_PROBABILITY = os.environ.get("CALIBRATE_PROBABILITY", "true").lower() in ("1", "true", "yes")

    # 변동성 이벤트+뉴스 아카이브 저장 위치 (유사 사례 검색用)
    EVENT_ARCHIVE_DB_PATH = os.environ.get("EVENT_ARCHIVE_DB_PATH", "./event_archive.db")

    # 마지막 실제 분석 전체 결과 (대시보드가 재실행 없이 바로 보여주기 위함)
    LAST_OUTCOME_PATH = os.environ.get("LAST_OUTCOME_PATH", "./last_outcome.json")

    # 분석 대상 핵심 종목 (항상 포함)
    CORE_TICKERS = ["삼성전자", "SK하이닉스"]

    # 관심 종목 개수 (핵심 종목 제외)
    WATCH_STOCK_COUNT = 10

    # (V3.11) 관심 종목 선정 방식: stratified(기본: 시총 상위30 절반 + 31~100 절반 무작위) | random | topcap
    WATCH_PICK_MODE = os.environ.get("WATCH_PICK_MODE", "stratified").strip().lower()
    # 종목별 "보합" 판정: 실제 등락률 절대값이 이 값(%) 미만이면 보합 적중
    STOCK_FLAT_PCT = float(os.environ.get("STOCK_FLAT_PCT", "0.7"))

    # (V3.10) 뉴스·심리 분석가가 볼 종목: 핵심 2 + 오늘 추첨된 관심 종목 (같은 목록)
    NEWS_EXTRA_STOCKS = int(os.environ.get("NEWS_EXTRA_STOCKS", "10"))
    NEWS_PAGES_CORE = int(os.environ.get("NEWS_PAGES_CORE", "2"))     # 핵심 종목당 네이버 뉴스 페이지 수
    NEWS_PAGES_EXTRA = int(os.environ.get("NEWS_PAGES_EXTRA", "1"))   # 추가 종목당
    NEWS_MAX_TITLES = int(os.environ.get("NEWS_MAX_TITLES", "80"))     # 프롬프트에 넣는 제목 상한
    # 뉴스 출처: auto(네이버 검색 API 키 있으면 search → 없으면 rss → 실패 시 stock) | search | rss | stock
    NEWS_SOURCE = os.environ.get("NEWS_SOURCE", "auto").strip().lower()
    NEWS_SEARCH_DISPLAY = int(os.environ.get("NEWS_SEARCH_DISPLAY", "40"))  # 검색 API 종목당 요청 건수(최대 100)
    # hub = 네이버 클라우드 NAVER API HUB 키(2026-08 이후 신규 발급은 이쪽) | legacy = 구 개발자센터 키(2027-06-30까지)
    NAVER_SEARCH_MODE = os.environ.get("NAVER_SEARCH_MODE", "hub").strip().lower()

    # 웹 대시보드 (로컬 전용)
    WEB_HOST = "127.0.0.1"   # 외부 노출 금지 — 바꾸지 말 것
    WEB_PORT = int(os.environ.get("WEB_PORT", "5000"))
    # 보기 전용 모드: true면 실제 분석/데모 실행/뉴스 수집(POST)을 서버에서 전부 거부한다.
    # 다른 사람에게 화면을 보여줄 때 사용 (대시보드_보기전용.bat).
    WEB_READONLY = os.environ.get("WEB_READONLY", "false").strip().lower() == "true"

    @staticmethod
    def api_key(provider: str | None = None) -> SecretStr:
        """현재(또는 지정한) provider의 API 키를 SecretStr로 반환. 없으면 빈 SecretStr."""
        return get_api_key(provider or Config.LLM_PROVIDER)

    @staticmethod
    def required_key_name(provider: str | None = None) -> str:
        return KEY_NAMES.get((provider or Config.LLM_PROVIDER), "ANTHROPIC_API_KEY")
