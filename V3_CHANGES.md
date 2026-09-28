# V3 변경사항 — 근거기반 토론 + QA 검증팀 + Gemini 지원

## 1. Bull/Bear 방향 고정 제거 (`agents/researchers.py`)

**이전**: 스키마 예시에 `"direction": "up"`(Bull)/`"down"`(Bear)이 고정값으로
박혀있어서, Bull은 데이터를 실제로 보고 판단하는 게 아니라 그냥 항상 "up"이라고
말하도록 강제된 것과 같았다. "에이전트별 적중률"을 재봐야 "그날 시장이 오를
확률 vs 내릴 확률"을 재는 것과 동일한 의미 없는 지표였다.

**지금**: `direction` 필드를 스키마에서 제거했다. 대신:
- 반드시 분석가 리포트/뉴스 자료에 실제로 있는 사실만 근거(`evidence`)로 인용
- 근거가 약하면 confidence를 낮추도록 명시 지시 (억지 확신 금지)
- 에이전트별 품질은 이제 QA 검증팀의 "근거 검증률"로 추적 (아래 참고)

## 2. QA 검증팀 신설 (`agents/qa_verifier.py`)

이중 검증 구조:

| 단계 | 무엇을 검증하나 | 비용 |
|---|---|---|
| 하위 QA (`verify_debate_evidence`) | Bull/Bear가 인용한 evidence가 실제 분석가 리포트/유사사례 자료에 있는 내용인지 대조 | 웹검색 없음, 저렴 |
| 상위 QA (`verify_final_result`) | Trader의 최종 reasoning이 토론/리스크 판단에 실제로 있던 내용에 근거하는지 대조 | 웹검색 없음, 저렴 |

둘 다 **새 정보를 찾지 않고 이미 가진 자료와 대조만** 하므로 토큰 비용이 적게
든다. 그리고 지금은 **차단하지 않고 플래그만** 남긴다 — 불일치가 실제로 얼마나
자주 나오는지 데이터가 쌓이기 전까지는 자동 차단/재시도를 넣지 않는 게 안전하다.

`memory/store.py`의 `AgentPrediction`에 `evidence_total`/`evidence_verified`
필드가 추가되어, Bull/Bear는 이걸로 품질을 추적하고 MarketAnalyst/RiskManager/
Trader는 기존처럼 방향 적중률로 추적한다 (`compute_stats()`의 `by_agent`가 두
방식을 자동으로 구분해서 집계).

## 3. Gemini API 지원 (`agents/base.py`)

`LLMClient` 추상 인터페이스는 그대로 두고, `GeminiLLMClient`를 새로 구현해서
`AnthropicLLMClient`와 동일하게 동작하도록 했다. `build_llm_client(provider=...)`
헬퍼로 provider만 바꾸면 파이프라인 코드는 전혀 손댈 필요가 없다.

```bash
# .env에서 프로바이더 전환
LLM_PROVIDER=anthropic   # 또는 gemini
GEMINI_API_KEY=...
GEMINI_MODEL=gemini-2.5-flash
```

주의: Gemini의 웹 검색 도구(Google Search grounding)는 Anthropic의
`web_search_20250305`와 API 형태가 달라서, `GeminiLLMClient.complete()` 내부에서
이 차이를 흡수했다. 파이프라인/에이전트 코드는 이 차이를 몰라도 된다.

## 테스트

`tests/test_qa_and_gemini.py` 신규 추가 (8개 테스트). 기존 테스트 중
Bull/Bear 고정 방향을 전제로 했던 것도 새 설계에 맞게 업데이트했다.
전체 25개 테스트 통과 확인함 (`pytest tests/ -v`).

## 남은 것 (다음 단계 후보)

- QA 검증 결과가 실제로 얼마나 자주 "불일치"를 잡아내는지는 아직 실 API로
  검증 못함 (Mock은 항상 verified=true를 돌려주도록 만들어서 로직 검증용으로만
  씀). 실제 Anthropic/Gemini API로 `cli.py run` 돌려보고 확인 필요.
- QA 결과를 "차단"으로 바꿀지는, 불일치 발생 빈도를 어느 정도 관찰한 뒤 결정.
