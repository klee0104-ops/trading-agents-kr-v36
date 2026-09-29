@echo off
setlocal
cd /d "%~dp0"
title TradingAgents-KR 아침 자동 분석
set PYTHONIOENCODING=utf-8
if not exist logs mkdir logs
for /f %%i in ('powershell -NoProfile -Command "Get-Date -Format yyyyMMdd"') do set TODAY=%%i
for /f %%i in ('powershell -NoProfile -Command "(Get-Date).Hour"') do set HOUR=%%i
set LOG=logs\자동분석_%TODAY%.log

echo ===== %date% %time% 자동 분석 시작 ===== >> "%LOG%"
call venv\Scripts\activate.bat

rem 마감 채점(15:45) 작업이 아직 없으면 자동 등록 (한 번만)
schtasks /query /tn "TradingAgents-KR 마감채점" >nul 2>&1
if errorlevel 1 (
  echo [등록] 마감 채점 작업 월~금 15:45 을 등록합니다. >> "%LOG%"
  powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\register_task.ps1" -BatPath "%~dp0복기_자동.bat" -Time "15:45" -TaskName "TradingAgents-KR 마감채점" >> "%LOG%" 2>&1
)

rem 장 시작(09:00) 이후 ~ 장 마감 전이면 아침 분석 의미가 없으므로 건너뜀 (수동 실행은 실제분석_로그실행.bat)
if %HOUR% GEQ 9 if %HOUR% LSS 16 (
  echo [건너뜀] 이미 장중 %HOUR%시 이라 아침 분석을 실행하지 않습니다. >> "%LOG%"
  exit /b 0
)

rem 1) 어제 예측 채점 (LLM 호출 없음) - 적중률 통계가 쌓입니다
echo --- 1/3 어제 예측 채점 --- >> "%LOG%"
python cli.py verify >> "%LOG%" 2>&1

rem 2) 오늘 분석 (실패하면 5분 간격 최대 3회 재시도: 무료 API 429 대비)
set TRY=0
:retry
set /a TRY+=1
echo --- 2/3 실제 분석 (시도 %TRY%/3) --- >> "%LOG%"
python cli.py run >> "%LOG%" 2>&1
if %errorlevel%==0 goto ok
if %TRY% GEQ 3 goto fail
echo [재시도 대기 5분] >> "%LOG%"
timeout /t 300 /nobreak >nul
goto retry

:ok
rem 3) 읽기 전용 결과 페이지 갱신 + 알림 + 브라우저 열기
echo --- 3/3 결과 페이지 생성/알림 --- >> "%LOG%"
python cli.py export-site >> "%LOG%" 2>&1
if errorlevel 1 exit /b 1
if not exist site\archive mkdir site\archive
copy /y site\index.html "site\archive\index_%TODAY%_아침.html" >nul
python tools\notify.py >> "%LOG%" 2>&1
echo ===== 완료 ===== >> "%LOG%"
exit /b 0

:fail
echo ===== 실패 (3회 시도) ===== >> "%LOG%"
python tools\notify.py --failed "%LOG%" >> "%LOG%" 2>&1
exit /b 1
