@echo off
setlocal
title TradingAgents-KR 아침 자동 실행 해제
powershell -NoProfile -ExecutionPolicy Bypass -Command "Unregister-ScheduledTask -TaskName 'TradingAgents-KR 아침분석' -Confirm:$false; Write-Host '[해제 완료] 아침 자동 실행이 해제되었습니다.'"
pause
