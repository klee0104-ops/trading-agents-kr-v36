# V2 변경사항 — 종목별 실검증 루프

이 버전은 원본 프로젝트 전체를 보존한 상태에서 **종목별 실제 검증 기능**을 추가한 버전이다.

## 핵심 흐름

```text
Trader
  ↓
종목별 예측 저장
  ├─ 종목명
  ├─ KRX 6자리 티커
  ├─ 예측 대상일
  └─ 방향성(up/down)
        ↓
python cli.py verify
        ↓
실제 가격 조회
        ↓
종목별 검증
  ├─ 전일 종가
  ├─ 실제 종가
  ├─ 실제 수익률(%)
  ├─ 실제 방향
  └─ 적중 여부
        ↓
MemoryStore
        ↓
종목별 적중률/평균 수익률
```

## 실행

```bash
python cli.py run
```

다음 거래일 데이터가 생성된 후:

```bash
python cli.py verify
```

## 저장되는 종목별 검증 데이터 예시

```json
{
  "name": "삼성전자",
  "ticker": "005930",
  "predicted_date": "2026-07-29",
  "actual_date": "2026-07-29",
  "previous_close": 100000.0,
  "actual_close": 102000.0,
  "actual_return_pct": 2.0,
  "direction": "up",
  "actual_direction": "up",
  "correct": true
}
```

## 중요한 설계

LLM이 반환한 종목명을 가격 API에 직접 전달하지 않는다.

```text
"삼성전자"
    ↓
TickerResolver
    ↓
"005930"
    ↓
가격 데이터 제공자
```

티커를 찾지 못한 종목은 예측 전체를 중단하지 않고 `ticker=None` 상태로 저장한 뒤 검증 단계에서 개별 실패로 기록한다.

## 원본 대비 변경 파일

- `data/price_history.py`
  - 종목명 → 티커 Resolver 강화
  - `fetch_actual_stock_direction()` 추가
- `data/market_data.py`
  - `fetch_stock_actual()` 추가
- `memory/store.py`
  - 종목별 가격/수익률/실제 거래일 저장
  - 종목별 평균 수익률 및 적중률 집계
- `graph/pipeline.py`
  - 예측 저장 시 티커 확정
  - `verify_pending()`에서 모든 종목별 실제 결과 검증
- `tests/test_stock_verification.py`
  - 종목별 검증 로직 테스트

**원본의 `venv`, 데이터베이스, 캐시, 설정 파일 등은 삭제하지 않고 그대로 보존했다.**
