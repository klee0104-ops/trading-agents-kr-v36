# 단기 ETF 모의 청산 정책: `net_profit_v1`

2026-10-02 이후 새로 생성된 schema 4 ETF 계획의 **새 진입 회차**에만 적용한다. 기존 계획·보유·체결은 변경하지 않는다. 실거래 주문이나 거래소 스톱 주문이 아니라, 프로그램이 관측한 시세로 판단하고 다음 모의 체결을 기록하는 규칙이다.

## 계산

진입 원가 `C`는 매수 슬리피지와 수수료를 포함한 보유분 총원가, 수량은 `q`, 최신 유효 ETF 시세는 `P_t`다. 기존 모의 체결의 편도 슬리피지 `s=0.0005`, ETF 수수료 `f=0.00015`를 그대로 쓴다.

`r_t = 100 × (q × P_t × (1-s) × (1-f) / C - 1)`

`h_t = max(h_(t-1), r_t)`은 보유 중 관측한 비용 차감 예상 청산 수익률의 최고값이다. 순서대로 다음을 평가한다.

1. 기존 단기 일손실 2%·계좌 최고 평가액 대비 10% 손실 제한이 우선한다.
2. 15:20 이후 첫 유효 시세에는 예정 청산한다.
3. 새 회차에서 `r_t ≥ 1.20%`이면 목표수익 청산한다.
4. `h_t ≥ 0.60%`가 된 뒤 `h_t - r_t ≥ 0.35%p`이면 이익 되돌림 청산한다.
5. 그 외에는 기존의 매시간 KOSPI200 방향 재검토를 적용한다.

매도 후에도 당일 같은 ETF를 무조건 다시 사지 않는다. 기존처럼 다음 시간대의 지수 방향 확인, 유효한 시세와 QA가 필요하다. 수익 청산은 원래 15:20 예측 적중 판정에서 제외하고 실제 청산 손익만 기록한다.

0.60/0.35/1.20은 수익을 보장하거나 과거 자료로 최적화한 수치가 **아니다**. 왕복 가정 비용 약 0.13%보다 높은 모의 실험 시작값이다. 5분 점검 사이의 변동과 실제 호가 부재 때문에 트리거 가격과 체결 가격은 다를 수 있고, 이익 보호가 실패할 수 있다. 원장에 청산 사유와 그때 관측된 예상 순수익률을 남겨 이후 기존 규칙과 비교한다.

## 참고 자료

- [SEC Investor.gov, Stop, Stop-Limit, and Trailing Stop Orders](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins-15): 추적손절의 작동 방식과 트리거 가격이 체결 가격을 보장하지 않는다는 설명.
- [FINRA, Stop Orders: Factors to Consider During Volatile Markets](https://www.finra.org/investors/insights/stop-orders-factors-consider-during-volatile-markets): 급변 시 체결 괴리와 짧은 가격 변동에 따른 오작동 위험.
- [Kaminski & Lo, *When Do Stop-Loss Rules Stop Losses?*, Journal of Financial Markets 18 (2014)](https://dspace.mit.edu/entities/publication/bb69ca4b-0cdc-487f-831d-63b2e84fafee): 손절 규칙의 가치가 시장·전략 특성에 따라 달라질 수 있음을 분석한 원문 저자 저장소.
- [SEC Investor.gov, How Fees and Expenses Affect Your Investment Portfolio](https://www.investor.gov/introduction-investing/general-resources/news-alerts/alerts-bulletins/investor-bulletins/updated): 거래 비용이 수익률을 줄인다는 근거.
