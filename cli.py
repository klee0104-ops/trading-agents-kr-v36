"""
CLI 엔트리 포인트.

분석:
  python cli.py run                # 오늘 분석 실행 (실제 LLM API + 실시간 데이터 필요)
  python cli.py verify             # 지난 예측들 검증 (KOSPI + 종목별)
  python cli.py backtest 2026-06-01 2026-06-30   # 구간 백테스트 (화면 출력만)
  python cli.py backtest-save 2026-08-28 2026-09-28 [저장파일] [라벨]
                                   # 저장형 백테스트 (끊겨도 이어서 진행). 저장파일을 바꾸면 버전 비교 가능
  python cli.py demo               # Mock으로 전체 파이프라인 동작만 확인 (네트워크 불필요)
  python cli.py collect 삼성전자 2026-06-01 2026-06-30 [임계값%] [뉴스페이지수]
                                   # 변동성 이벤트+뉴스 수집 (LLM 미사용, 토큰 소모 없음)

API 키 보안 (SECURITY.md 참고):
  python cli.py set-key gemini|anthropic   # 키를 Windows 자격 증명 관리자에 저장 (입력 화면에 안 보임)
  python cli.py key-status                 # 키가 어디서 로드되는지 확인 (값은 마스킹)
  python cli.py migrate-keys               # .env의 평문 키를 자격 증명 관리자로 옮기고 .env에서 삭제
  python cli.py delete-key gemini|anthropic
  python cli.py check-secrets              # 프로젝트 파일에 키가 새어 있는지 검사
  python cli.py package                    # .env/DB/기록을 뺀 공유용 zip 생성 (release/ 폴더)

공유:
  python cli.py export-site                # 읽기 전용 스냅샷 페이지 생성 (site/index.html, 실행 버튼 없음)
"""
import sys
import json
from datetime import date

from config import Config
from agents.base import MockLLMClient, build_llm_client
from data.market_data import MarketDataProvider
from memory.store import MemoryStore
from graph.pipeline import TradingAgentsPipeline
from backtest.runner import run_backtest, summarize


def _real_llm():
    """Config.LLM_PROVIDER 설정에 따라 Anthropic 또는 Gemini 클라이언트를 만든다.
    키는 SecretStr 그대로 넘겨서, 평문은 SDK 클라이언트 생성 시점에만 꺼내진다."""
    provider = Config.LLM_PROVIDER
    return build_llm_client(
        provider=provider,
        anthropic_key=Config.api_key("anthropic") if provider == "anthropic" else "",
        anthropic_model=Config.ANTHROPIC_MODEL,
        gemini_key=Config.api_key("gemini") if provider == "gemini" else "",
        gemini_model=Config.GEMINI_MODEL,
        gemini_web_search_enabled=Config.GEMINI_WEB_SEARCH_ENABLED,
    )


def cmd_market_day() -> int:
    """(V3.29) 예약 아침 분석용: 오늘이 KRX 거래일이면 0, 주말·휴장일이면 2 (배치가 분석을 건너뛴다).
    달력이 없는 해면 경고만 출력하고 0 (평일=거래일로 간주)."""
    from data.sessions import kst_now
    from data.krx_calendar import holiday_name, calendar_known, is_trading_day
    today = kst_now().date()
    if not calendar_known(today):
        print(f"[경고] {today.year}년 KRX 휴장일 달력이 없습니다 (data/krx_calendar.py). 평일은 거래일로 간주합니다.")
    if is_trading_day(today):
        print(f"[거래일] {today}")
        return 0
    print(f"[휴장] {today} {holiday_name(today) or '주말'} — 아침 분석을 건너뜁니다 (AI 호출 없음).")
    return 2


def cmd_run():
    memory = MemoryStore(Config.MEMORY_DB_PATH)
    pipeline = TradingAgentsPipeline(_real_llm(), memory, debate_max_rounds=Config.DEBATE_MAX_ROUNDS)
    outcome = pipeline.run()
    from memory.outcome_store import save_last_outcome
    save_last_outcome(outcome, Config.LAST_OUTCOME_PATH)  # 대시보드에서 바로 보이도록
    print(json.dumps(outcome["final_result"], ensure_ascii=False, indent=2))
    print("\n=== QA/검증 요약 ===")
    print(json.dumps({"verification": outcome["verification"], "final_qa": outcome["final_qa"]},
                     ensure_ascii=False, indent=2))


