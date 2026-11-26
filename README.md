# TradingAgents-KR V3.6 (V3.1 + V3.5 병합본)

> 병합 내역은 `V3.6_MERGE_NOTES.md`, **API 키 설정·보안은 `SECURITY.md`** 를 먼저 보세요.

TauricResearch/TradingAgents의 멀티 에이전트 구조(분석가 → Bull/Bear 토론 루프 →
리스크 매니저 → 트레이더)를 참고해, 미국 증시 흐름으로 KOSPI를 예측하는
간소화된 버전입니다. 이전에 만든 HTML 대시보드 아티팩트와 같은 출력 스키마를
쓰기 때문에, 이 백엔드가 완성되면 그 프론트엔드를 거의 그대로 재사용할 수 있습니다.

## 아키텍처

"AI Multi-Agent Platform V3.5" 설계 원칙("판단이 필요한 것만 LLM, 계산 가능한 것은
전부 코드")을 반영한 구조입니다.

```
RuleEngine + MarketRegimeDetection (rules/engine.py, 코드)
      |
MarketAnalyst (LLM) -> HistoricalRetrieval/RAG (코드, TF-IDF)
      |
DebateLoop: Bull <-> Bear (근거 인용 필수, 방향 고정 없음, LLM)
      |
QA 검증팀 하위 — 토론 근거가 실제 자료에 있는지 대조 (LLM, 웹검색 없음)
      |
StrategyManager (코드, Bull/Bear 근거검증률 기반 가중치)
      |
RiskManager (LLM) -> 검증 ① 규칙엔진/과거사례와 상충 시에만 LLM 재판단
      |
Trader (LLM) -> 검증 ② 규칙 기반 충돌 검사 → 충돌 시에만 QA 상위 검증(LLM)
      |
MemoryStore (에이전트별 예측·근거검증률·QA 결과·종목별 검증 저장)
```

백테스트 시에는 여기에 더해 `execution/`(Portfolio Optimizer, Execution Simulator)과
`backtest/performance.py`(Performance Analyzer)가 결합되어, 예측 방향 적중률뿐 아니라
CAGR/MDD/Sharpe/Sortino/Alpha/Beta/Win Rate까지 계산합니다.

## 빠른 시작

```bash
pip install -r requirements.txt
cp .env.example .env          # LLM_PROVIDER 등 설정 (키는 적지 않음)
python cli.py set-key gemini  # API 키를 Windows 자격 증명 관리자에 저장 (권장)
python cli.py set-key dart    # OpenDART 인증키를 별도 항목에 저장 (숨김 입력)
python cli.py key-status      # 키 로드 확인 (마스킹 표시)

# 1) 네트워크 없이 Mock으로 파이프라인/토론 루프 동작만 확인
python cli.py demo

# 2) 테스트 실행
pytest tests/ -v

# 3) 실시간 분석 실행 (실행 시점 데이터 수집 + 실제 API 호출, 연속 갱신 아님)
python cli.py run

# 4) 지난 예측 검증
python cli.py verify

# 5) 구간 백테스트 (웹검색 없이, 그 시점 데이터만으로 판단)
python cli.py backtest 2026-06-01 2026-06-30
```

## 이번 단계에서 실제로 구현한 것

- **토론 루프**: Bull/Bear가 서로의 직전 발언을 보고 반박하며 N라운드 진행, confidence가 수렴하면 조기 종료
- **메모리 피드백 루프**: 과거 종목별/지표별/Bull-Bear 적중률을 다음 분석 프롬프트에 자동 주입
- **Look-ahead 방지**: 백테스트 시 조회 시점 이후 데이터가 섞이지 않도록 `backtest_mode`로 차단
- **근거 기반 토론 + QA 검증팀** (V3): Bull/Bear 방향 고정 제거, 인용 근거를 소스와 대조
- **종목별 실제 등락 검증 + 에이전트별 적중률/근거검증률 추적** (V2.1)
- **API 키 보안** (V3.6): 자격 증명 관리자 저장, SecretStr, 로그/에러 마스킹, 웹 CSRF·DNS 리바인딩 차단, 안전한 공유 zip
- **Mock/Real 클라이언트 분리**: 네트워크 없이도 전체 로직을 테스트 가능
- **복수 LLM 프로바이더 지원**: `.env`의 `LLM_PROVIDER`를 `anthropic` 또는 `gemini`로 설정해 전환 가능 (`agents/base.py`의 `AnthropicLLMClient` / `GeminiLLMClient`)
- **Feature Engineering** (`data/features.py`): RSI14/MACD/Bollinger %B를 미국 지수 종가로 순수 계산해 분석가에게 사실로 전달 (LLM이 재계산하지 않음)
- **Rule Engine + Market Regime Detection** (`rules/engine.py`): VIX 절대 수준/급등, S&P500 급락, 광범위 약세 등을 임계값으로 판정해 리스크 힌트와 장세(추세/변동성)를 코드로 산출
- **Verification Agent** (`agents/verification.py`): Risk Manager 판단이 규칙엔진·과거 유사사례와 상충하는지 코드로 먼저 검사하고, 상충이 있을 때만 LLM을 호출해 재판단 (비용 절감)
- **Strategy Manager** (`memory/strategy_manager.py`): 최근 Bull/Bear 방향 적중률로 가중치를 계산해 Risk Manager/Trader 프롬프트에 주입 (에이전트 자체는 건드리지 않고 가중치만 조정)
- **Portfolio Optimizer / Execution Simulator / Performance Analyzer** (`execution/`, `backtest/performance.py`): 백테스트 시 신뢰도·리스크 수준에 따라 현금/종목 비중을 계산하고, KOSPI 실제 등락에 대입해 CAGR/MDD/Sharpe/Sortino/Alpha/Beta/Win Rate까지 산출

## 의도적으로 남겨둔 다음 단계 (Phase 2+)

- 분석가를 fundamental/sentiment/technical로 세분화 (지금은 하나로 통합)
- 임베딩 기반 메모리(현재는 텍스트 요약 주입 방식 — 더 정교한 유사 사례 검색으로 발전 가능)
- 종목별 실제 등락 자동 검증은 V2.1에서 구현됨 (`verify`)
- Execution Simulator의 종목별 실제 체결 반영 (현재는 KOSPI 지수 등락률로 근사)
- 체크포인트 재개
- SQLite/Postgres로 저장소 교체 (지금은 JSON 파일)
- 스케줄러(cron)로 매일 자동 실행 + 기존 HTML 대시보드를 이 백엔드 API에 연결

## ⚠️ 참고

- 이 코드는 이 대화의 샌드박스에서 **Mock 데이터로 로직만 검증**했습니다. 실제
  시장 데이터 조회(yfinance)와 실제 Anthropic API 호출은 인터넷이 되는 로컬
  환경에서 직접 테스트해보셔야 합니다.
- 백테스트는 그 시점 실제 지표 수치는 정확히 재현하지만, LLM 에이전트 자체가
  "그 시점의 뉴스/맥락"까지 완벽히 재현하진 못한다는 한계가 있습니다.
- 투자 자문이 아닙니다. 모든 출력은 참고용 정보입니다.
