param([switch]$HourlyResearch)
$ErrorActionPreference = 'Stop'
$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'venv\Scripts\pythonw.exe'
if (-not (Test-Path -LiteralPath $python)) { throw 'Project Python is missing.' }
$identity = [System.Security.Principal.WindowsIdentity]::GetCurrent().Name
$principal = New-ScheduledTaskPrincipal -UserId $identity -LogonType Interactive -RunLevel Limited
$settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 3)
$taskName = 'TradingAgents-US PaperTick'
$action = New-ScheduledTaskAction -Execute $python -Argument '-B -m tools.weekday_task --job us_tick' -WorkingDirectory $projectRoot
. (Join-Path $PSScriptRoot 'weekday_schedule.ps1')
$trigger = New-WeekdayRepeatingTrigger -Minutes 5
Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $trigger -Principal $principal -Settings $settings -Description 'Read-only US IEX quote polling and local USD paper ledger; never broker orders or AI calls.' -Force | Out-Null
Write-Output 'Registered: TradingAgents-US PaperTick (5 minutes).'
if ($HourlyResearch) {
    $researchName = 'TradingAgents-US Research'
    $researchAction = New-ScheduledTaskAction -Execute $python -Argument '-B -m tools.weekday_task --job us_research' -WorkingDirectory $projectRoot
    $researchTrigger = New-WeekdayRepeatingTrigger -Minutes 60
    $researchSettings = New-ScheduledTaskSettingsSet -StartWhenAvailable -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries -MultipleInstances IgnoreNew -ExecutionTimeLimit (New-TimeSpan -Minutes 15)
    Register-ScheduledTask -TaskName $researchName -Action $researchAction -Trigger $researchTrigger -Principal $principal -Settings $researchSettings -Description 'US paper research; paid LLM calls occur only when US_AUTO_ANALYZE=true and NYSE session is open.' -Force | Out-Null
    Write-Output 'Registered: TradingAgents-US Research (hourly; paid model calls require US_AUTO_ANALYZE=true).'
}