def cmd_verify():
    memory = MemoryStore(Config.MEMORY_DB_PATH)
    # 채점 자체는 LLM을 쓰지 않는다. (V3.16) 복기(틀린 날 AI 1회)를 위해 키가 있으면 실제 LLM, 없으면 Mock.
    try:
        llm = _real_llm() if Config.POSTMORTEM and Config.api_key() else MockLLMClient()
    except Exception:  # noqa: BLE001
        llm = MockLLMClient()
    pipeline = TradingAgentsPipeline(llm, memory)
    pipeline.verify_pending()
    print(json.dumps(memory.compute_stats(), ensure_ascii=False, indent=2))


def cmd_backtest(start_str: str, end_str: str):
    memory = MemoryStore(None)  # 백테스트는 실거래 기록과 분리 (인메모리)
    pipeline = TradingAgentsPipeline(_real_llm(), memory, backtest_mode=True)
    results = run_backtest(pipeline, date.fromisoformat(start_str), date.fromisoformat(end_str))
    print(json.dumps(summarize(results), ensure_ascii=False, indent=2))


def cmd_backtest_save(start_str: str, end_str: str, out_path: str = "./backtest_results.json",
                      run_label: str = ""):
    """저장형 백테스트. out_path를 다르게 주면 이전 결과를 보존한 채 새 버전과 비교할 수 있다."""
    from backtest.persist import run_backtest_persist
    memory = MemoryStore(None)  # 운영 기록과 분리
    pipeline = TradingAgentsPipeline(_real_llm(), memory, backtest_mode=True,
                                     debate_max_rounds=Config.DEBATE_MAX_ROUNDS)
    provider = MarketDataProvider(backtest_mode=True)
    label = Config.GEMINI_MODEL if Config.LLM_PROVIDER == "gemini" else Config.ANTHROPIC_MODEL
    log_path = out_path.replace(".json", "_log.txt")
    s = run_backtest_persist(pipeline, provider, date.fromisoformat(start_str), date.fromisoformat(end_str),
                             path=out_path, log_path=log_path,
                             model_label=f"{Config.LLM_PROVIDER}:{label}", run_label=run_label)
    print(json.dumps(s, ensure_ascii=False, indent=2))


def cmd_demo():
    """네트워크 없이 Mock으로 파이프라인 전체(토론 + QA + 하이브리드 검증)가 도는지 확인."""
    memory = MemoryStore("./demo_memory.json")
    pipeline = TradingAgentsPipeline(MockLLMClient(), memory, debate_max_rounds=3,
                                      data_provider=_DemoDataProvider())
    outcome = pipeline.run()
    print("=== 규칙엔진 / 장세 ===")
    print(json.dumps(outcome["market_analysis"], ensure_ascii=False, indent=2))
    print("\n=== 토론 트랜스크립트 (근거 인용 포함) ===")
    for turn in outcome["debate_transcript"]:
        ev = ", ".join(turn.get("evidence", []))
        print(f"  round {turn['round']} [{turn['stance']}] {turn['argument']} "
              f"(conf={turn.get('confidence')}) | 근거: {ev}")
    print("\n=== QA 검증 (하위: 토론 근거 대조) ===")
    print(json.dumps(outcome["debate_qa"], ensure_ascii=False, indent=2))
    print("\n=== Strategy Manager 가중치 ===")
    print(json.dumps(outcome["strategy_weights"], ensure_ascii=False, indent=2))
    print("\n=== 리스크 매니저 ===")
    print(json.dumps(outcome["risk_assessment"], ensure_ascii=False, indent=2))
    print("\n=== 검증 ① (리스크 단계) ===")
    print(json.dumps(outcome["verification"], ensure_ascii=False, indent=2))
    print("\n=== 최종 결과 ===")
    print(json.dumps(outcome["final_result"], ensure_ascii=False, indent=2))
    print("\n=== 검증 ② (최종 단계: 규칙 → 충돌 시 QA 상위) ===")
    print(json.dumps(outcome["final_qa"], ensure_ascii=False, indent=2))


def cmd_collect(stock_name: str, start_str: str, end_str: str,
                 threshold_str: str = "3.0", news_max_pages_str: str = "20"):
    """변동성 이벤트 + 관련 뉴스를 수집해 event_archive.db에 저장한다 (LLM 미사용)."""
    from memory.event_archive import collect_and_store
    result = collect_and_store(
        stock_name,
        date.fromisoformat(start_str),
        date.fromisoformat(end_str),
        threshold_pct=float(threshold_str),
        news_max_pages=int(news_max_pages_str),
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))


