@echo off
setlocal
cd /d "%~dp0"
echo TradingAgents 예약 작업을 다시 켭니다.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ScheduledTask | Where-Object { $_.TaskName -like 'TradingAgents*' } | ForEach-Object { Enable-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath | Out-Null; Write-Output ('재개: ' + $_.TaskName) }"
echo scheduler_pause.json 의 paused 값도 false 로 바꿔야 실제로 실행됩니다.
timeout /t 8 >nul
