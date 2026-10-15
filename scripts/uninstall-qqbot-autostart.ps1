$ErrorActionPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'common.ps1')
Stop-ScheduledTask -TaskName $TaskName
Unregister-ScheduledTask -TaskName $TaskName -Confirm:$false
Get-CimInstance Win32_Process | Where-Object {
    $_.ProcessId -ne $PID -and $_.CommandLine -match 'qqbot-watchdog\.ps1'
} | ForEach-Object {
    Stop-Process -Id $_.ProcessId -Force
}
Write-Output "Removed: $TaskName"
