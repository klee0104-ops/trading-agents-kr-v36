@echo off
setlocal
cd /d "%~dp0"
title TradingAgents-KR 마감 채점 자동 등록
echo 매주 월~금 15:45 에 채점 + 복기를 자동 실행하도록 등록합니다 (장 마감 직후).
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0tools\register_task.ps1" -BatPath "%~dp0복기_자동.bat" -Time "15:45" -TaskName "TradingAgents-KR 마감채점"
pause
