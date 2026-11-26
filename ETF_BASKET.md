# AI ETF 바스켓 비교 가상계좌

기존 3,000만 원 원장과 별도의 1,000만 원 비교 실험이다. 자본금과 손익을 기존 팀별 계좌에 합산하지 않는다. 실제 주문 API를 호출하지 않는다.

## 선정과 승인

- 후보: KODEX 200(069500, 국내 대표주), KODEX 미국S&P500(379800, 미국 대표주·환노출), KODEX 골드선물(H)(132030, 금선물·환헤지), KODEX 머니마켓액티브(488770, 초단기 금융상품).
- 네이버 증권 공개 일별 시세에서 직전 거래일까지 각 후보 10개 종가를 모은다. AI는 이 자료만 보고 2~4개 ETF와 정수 비중·정성 근거·자료 ID를 제안한다. Gemini 1회 호출이 필요하며 모델 응답을 승인으로 취급하지 않는다.
- 계획 생성 때 전달한 10개 종가의 첫값·마지막값·변화율·최저/최고 차이·상승/하락 구간 수를 수치 요약으로 보존한다. 이는 AI가 작성한 장문 추론이 아니라 입력 자료의 결정적 계산이다. 기존 계획에는 원문 종가가 저장되지 않았으므로 `--backfill-evidence`로 같은 기준일 자료를 다시 조회할 수 있다. 이때 `rechecked_after_creation`으로 표시하며 원래 모델 입력과 동일하다고 주장하지 않는다. AI가 남긴 편입 판단 원문은 한 문장뿐이다.
- 다음 계획부터는 기존 Gemini 계획 호출 한 번에서 ETF별 가격 흐름 해석·비중 선택 이유·반대 시나리오 세 문장을 함께 요청한다. 자기 시세 ID·길이·위험 문자열 검증을 통과한 세 문장만 `analysis`로 저장한다. 누락되면 장문 분석이 없는 상태로 표시하며 한 줄 근거의 기존 계획 QA와 체결 자격을 바꾸지 않는다. 출력 토큰 상한은 1,400에서 2,400으로 증가하므로 다음 `--analyze` 호출의 사용량은 늘 수 있다. 이 변경 자체는 모델을 호출하지 않는다.
- 결정적 QA는 직전 거래일 자료, 종목 코드, 중복, 자기 시세 ID, 근거, 비중을 검증한다. ETF 합계 40~90%, 현금 10% 이상, 종목별 상한은 40/35/20/40%다. 인버스·레버리지·미제공 코드는 거부한다. 실패 시 현금을 유지한다.
- 생성된 계획은 다음 거래일(장전 실행이면 당일)만 첫 진입 대상으로 삼는다. 목표일을 지나면 자동 소급체결하지 않는다. 현재 버전의 첫 계획 이후 자동 리밸런싱은 하지 않는다.

## 가상 체결과 비교

정규장 중 관측 60초, 마지막 거래 15분 이내인 ETF 시세가 계획·보유 종목 모두에 있어야 한다. 네 종목 시세를 받은 **뒤의 시각**으로 신선도를 검사한다. 누락 시 주문과 현재 성과를 보류한다. 정수 주 단위, 매수 편도 수수료 0.015%, 불리한 방향 가격 차이 0.05%를 적용한다. 첫 바스켓 진입 시 같은 시각·동일 비용 가정으로 KODEX 200 100% 가상 매수 기준선을 시작한다. 상품 자체 보수는 ETF 가격에 반영되는 범위 외에 별도 차감하지 않는다. 세금, 분배금, 실제 호가·잔량, 환전은 모델링하지 않는다.

원장은 `paper_etf_basket.json`, AI 계획은 `paper_etf_basket_plan.json`이다. 두 파일은 기존 `paper_portfolio.json`과 같은 디렉터리에 있지만 별개다. 공개에는 허용된 요약만 전달한다. API 키와 원시 모델 응답은 계획·계좌·공유 페이지에 저장하지 않는다.

명시적 수동 명령:

```powershell
python -B -m tools.etf_basket --init
python -B -m tools.etf_basket --analyze
python -B -m tools.etf_basket --tick
python -B -m tools.etf_basket --status
python -B -m tools.etf_basket --backfill-evidence
```

`--analyze`는 그 목표 거래일에 대해 중복 모델 호출을 하지 않는다. `--tick`은 정규장 외에는 네트워크 요청 없이 종료한다. 기존 `TradingAgents-KR PaperPortfolio` 예약작업의 5분 `--tick`에 이 계좌의 별도 점검을 연결했다. 첫 진입은 목표 거래일에 승인된 계획과 유효한 정규장 시세가 모두 있을 때만 실행한다. 이후 보유분은 같은 예약작업으로 평가하며 새 AI 계획을 자동 호출하거나 자동 리밸런싱하지 않는다. 두 원장과 잠금은 분리되고, 한쪽 실패가 다른 쪽 점검을 막지 않는다. 예약작업 결과와 최근 상태는 `logs/paper_portfolio_status.json`의 `status`와 `basket_status`로 확인한다.

기존 계획의 자료 보충은 사용자 원장·체결을 건드리지 않고 계획에 선택적 `evidence_summary`만 추가한다. 쓰기 직전 `paper_etf_basket_plan.json.before-evidence-backfill`에 원본 전체를 보존한다. 복구하려면 예약작업이 계획 파일을 읽지 않는 시간에 현재 계획을 별도로 보존하고 이 백업을 원래 계획 파일명으로 복사한다. 백업이 이미 있으면 덮어쓰지 않고 중단한다. 과거 자료 재조회 실패 시 계획은 변경하지 않는다.

복구하려면 기존 계좌가 아니라 위 두 바스켓 파일만 보존·분리한다. 이미 모의 체결한 기록은 새 AI 계획으로 덮어쓰지 않는다.

## 근거와 위험

상품 코드와 구조는 삼성자산운용의 공식 KODEX 상품 자료로 확인한다. ETF는 시장가격과 NAV가 달라질 수 있고 스프레드·비용은 수익을 낮춘다. 미국지수 ETF는 환율, 금선물 ETF는 선물 롤오버·환헤지, 머니마켓 ETF는 신용·금리 위험이 있다. 분산은 손실 방지가 아니다.

- SEC ETF 안내: https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-24
- SEC ETF 비용 안내: https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/mutual-fund-and-etf-fees-and-expenses-investor-bulletin
- KODEX 상품 검색: https://www.samsungfund.com/etf/search.do