# ------------------------------------------------------------------ 보안 명령
def _provider_arg(args) -> str:
    p = (args[0] if args else Config.LLM_PROVIDER).strip().lower()
    from security.secret_store import KEY_NAMES
    if p not in KEY_NAMES:
        print(f"provider는 {', '.join(KEY_NAMES)} 중 하나여야 합니다. (naver_id/naver_secret = 네이버 검색 API)")
        sys.exit(1)
    return p


def cmd_set_key(args):
    import getpass
    from security.secret_store import store_in_keyring, keyring_available, key_status, mask
    provider = _provider_arg(args)
    if not keyring_available():
        print("[중단] OS 자격 증명 저장소를 사용할 수 없습니다. `pip install keyring` 후 다시 시도하세요.")
        sys.exit(1)
    # getpass: 입력한 키가 화면·명령 기록(history)에 남지 않는다
    value = getpass.getpass(f"{provider} API 키를 붙여넣고 Enter (화면에 표시되지 않음): ").strip()
    try:
        store_in_keyring(provider, value)
    except ValueError as e:
        print(f"[실패] {e}")
        sys.exit(1)
    st = key_status(provider)
    print(f"[완료] {st.env_name} 저장됨 → Windows 자격 증명 관리자 (값: {mask(value)})")
    if st.source == "environment":
        print("  ※ 시스템 환경변수에 같은 이름의 키가 있어서 그쪽이 우선 사용됩니다.")
    if st.dotenv_has_plaintext:
        print("  ※ .env 파일에도 평문 키가 남아있습니다. `python cli.py migrate-keys`로 지우세요.")


def cmd_delete_key(args):
    from security.secret_store import delete_from_keyring
    provider = _provider_arg(args)
    ok = delete_from_keyring(provider)
    print("[완료] 삭제했습니다." if ok else "[안내] 자격 증명 저장소에 해당 키가 없습니다.")


def cmd_key_status():
    from security.secret_store import key_status, keyring_available
    print(f"현재 LLM_PROVIDER = {Config.LLM_PROVIDER}")
    print(f"OS 자격 증명 저장소 사용 가능: {'예' if keyring_available() else '아니오 (keyring 미설치 또는 이 OS에 저장소 없음)'}")
    for p in ("anthropic", "gemini"):
        st = key_status(p)
        src = {"environment": "시스템 환경변수", "keyring": "자격 증명 관리자(권장)",
               ".env": ".env 파일(평문, 비권장)", "none": "없음"}[st.source]
        line = f"- {st.env_name}: {'있음' if st.found else '없음'} | 출처: {src}"
        if st.found:
            line += f" | {st.masked}"
        print(line)
        if st.dotenv_has_plaintext:
            print(f"    ⚠ .env에 {st.env_name} 평문이 있습니다 → python cli.py migrate-keys")


def cmd_migrate_keys():
    from security.secret_store import (_read_dotenv, is_placeholder, store_in_keyring,
                                        remove_key_from_dotenv, keyring_available, KEY_NAMES, mask)
    if not keyring_available():
        print("[중단] OS 자격 증명 저장소를 사용할 수 없습니다. `pip install keyring` 후 다시 시도하세요.")
        sys.exit(1)
    env = _read_dotenv()
    moved = 0
    for provider, name in KEY_NAMES.items():
        v = env.get(name)
        if v and not is_placeholder(v):
            store_in_keyring(provider, v)
            remove_key_from_dotenv(provider)
            print(f"[이동] {name} ({mask(v)}) → 자격 증명 관리자, .env에서 삭제")
            moved += 1
    print(f"완료: {moved}개 이동." if moved else ".env에 옮길 평문 키가 없습니다.")


def cmd_check_secrets():
    from security.scan import scan_for_secrets
    findings = scan_for_secrets()
    danger = [f for f in findings if f.severity == "위험"]
    warn = [f for f in findings if f.severity == "경고"]
    for f in danger:
        print(f"[위험] {f.path}:{f.line}  {f.masked}  ← 코드/문서에 키가 들어있습니다. 삭제 후 키를 재발급하세요.")
    for f in warn:
        print(f"[경고] {f.path}:{f.line}  {f.masked}  ← .env 평문 키. migrate-keys 권장")
    if not findings:
        print("[통과] 프로젝트 파일에서 API 키가 발견되지 않았습니다.")
    sys.exit(1 if danger else 0)


