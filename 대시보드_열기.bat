@echo off
setlocal
cd /d "%~dp0"
title TradingAgents-KR 대시보드 서버 (이 창을 닫으면 서버 종료)
call venv\Scripts\activate.bat
rem 이미 떠 있는 옛 서버(5000 포트)가 있으면 내리고 새로 띄운다 (코드 변경 반영)
for /f "tokens=5" %%p in ('netstat -ano ^| findstr /r /c:":5000 .*LISTENING"') do (
  echo 기존 대시보드 서버 PID %%p 를 종료합니다.
  taskkill /pid %%p /f >nul 2>&1
)
echo 대시보드 서버를 시작합니다. 잠시 후 브라우저가 열립니다.
echo 주소: http://127.0.0.1:5000  (이 PC에서만 접속 가능)
start "" /min cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:5000/"
python -m web.server
pause
