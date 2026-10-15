$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$runtime = Get-QQBotRuntimeConfig
$WatchdogScript = Join-Path $PSScriptRoot 'qqbot-watchdog.ps1'
$PowerShellExe = "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe"

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    throw 'config.local.toml is missing. Run scripts\initialize.ps1 first.'
}
if (-not (Test-Path -LiteralPath $RuntimeConfigPath)) {
    throw 'scripts\runtime.local.psd1 is missing. Copy runtime-config.example.psd1 and set local paths.'
}
if ([string]::IsNullOrWhiteSpace($runtime.NapCatRoot) -or [string]::IsNullOrWhiteSpace($runtime.QQPath)) {
    throw 'NapCatRoot and QQPath must be set in scripts\runtime.local.psd1.'
}
$requiredPaths = @(
    (Join-Path $runtime.NapCatRoot 'NapCatWinBootMain.exe'),
    (Join-Path $runtime.NapCatRoot 'NapCatWinBootHook.dll'),
    $runtime.QQPath
)
foreach ($requiredPath in $requiredPaths) {
    if (-not (Test-Path -LiteralPath $requiredPath -PathType Leaf)) {
        throw "Required startup file not found: $requiredPath"
    }
}

$action = New-ScheduledTaskAction -Execute $PowerShellExe -Argument (
    "-NoProfile -NonInteractive -WindowStyle Hidden -ExecutionPolicy Bypass -File `"$WatchdogScript`""
)
$trigger = New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME
$taskSettings = @{
    AllowStartIfOnBatteries = $true
    DontStopIfGoingOnBatteries = $true
    StartWhenAvailable = $true
    MultipleInstances = 'IgnoreNew'
    RestartCount = 999
    RestartInterval = (New-TimeSpan -Minutes 1)
    ExecutionTimeLimit = ([TimeSpan]::Zero)
}
$settings = New-ScheduledTaskSettingsSet @taskSettings

$register = @{
    TaskName = $TaskName
    Action = $action
    Trigger = $trigger
    Settings = $settings
    Description = 'Keeps the private NapCat + Codex QQ companion available after Windows sign-in.'
    Force = $true
}
Register-ScheduledTask @register | Out-Null

Start-ScheduledTask -TaskName $TaskName
Write-Output "Installed and started: $TaskName"
Write-Output "Project: $ProjectRoot"