def cmd_package():
    from pathlib import Path
    from security.scan import build_release_zip
    from security.secret_store import PROJECT_ROOT
    out = PROJECT_ROOT / "release" / f"{PROJECT_ROOT.name}_{date.today():%Y%m%d}.zip"
    r = build_release_zip(out)
    if r["ok"]:
        print(f"[완료] {r['zip']} (파일 {r['files']}개, 제외 {r['skipped']}개 — .env/DB/기록/venv 등)")
    else:
        print("[중단] 결과 zip에서 키로 보이는 값이 발견되어 zip을 삭제했습니다:")
        for f in r["leaks"]:
            print(f"  - {f['path']}:{f['line']} {f['masked']}")
        sys.exit(1)


def cmd_export_site():
    from web.export_site import export_site
    r = export_site()
    from web.publish_site import publish_if_enabled
    published = publish_if_enabled(r['index'])
    if published:
        print(f"[공유 페이지 업로드 완료] {published['url']} (게시 반영에 수 분 걸릴 수 있습니다.)")
    print(f"[완료] 읽기 전용 스냅샷: {r['index']}")
    print("  이 파일은 실행 버튼도 서버 연결도 없어서, 누가 열어도 분석(API 과금)을 실행할 수 없습니다.")


class _DemoDataProvider(MarketDataProvider):
    """데모용: 실제 네트워크 호출 없이 고정 스냅샷을 반환."""
    def fetch_us_snapshot(self, as_of_date=None):
        from data.market_data import MarketSnapshot, IndexQuote
        return MarketSnapshot(
            as_of=str(date.today()),
            us_indices=[
                IndexQuote("S&P500", 0.4, "up", str(date.today())),
                IndexQuote("SOX", 1.1, "up", str(date.today())),
                IndexQuote("VIX", 2.0, "up", str(date.today())),
            ],
            macro=[
                IndexQuote("원/달러", -0.35, "down", str(date.today()), value=1372.5),
                IndexQuote("미국10년금리", 0.8, "up", str(date.today()), value=4.21),
                IndexQuote("WTI유가", -1.2, "down", str(date.today()), value=76.4),
                IndexQuote("달러인덱스", -0.2, "down", str(date.today()), value=103.1),
            ],
            kr_flow={"date": str(date.today()), "foreign_net_krw_bn": 1850.0, "direction": "buy",
                     "strong": False, "source": "demo"},
        )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)

    cmd, rest = sys.argv[1], sys.argv[2:]
    if cmd == "market-day":
        sys.exit(cmd_market_day())
    elif cmd == "run":
        cmd_run()
    elif cmd == "verify":
        cmd_verify()
    elif cmd == "review-now":
        memory = MemoryStore(Config.MEMORY_DB_PATH)
        try:
            llm = _real_llm() if Config.api_key() else MockLLMClient()
        except Exception:  # noqa: BLE001
            llm = MockLLMClient()
        r = TradingAgentsPipeline(llm, memory).review_now(sys.argv[2] if len(sys.argv) > 2 else None)
        if r:
            print(json.dumps({k: r[k] for k in ("lesson", "cause", "misleading_evidence", "ignored_signals", "luck", "confidence_note", "note")},
                             ensure_ascii=False, indent=2))
    elif cmd == "backtest":
        cmd_backtest(rest[0], rest[1])
    elif cmd == "backtest-save":
        cmd_backtest_save(rest[0], rest[1], *(rest[2:4]))
    elif cmd == "demo":
        cmd_demo()
    elif cmd == "collect":
        if len(rest) < 3:
            print(__doc__)
            sys.exit(1)
        threshold = rest[3] if len(rest) > 3 else "3.0"
        news_max_pages = rest[4] if len(rest) > 4 else "20"
        cmd_collect(rest[0], rest[1], rest[2], threshold, news_max_pages)
    elif cmd == "set-key":
        cmd_set_key(rest)
    elif cmd == "delete-key":
        cmd_delete_key(rest)
    elif cmd == "key-status":
        cmd_key_status()
    elif cmd == "migrate-keys":
        cmd_migrate_keys()
    elif cmd == "check-secrets":
        cmd_check_secrets()
    elif cmd == "package":
        cmd_package()
    elif cmd == "export-site":
        cmd_export_site()
    else:
        print(__doc__)
        sys.exit(1)
