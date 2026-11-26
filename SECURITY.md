# API 키 보안 가이드 (V3.6)

## 한 줄 요약
키는 `.env`에 적지 말고 **`python cli.py set-key gemini`** (또는 `start.bat` 메뉴 8번)으로
Windows 자격 증명 관리자에 저장하세요. 프로그램이 알아서 거기서 읽습니다.

## 키를 찾는 순서
1. **시스템 환경변수** (`GEMINI_API_KEY`, `ANTHROPIC_API_KEY`) — 사용자가 직접 설정한 경우
2. **Windows 자격 증명 관리자** (서비스 이름 `TradingAgents-KR`) — 권장
3. **`.env` 파일** — 평문이라 비권장. 호환성 때문에만 남겨둠

`sk-ant-...`, `AIza...` 같은 예시 값은 "키 없음"으로 처리합니다.

## 명령어

미국장 읽기 전용 IEX 연결에 쓸 Alpaca **paper 전용 키**는 `python cli.py set-key alpaca_id`와 `python cli.py set-key alpaca_secret`으로 별도 보관합니다. `python cli.py key-status`에서 값은 일부도 표시하지 않습니다. 이 키로 실제 주문을 호출하는 코드는 없으며, 공개 HTML과 배포 zip에도 키·미국장 원장·분석 계획이 들어가지 않습니다. 미국장 계좌 요약 공개는 `US_PUBLIC_SUMMARY=true`를 설정했을 때만 생성되며 원시 호가·체결 추정 가격은 포함하지 않습니다. 상세 운용은 [US_MARKET.md](US_MARKET.md)를 참고하세요.

| 명령 | 하는 일 |
|---|---|
| `python cli.py set-key gemini` | Gemini 키를 숨김 입력 → 자격 증명 관리자에 저장 |
| `python cli.py set-key dart` | OpenDART 인증키 40자리를 숨김 입력 → 별도 자격 증명 항목에 저장 |
| `python cli.py key-status` | 키 로드 여부·출처 확인 (OpenDART 값은 일부도 표시하지 않음) |
| `python cli.py migrate-keys` | `.env`의 평문 키를 자격 증명 관리자로 옮기고 `.env`에서 삭제 |
| `python cli.py delete-key gemini` | 저장된 키 삭제 |
| `python cli.py check-secrets` | 프로젝트 파일에 키가 새어 있는지 검사 |
| `python cli.py package` | `.env`·DB·예측기록·venv·`.claude`를 뺀 공유용 zip 생성 (만든 뒤 다시 검사해서 키가 있으면 zip 삭제) |

OpenDART 키는 `DART_API_KEY`라는 별도 항목으로 보관합니다. 상태 화면에는 키 일부도 표시하지 않습니다. `python cli.py delete-key dart`로 해당 항목만 삭제할 수 있습니다. 키가 있으면 장기팀 예약작업의 다음 데이터 수집에서 공식 공시 목록과 구조화 재무제표를 조회합니다. 키 등록 자체가 유료 모델 호출이나 즉시 가상 주문을 실행하지는 않습니다. 공식 API 요청에는 키가 필요하지만 응답 상태·로그·공개 페이지·모의계좌 원장에 키나 키가 포함된 요청 URL을 저장하지 않습니다.

## 코드에 들어간 보호 장치
- **SecretStr**: 키는 `print`/`repr`/f-string/JSON/pickle 어디로도 평문이 나오지 않음. SDK 클라이언트를 만드는 순간에만 `.reveal()`
- **os.environ 미노출**: `.env`의 키는 `os.environ`에 올리지 않음 → 자식 프로세스로 상속 안 됨
- **마스킹**: 로그 필터 + 처리되지 않은 예외(traceback) + LLM SDK 예외 메시지 모두 키 패턴(`sk-ant-`, `AIza`, `AQ.`, `?key=`)과 실제 로드된 키 값을 가림. SDK 원본 예외 체인은 끊어서 요청 헤더가 traceback에 안 나오게 함
- **HTTP 디버그 로그 차단**: httpx/urllib3/SDK 로거를 WARNING 이상으로 고정
- **웹 대시보드**
  - `127.0.0.1`에만 바인딩 (같은 와이파이의 다른 기기에서 접근 불가)
  - Host 헤더 허용목록 → DNS 리바인딩 공격 차단
  - POST는 서버 시작 때마다 새로 만드는 세션 토큰(`X-TA-Token`) + Origin 검사 + JSON만 허용 → 다른 웹사이트가 몰래 "실제 분석 실행"을 눌러 과금시키는 CSRF 차단
  - 실제 분석은 동시에 1건만 (과금 폭주 방지)
  - `/api/health`는 키 존재 여부와 출처만 반환 (값·끝자리 모두 미포함)
  - 에러 응답 마스킹, CSP/X-Frame-Options/nosniff/no-store 헤더
  - 정적 파일은 `app.js`, `style.css`만 서빙 (`.env` 등 경로 조작 불가)
  - 수집 뉴스 링크는 http/https만 허용, LLM·뉴스 텍스트는 전부 HTML 이스케이프
- **`.gitignore`**: `.env`, `*.db`, 예측 기록, `venv/`, `.claude/`, `*.zip`

## 키가 노출됐을 때
1. Google AI Studio / Anthropic Console에서 **기존 키 삭제 → 새 키 발급**
2. `python cli.py set-key gemini`로 새 키 등록
3. 키가 들어있던 zip·폴더·채팅 기록 정리

> 참고: 이전 버전 폴더 `새 폴더 (3)` 와 그 zip 안의 `.env`에 Gemini 키가 평문으로 들어있었습니다.
> 이 zip을 누군가와 공유했거나 업로드한 적이 있다면 재발급을 권장합니다.
