@echo off
setlocal
cd /d "%~dp0"
title TradingAgents-KR 아침 자동 실행 등록
echo 매주 월~금 07:30 에 자동 분석을 실행하도록 Windows 작업 스케줄러에 등록합니다.
echo (PC가 켜져 있고 로그인된 상태여야 합니다. 절전 상태면 깨워서 실행합니다.)
echo.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\register_task.ps1" -BatPath "%~dp0자동분석_실행.bat" -Time "07:30"
echo.
echo 지금 바로 한 번 테스트하려면 자동분석_실행.bat 을 더블클릭하세요.
pause
