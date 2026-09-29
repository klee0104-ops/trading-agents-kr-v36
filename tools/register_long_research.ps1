$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Project Python is missing.' }
$taskName = 'TradingAgents-KR LongResearch'
$action = New-ScheduledTaskAction -Execute $python -Argument '-B -m tools.long_research --cycle' -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 60)
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 10)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Collect US-edition news hourly, all sessions. Weekly long-horizon review up to three model calls. No live orders.' -Force | Out-Null
Write-Output 'Registered: TradingAgents-KR LongResearch (60 minutes, current user logged in).'
