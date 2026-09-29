# 비공개 소스와 HTML 공유

## 고정 주소 자동 갱신 (2026-09-29)

공유 주소: https://klee0104-ops.github.io/trading-agents-kr-report/

소스 저장소 `trading-agents-kr-v36`는 Private으로 유지하며 Pages를 켜지 않는다. 별도 Public 저장소 `trading-agents-kr-report`에는 보안 검사한 `index.html`과 `.nojekyll`만 둔다. 이 주소와 게시된 결과는 인터넷에서 누구나 열 수 있다. 검색 제외 표시는 접근 통제가 아니다. 공개 Git 이력에도 과거 공유 결과가 남는다.

`sharing_public.json`의 `enabled: true`이면 `python cli.py export-site`가 HTML 생성 후 자동 업로드한다. 기존 아침 분석·마감 채점 배치도 이 명령을 사용하므로 같은 흐름에 연결된다. `false`로 바꾸면 로컬 HTML만 생성한다. 생성/검사/업로드 중 실패하면 명령은 실패하고 공개 페이지는 이전 버전을 유지한다. 업로드 후 GitHub Pages 반영에 수 분 걸릴 수 있다.

사이트는 5분마다 새로고침하여 새 게시물을 가져온다. 브라우저에서 분석하거나 로컬 PC에 접속하지 않는다. PC가 꺼졌거나 로그아웃·네트워크 장애·GitHub 인증 만료로 작업이 실행되지 않으면 새 데이터는 게시되지 않는다. 기존 다운로드 HTML/ZIP은 자동 교체되지 않으므로 고정 주소를 전달한다.

GitHub CLI 인증은 Windows 자격 증명 저장소를 사용한다. 프로젝트 파일·HTML·로그에 토큰을 쓰지 않는다. 사용자 계정의 `gh` 인증으로 고정 대상 저장소의 index.html만 업로드하며 `.env`, 원본 계좌 JSON, DB, 로그, 소스는 전송하지 않는다. 실제 HTML에 CSP 스크립트 해시와 외부 연결 차단을 적용하고 활성 키뿐 아니라 환경변수/.env의 가려진 키도 검사한다.

GitHub에는 소스만 Private 저장소로 보관한다. 협업자를 추가하지 않으면 일반 사용자는 열람할 수 없다. API 키는 내 PC 자격 증명 관리자에 보관하고 .env, DB, 계좌, 로그, 분석 결과, site/를 업로드하지 않는다.

## 파일 전달

`공유페이지_만들기.bat` 또는 `python cli.py export-site` 실행 후 `site/index.html` 한 파일만 전달한다. 보고서는 생성 당시의 스냅샷이며 자동 갱신되지 않는다. 사용자가 공유를 허용한 가상계좌 수익률·평가금액·수익곡선·보유 종목·최근 모의 체결 내역을 포함한다. 화면에는 모의투자이며 실제 자금 거래가 없다고 표시한다. 공유본에는 API 호출 기능이 없으며 뉴스 제목 및 키 마스킹을 적용한다. 외부 폰트 요청 없이 브라우저 기본 대체 폰트를 사용한다. 원본 계좌 JSON·API 키·DB는 계속 GitHub에서 제외한다.

수신자는 HTML 내부에 포함된 분석과 예측 기록을 읽고 저장·재전달할 수 있다. 파일을 전달한 뒤 회수하거나 지정인만 열도록 제한하는 기능은 없다. 파일 안에 JavaScript 비밀번호 창을 넣는 것은 접근 통제가 아니다.

## 로그인 공유: Cloudflare Access + 정적 보고서

API 서버 전체를 공개하지 말고 공유 HTML만 별도 정적 사이트로 배포한다. GitHub의 Python 소스·키를 웹에 배포할 필요가 없다.

1. Cloudflare 계정에서 Pages 프로젝트를 준비한다. 민감한 보고서 대신 빈 페이지로 먼저 구성한다.
2. Zero Trust의 Access에서 사이트를 보호하고 One-time PIN 로그인 방식을 활성화한다.
3. Allow 정책의 Include에는 열람할 **정확한 이메일 주소**를 넣는다. Everyone 또는 로그인 방식만 Include에 넣으면 안 된다. 미등록 사용자는 거부한다.
4. 세션 기간을 정하고, 보고서 URL 전체(HTML/정적 데이터 포함)를 보호한다. Pages의 기본 Access 토글은 미리보기만 보호하므로 프로덕션 `project.pages.dev`, `*.project.pages.dev` 미리보기, 사용자 도메인 모두 별도 확인한다. 인증을 우회할 원본 주소를 남기지 않는다.
5. 로그아웃 브라우저에서 본문 차단, 허용 이메일 로그인 성공, 미등록 이메일 차단, 모든 별칭·미리보기 주소 차단을 확인한 뒤 보고서를 올린다.
6. 권한 회수 시 허용 목록에서 삭제하고 활성 세션도 취소한다. 이미 저장한 파일·캡처는 회수할 수 없다.

수신자 흐름: 링크 → 이메일 입력 → 이메일로 받은 인증 코드 입력 → 보고서 열람. 직접 이메일을 발송하는 기능은 이번 작업에서 사용하지 않는다.

실제 설정에는 Cloudflare 계정 로그인과 허용할 이메일 목록, 필요시 사용할 도메인이 필요하다. 이 문서 작성만으로 로그인 제한이 활성화되지는 않는다. 요금·인원 한도는 설정 시 계정의 현재 플랜에서 확인한다.

공식 근거:
- https://developers.cloudflare.com/cloudflare-one/integrations/identity-providers/one-time-pin/
- https://developers.cloudflare.com/cloudflare-one/access-controls/policies/
- https://developers.cloudflare.com/pages/platform/known-issues/
