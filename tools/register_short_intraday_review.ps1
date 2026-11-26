$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Project Python is missing.' }
$taskName = 'TradingAgents-KR ShortIntradayReview'
$action = New-ScheduledTaskAction -Execute $python -Argument '-B -m tools.weekday_task --job short_review' -WorkingDirectory $projectRoot
. (Join-Path $PSScriptRoot 'weekday_schedule.ps1')
$trigger = New-WeekdayRepeatingTrigger -Minutes 5
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 5)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Watch fresh KOSPI200/ETF quotes and public headlines every five minutes; at most two cited two-role AI reviews per KRX session. Paper only.' -Force | Out-Null
Write-Output 'Registered: TradingAgents-KR ShortIntradayReview (5-minute watcher, max 2 reviewed events/day).'
