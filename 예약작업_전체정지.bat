@echo off
setlocal
cd /d "%~dp0"
echo TradingAgents 예약 작업을 모두 끕니다. 실행 중인 작업도 멈춥니다.
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ScheduledTask | Where-Object { $_.TaskName -like 'TradingAgents*' } | ForEach-Object { Stop-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath -ErrorAction SilentlyContinue; Disable-ScheduledTask -TaskName $_.TaskName -TaskPath $_.TaskPath | Out-Null; Write-Output ('정지: ' + $_.TaskName) }" > logs\scheduler_pause_result.txt 2>&1
powershell -NoProfile -ExecutionPolicy Bypass -Command "Get-ScheduledTask | Where-Object { $_.TaskName -like 'TradingAgents*' } | Select-Object TaskName, State | Format-Table -AutoSize | Out-String -Width 200" >> logs\scheduler_pause_result.txt 2>&1
type logs\scheduler_pause_result.txt
echo.
echo 완료. 이 창은 5초 뒤 닫힙니다.
timeout /t 5 >nul
