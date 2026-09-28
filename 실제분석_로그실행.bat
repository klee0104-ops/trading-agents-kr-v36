@echo off
setlocal
cd /d "%~dp0"
title TradingAgents-KR 실제 분석 (로그 기록)
call venv\Scripts\activate.bat
set PYTHONIOENCODING=utf-8
echo 실제 분석 실행 중... 결과는 실행로그.txt 에 저장됩니다. 2~5분 걸릴 수 있습니다.
python cli.py run > 실행로그.txt 2>&1
echo 종료 코드: %errorlevel% >> 실행로그.txt
type 실행로그.txt
echo.
echo 완료. 이 창은 10초 뒤 닫힙니다.
timeout /t 10 >nul
