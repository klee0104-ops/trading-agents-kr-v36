$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Project Python is missing.' }
$taskName = 'TradingAgents-KR PublicQuotes'
$action = New-ScheduledTaskAction -Execute $python -Argument '-B -m tools.refresh_public_quotes' -WorkingDirectory $projectRoot
$trigger = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) -RepetitionInterval (New-TimeSpan -Minutes 5)
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Refresh public KOSPI quotes only every 5 minutes. No AI analysis, grading or trades.' -Force | Out-Null
Write-Output 'Registered: TradingAgents-KR PublicQuotes (5 minutes, current user logged in).'
