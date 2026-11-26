# 매매 활성화 작업 결과 — 2026-10-22

모든 거래는 가상 원장에 한정한다. 기존 `paper_portfolio.json`의 매매 비중·레버리지 확률·손실 중지 규칙은 유지한다. 새 실험값은 `paper_shadow_v2.json`에만 적용하며 수익 개선을 아직 주장하지 않는다.

| 작업 | 변경 파일 | 구현·검증 범위 | 남은 한계 |
|---|---|---|---|
| P0-1·2 | `agents/moderator.py`, `agents/trader.py`, `graph/pipeline.py`, `tests/test_moderator.py`, `tools/probability_trial.py` | 미해결 토론의 50% 앵커 제거, 근거 기반 방향·강도 지시. 기록을 남기지 않는 5회 Trader 시험 도구 추가 | 실제 모델 5회 확률 분포는 별도 유료 실행 결과로 판단 |
| P0-3 | `data/etf_forecast.py`, `agents/stock_moderator.py`, `execution/etf_contract.py`, `tests/test_etf_alignment.py` | ETF별 방향 근거 요구, 세 자산 모두 정확히 50일 때만 중립 탐색. 기존 운영 5%는 유지 | 프롬프트 변경 후 실제 모델 분포 검증 필요 |
| P0-4 | `rules/consensus.py`, `graph/pipeline.py`, `memory/store.py`, `tools/migrate_consensus.py`, `tests/test_consensus.py` | 출처 미확인 분석가 제외·토론 QA·SOX 합의 점수(−1~1)를 AI 확률과 별도 저장 | 이 점수가 예측력 개선에 기여하는지 아직 미검증 |
| P0-5 | `rules/calibrate.py`, `execution/shadow_v2.py`, `tests/test_calibrate.py` | 섀도 진단만 검증 기록 12건부터 축소 보정. 운영 원장 20건 기준 유지 | ETF 자체 확률은 KOSPI 보정값으로 대체하지 않음 |
| P1-1·2·3 | `config.py`, `rules/activation_policy.py`, `execution/position.py`, `execution/etf_contract.py`, `execution/leverage_contract.py`, `tests/test_activation_shadow.py` | 별도 섀도에 edge 0.04, 목표변동성 20%, 하한 0.6, 합의 1.25배, 최근 40건 상대 강진입선, 중립 10% 적용. 레버리지 상대 기준은 진단만 | 설정은 최적값이 아닌 실험값. 운영 레버리지 60%·장중 +0.15%와 QA 유지 |
| P1-4·5·6 | `execution/leverage_contract.py`, `memory/long_report.py`, `tools/long_research.py`, `execution/split_portfolio.py`, `tools/paper_portfolio.py`, `LEVERAGE_PAPER.md`, `SPLIT_PORTFOLIO.md` | 레버리지 14일 0체결 시 대기 사유 3개, 장기 후보 관문별 집계, 장기 ETF 중지 수동 검토·재개·백업 | 자본 자동 이체·장기 기준 완화 없음. 중지 재개 명령은 실행하지 않음 |
| P2-1·2 | `execution/split_portfolio.py`, `execution/exposure_alert.py`, `execution/paper_opportunity.py`, `web/export_site.py`, `tools/refresh_public_quotes.py` | 날짜별 투자·현금 비중, 5거래일 저노출 로컬 경고, 무거래 QA 사유 한 줄 공개 | 시세 누락일은 노출 기록하지 않음. 경고 알림 발송은 없음 |
| P2-3·4·5 | `tools/morning_retry_once.py`, `tools/register_morning_retry.ps1`, `tools/register_public_quotes.ps1`, 이 문서 | 사용자 승인으로 08:20 조건부 1회 재시도 등록, 공개 게시 점검 10분 주기 적용. 구 공격형 비교 종료 원인 확인 | PC 꺼짐·절전·로그아웃 시 실행 보장 없음. 별도 AI 호출은 분석 누락 때만 |
| P3 | `execution/shadow_v2.py`, `tools/paper_portfolio.py`, `web/export_site.py`, `tests/test_activation_shadow.py` | 별도 1,200만원 원장, 동일 5분 작업·시세·비용, 10거래일 비교 카드와 자동 전환 차단 | 비교가 시작된 뒤 실제 거래일 표본이 쌓여야 평가 가능 |

## 운영 확인

