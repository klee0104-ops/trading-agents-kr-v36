# 검증 하네스

프로젝트 루트에서 다음 명령을 실행한다. Python은 기존 venv의 인터프리터를 권장한다. Node.js도 필요하다.

```powershell
.\venv\Scripts\python.exe tools\verify_harness.py
```

JS 문법 → 프론트엔드 회귀 검사 → 전체 pytest 순서로 실행하며 하나라도 실패하면 0이 아닌 종료 코드로 중단한다. 각 단계 제한 시간은 180초다. CI에서는 이 명령의 성공을 배포 선행 조건으로 설정한다. 현재 저장소에는 CI/브랜치 보호가 없어 명령을 실행하지 않는 수동 배포까지 차단하지는 못한다.

테스트 중 외부 소켓 연결은 conftest의 하네스 모드에서 차단한다. 실제 분석이나 사이트 내보내기는 실행하지 않는다. JS 검사는 VM 기반으로 누락 요소 계약, 두 개 이상 백테스트 비교, KPI 이스케이프, QA 미검증 표시를 다룬다. 브라우저 레이아웃·접근성 전체 검사를 대신하지 않는다.

구조: `web/` HTTP·화면, `security/` 비밀값·패키징, `graph/` 실행 흐름, `agents/` LLM 판단, `rules/` 결정적 계산, `execution/` 모의계좌, `memory/` 저장, `tests/` 회귀, `tools/verify_harness.py` 완료 게이트.

개발 지침은 AGENTS.md에 둔다. [공식 AGENTS.md 문서](https://learn.chatgpt.com/docs/agent-configuration/agents-md)에 따른 프로젝트 지침이며 보안 샌드박스가 아니다. 내부 LLM 출력 스키마·호출 예산·프롬프트 인젝션 대응 강화는 별도의 런타임 정책 구현이 필요하다.
