@echo off
setlocal
cd /d "%~dp0"

where python >nul 2>nul
if errorlevel 1 goto no_python

if exist venv goto venv_ready
echo [최초 실행] 가상환경을 생성합니다...
python -m venv venv
if errorlevel 1 goto venv_fail

:venv_ready
call venv\Scripts\activate.bat
if errorlevel 1 goto activate_fail

echo [의존성 확인 중...]
pip install -r requirements.txt
if errorlevel 1 goto pip_fail

if exist .env goto menu
echo .env 파일이 없어 .env.example을 복사합니다.
copy .env.example .env >nul
echo .env 파일을 만들었습니다. 이어서 API 키를 안전하게 등록합니다.
echo (키는 .env가 아니라 Windows 자격 증명 관리자에 저장됩니다)
goto do_setkey

:menu
echo.
echo ===== TradingAgents-KR =====
echo 1. 실제 분석 실행 (run)
echo 2. Mock 데모 (demo, API 불필요)
echo 3. 지난 예측 검증 (verify)
echo 4. 구간 백테스트 (backtest)
echo 5. 뉴스/변동성 데이터 수집 (collect, API 불필요)
echo 6. 웹 대시보드 실행 (브라우저 자동 접속)
echo 7. 종료
echo 8. API 키 등록/변경 (Windows 자격 증명 관리자에 안전 저장)
echo 9. API 키 상태 확인 / .env 평문 키 이전
echo 10. 공유용 zip 만들기 (.env/기록/DB 제외 + 키 유출 검사)
echo 11. 아침 자동 실행 등록 (월~금 07:30, 알림+결과 페이지)
echo 12. 아침 자동 실행 해제
echo =============================
set "choice="
set /p choice="선택: "

if "%choice%"=="1" goto do_run
if "%choice%"=="2" goto do_demo
if "%choice%"=="3" goto do_verify
if "%choice%"=="4" goto do_backtest
if "%choice%"=="5" goto do_collect
if "%choice%"=="6" goto do_web
if "%choice%"=="8" goto do_setkey
if "%choice%"=="9" goto do_keystatus
if "%choice%"=="10" goto do_package
if "%choice%"=="11" goto do_autoreg
if "%choice%"=="12" goto do_autounreg
goto end

:do_autoreg
call "%~dp0자동실행_등록.bat"
goto menu

:do_autounreg
call "%~dp0자동실행_해제.bat"
goto menu

:do_setkey
set "provider="
echo   gemini / anthropic = AI 키,  naver_id / naver_secret = 네이버 검색 API(뉴스, ncloud.com NAVER API HUB)
set /p provider="provider 입력 (기본 gemini): "
if "%provider%"=="" set "provider=gemini"
python cli.py set-key %provider%
goto after_run

:do_keystatus
python cli.py key-status
echo.
set "mig="
set /p mig=".env에 평문 키가 있다면 자격 증명 관리자로 옮길까요? (y/N): "
if /i "%mig%"=="y" python cli.py migrate-keys
goto after_run

:do_package
python cli.py check-secrets
python cli.py package
goto after_run

:do_run
python cli.py run
goto after_run

:do_demo
python cli.py demo
goto after_run

:do_verify
python cli.py verify
goto after_run

:do_backtest
set "start_date="
set "end_date="
set /p start_date="시작일 (예: 2026-06-01): "
set /p end_date="종료일 (예: 2026-06-30): "
python cli.py backtest %start_date% %end_date%
goto after_run

:do_collect
set "stock_name="
set "start_date="
set "end_date="
set "threshold="
set /p stock_name="종목명 (예: 삼성전자): "
set /p start_date="시작일 (예: 2026-06-01): "
set /p end_date="종료일 (예: 2026-06-30): "
set /p threshold="변동성 임계값 %% (기본 3.0, 그냥 엔터 가능): "
if "%threshold%"=="" set "threshold=3.0"
python cli.py collect %stock_name% %start_date% %end_date% %threshold%
goto after_run

:do_web
echo 웹 대시보드 서버를 새 창에서 시작합니다. 그 창을 닫으면 서버가 종료됩니다.
start "TradingAgents-KR 서버" /D "%~dp0" cmd /k venv\Scripts\python.exe -m web.server
timeout /t 3 /nobreak >nul
start "" "http://127.0.0.1:5000/"
goto after_run

:after_run
echo.
pause
goto menu

:no_python
echo [오류] python 명령을 찾을 수 없습니다.
echo python.org 에서 파이썬을 설치하고, 설치 시 "Add python.exe to PATH"를 체크해주세요.
pause
exit /b 1

:venv_fail
echo [오류] 가상환경 생성에 실패했습니다. 위 메시지를 확인해주세요.
pause
exit /b 1

:activate_fail
echo [오류] 가상환경 활성화에 실패했습니다. venv 폴더가 손상되었을 수 있습니다.
echo venv 폴더를 삭제한 뒤 다시 실행해보세요.
pause
exit /b 1

:pip_fail
echo [오류] 의존성 설치에 실패했습니다. 위 메시지를 확인해주세요.
pause
exit /b 1

:end
exit /b 0