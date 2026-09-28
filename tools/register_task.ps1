param(
    [Parameter(Mandatory=$true)][string]$BatPath,
    [string]$Time = "07:30",
    [string]$TaskName = "TradingAgents-KR 아침분석"
)
$ErrorActionPreference = "Stop"
if (-not (Test-Path $BatPath)) { Write-Host "[오류] 파일이 없습니다: $BatPath"; exit 1 }

$action  = New-ScheduledTaskAction -Execute "cmd.exe" -Argument "/c `"$BatPath`"" -WorkingDirectory (Split-Path $BatPath)
$trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $Time
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -WakeToRun `
    -ExecutionTimeLimit (New-TimeSpan -Hours 1) -MultipleInstances IgnoreNew `
    -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
# 로그인된 사용자 세션에서 실행 -> 알림/브라우저 열기 가능. 비밀번호 저장 없음.
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited

Register-ScheduledTask -TaskName $TaskName -Action $action -Trigger $trigger -Settings $settings -Principal $principal -Force | Out-Null
$t = Get-ScheduledTask -TaskName $TaskName
$info = $t | Get-ScheduledTaskInfo
Write-Host "[등록 완료] '$TaskName'  월~금 $Time"
Write-Host "  다음 실행 예정: $($info.NextRunTime)"
Write-Host "  해제: 자동실행_해제.bat  /  확인: 작업 스케줄러(taskschd.msc)"