- 10월 14일: `logs/manual_catchup_2026-10-14_analysis.log`가 09:22에 작성됐고 같은 날 자동 분석 로그는 확인되지 않았다. 현재 07:30 작업은 활성(Ready), 10월 22일 마지막 결과 0이다. 과거 작업 스케줄러 이벤트를 읽을 수 없어 10월 14일 누락 원인은 확정할 수 없다. 10월 22일 사용자 승인으로 08:20 재시도 작업을 등록했다.
- 기존 `paper_aggressive.json`은 10월 7일 이후 `comparison_archive`가 설정돼 있다. 코드상 schema 4 계획이 기존 정책에 승격되면 비교를 보관하고 더 이상 틱하지 않는 설계다. 과거 공격형 원장을 재사용하지 않는다.
- PublicQuotes는 10분마다 내용을 확인하고 변경분이 있으면 게시한다. 모의계좌의 5분 점검과 공개 사이트 브라우저의 5분 확인은 별도다. 직전 Windows 작업 설정은 `.review-backups/scheduler-20261022-095936/PublicQuotes.xml`에 보관했다.
- 실제 작업 스케줄러에서 `MorningRetry`는 평일 08:20(다음 실행 10월 23일 08:20), `PublicQuotes`는 `PT10M`(첫 실행 10월 22일 10:03:10), 기존 `PaperPortfolio`는 `PT5M`으로 확인했다. 등록 전 전체 하네스 JS·프론트엔드·Python **612건 통과**. 08:20 작업을 현재 시각에 `--execute`로 호출했을 때 시간 창 밖으로 판단해 모델 분석 없이 종료했다.
- 10월 22일 10:03:10 새 PublicQuotes 작업의 첫 자동 실행은 결과 코드 0, `logs/public_quotes_status.json`의 상태 `published`였다. 다음 실행 시각은 10:13:10으로 10분 간격이다. MorningRetry의 첫 예약 실행은 10월 23일이므로 실제 누락 복구 성공 여부는 아직 확인되지 않았다.
- 예약 설정을 되돌릴 때는 `MorningRetry`를 사용 안 함으로 설정하고, 백업한 `PublicQuotes.xml`을 `Register-ScheduledTask -TaskName 'TradingAgents-KR PublicQuotes' -Xml (Get-Content -LiteralPath '.review-backups/scheduler-20261022-095936/PublicQuotes.xml' -Raw) -Force`로 복원한 뒤 주기 `PT5M`을 확인한다. 복원은 예약작업이 실행 중이지 않을 때 수행한다.

## 원장·스키마와 복구

- 과거 예측·체결·채점·수익률은 재계산하지 않는다. 새 예측에 `consensus_score`가 추가된다. 기존 기록의 필드 추가가 필요하면 `python -m tools.migrate_consensus --apply`가 원본을 `.review-backups/consensus-<시각>/memory_store.json`에 백업한 뒤 과거 행에 `null`만 넣는다.
- `paper_portfolio.json`은 신규 날짜별 노출 필드가 추가되는 시점에 기존 파일을 `.review-backups/activation-exposure-<시각>/paper_portfolio.json`에 백업한다. 장기 ETF 수동 중지 검토·재개에도 `.review-backups/long-core-stop-*` 백업을 만든다.
- 복구 시 예약작업을 중지하고 현재 파일을 별도로 보존한 뒤 해당 백업을 원래 위치로 복사한다. 백업 이후 체결이 있다면 단순 덮어쓰면 그 체결을 잃으므로 먼저 기록을 대조한다.
- 별도 `paper_shadow_v2.json`은 기존 계좌 손익에 합산하지 않는다. `PAPER_SHADOW_V2_ENABLED=false`로 새 틱을 중단할 수 있다. 비교 중단은 파일을 삭제하는 명령이 아니다.

## 검증과 전환

작업 복사본과 실제 프로젝트의 전체 하네스에서 JS 문법·프론트엔드 회귀·Python **612건 통과**를 확인했다. 후속 정책·마이그레이션·재시도 집중 검사도 **20건 통과**했다. 신규 테스트는 Mock·합성 시세·임시 경로만 사용한다.

10월 22일 실제 저장 자료로 Gemini Trader 판단을 **5회** 비기록 실행한 결과 `[55, 55, 55, 55, 55]%`였다(`logs/probability_trial_20261022.json`). 정확히 50% 비율은 0%로 목표를 충족하지만, 값이 55%에만 몰려 분포 다양성·확률 보정 성능이 개선됐다고 판단할 수 없다. 이 시험은 동일 시장 자료를 반복한 것이며 미래 적중률을 검증하지 않는다. 신규 분석 전체 파이프라인이나 체결 기록은 추가 생성하지 않았다.

실제 프로젝트에는 41개 파일을 `.review-backups/activation-code-20261022-094352`에 백업 후 반영했다. 과거 예측 17건은 `.review-backups/consensus-20261022-094421/memory_store.json` 백업 뒤 `consensus_score: null`만 추가했다. 09:46 기존 5분 작업은 결과 0으로 끝났고 별도 1,200만 원 비교 원장을 생성했다. 당일 진입 시간 종료 후 생성되어 비교 시작일은 **2026-10-23**, 현재 신규 비교 체결은 0건이다. 새 정책의 수익 우위는 아직 알 수 없다.

첫 운영 틱은 기존 3,000만 원 원장을 `.review-backups/activation-exposure-20261022-094632`에 백업했다. 기존 계좌는 체결 51건이며 별도 비교 계좌는 체결 0건으로 합산되지 않는다. 09:51 PublicQuotes 예약 작업은 결과 0, 상태 `published`로 종료했다. 별도 `chatgpt.site` 공개 홈페이지에는 새 비교·거래 사유 화면과 같은 공개 게시본의 내장 사본을 버전 **54**로 배포했고, 브라우저에서 두 카드 표시를 확인했다. 사이트는 GitHub 공개 게시본을 읽고 변경 시 화면을 갱신한다. 사이트 배포는 기존 계좌의 분석·매매 예약 주기를 바꾸지 않았다.

최소 10거래일, 권장 20거래일 동안 비용 차감 수익·최대 낙폭·체결·평균 노출·방향 정확도·Brier·정확히 50 비율을 비교한다. 수익 우위, 낙폭 1.5배 이내, 정확히 50 비율 10% 이하가 모두 충족돼도 정책을 자동 변경하지 않는다. 전환·롤백은 사용자의 별도 결정과 `capital_events` 시각 기록 후 실행한다.
