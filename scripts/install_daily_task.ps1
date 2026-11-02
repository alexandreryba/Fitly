<#
Registers (or removes) a Windows scheduled task that runs the Fitly morning review.
Run it yourself, once, from PowerShell at the project root:
  .\scripts\install_daily_task.ps1                 # every weekday at 08:00
  .\scripts\install_daily_task.ps1 -Time 07:30 -EveryDay
  .\scripts\install_daily_task.ps1 -Remove
Logs: data\logs\morning_*.log. The task only reads public job boards; it never applies.
#>
param([string]$Time = "08:00", [switch]$EveryDay, [switch]$Remove)
$ErrorActionPreference = "Stop"
$TaskName = "Fitly Morning Review"
if ($Remove) {
    Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
    Write-Host "Removed scheduled task '$TaskName'."
    exit 0
}
$ProjectRoot = (Resolve-Path (Join-Path $PSScriptRoot "..")).Path
$Script = Join-Path $ProjectRoot "scripts\morning_scheduled.ps1"
$Action = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $ProjectRoot `
    -Argument "-NoProfile -ExecutionPolicy Bypass -File `"$Script`""
if ($EveryDay) {
    $Trigger = New-ScheduledTaskTrigger -Daily -At $Time
} else {
    $Trigger = New-ScheduledTaskTrigger -Weekly -DaysOfWeek Monday,Tuesday,Wednesday,Thursday,Friday -At $Time
}
$Settings = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Minutes 30)
Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings `
    -Description "Fitly: collect public offers and build the review queue (read-only)." -Force | Out-Null
Write-Host "Scheduled '$TaskName' at $Time. Logs: $ProjectRoot\data\logs"
