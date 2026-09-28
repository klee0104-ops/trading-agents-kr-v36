@echo off
setlocal
cd /d "%~dp0"
title TradingAgents-KR 대시보드 [보기 전용] (이 창을 닫으면 서버 종료)
call venv\Scripts\activate.bat
set WEB_READONLY=true
echo [보기 전용 모드] 분석 실행 / 데모 / 뉴스 수집 버튼이 숨겨지고 서버에서도 차단됩니다.
echo 주소: http://127.0.0.1:5000  (이 PC에서만 접속 가능)
start "" /min cmd /c "timeout /t 3 /nobreak >nul & start http://127.0.0.1:5000/"
python -m web.server
pause
