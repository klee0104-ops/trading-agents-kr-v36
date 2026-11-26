# Shared by registration commands: repeat from 04:00 until 16:00 KST on weekdays.
function New-WeekdayRepeatingTrigger {
    param([Parameter(Mandatory=$true)][ValidateRange(1,1440)][int]$Minutes)
    $trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At '04:00'
    $trigger.StartBoundary = [DateTimeOffset]::UtcNow.ToOffset([TimeSpan]::FromHours(9)).ToString('yyyy-MM-dd') + 'T04:00:00+09:00'
    $repeat = New-ScheduledTaskTrigger -Once -At '04:00' -RepetitionInterval (New-TimeSpan -Minutes $Minutes) -RepetitionDuration (New-TimeSpan -Hours 11 -Minutes 59 -Seconds 59)
    $repeat.Repetition.StopAtDurationEnd = $true
    $trigger.Repetition = $repeat.Repetition
    return $trigger
}
