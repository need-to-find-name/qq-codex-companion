param(
    [int]$IntervalSeconds = 30
)

$ErrorActionPreference = 'Continue'
. (Join-Path $PSScriptRoot 'common.ps1')
$runtime = Get-QQBotRuntimeConfig
$BridgeExe = Get-QQBotBridgeExecutable
$WatchdogLog = Join-Path $ProjectRoot 'logs\watchdog.log'
$NapCatRoot = $runtime.NapCatRoot
$NapCatLauncher = if ($NapCatRoot) { Join-Path $NapCatRoot 'NapCatWinBootMain.exe' } else { '' }
$NapCatHook = if ($NapCatRoot) { Join-Path $NapCatRoot 'NapCatWinBootHook.dll' } else { '' }
$QQPath = $runtime.QQPath
$Mutex = New-Object System.Threading.Mutex($false, 'Local\QQCodexCompanionWatchdog')
$HasMutex = $false

function Write-WatchdogLog([string]$Message) {
    $directory = Split-Path -Parent $WatchdogLog
    if (-not (Test-Path -LiteralPath $directory)) {
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
    }
    $line = '{0} {1}' -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message
    Add-Content -LiteralPath $WatchdogLog -Value $line -Encoding UTF8
}

function Get-BotQQ {
    if (-not (Test-Path -LiteralPath $ConfigPath)) {
        return $null
    }
    $line = Get-Content -LiteralPath $ConfigPath -ErrorAction SilentlyContinue |
        Where-Object { $_ -match '^\s*bot_qq\s*=\s*\d+\s*$' } |
        Select-Object -First 1
    if ($line -match '(\d+)') {
        return $Matches[1]
    }
    return $null
}

function Ensure-Bridge {
    $listener = Get-PortListener $runtime.OneBotWsPort
    if ($null -ne $listener) {
        return
    }
    if ((Get-ExactProcess $BridgeExe).Count -gt 0) {
        return
    }
    if (-not (Test-Path -LiteralPath $BridgeExe)) {
        Write-WatchdogLog "Bridge executable is missing: $BridgeExe"
        return
    }
    Start-Process -FilePath $BridgeExe -WorkingDirectory $ProjectRoot -WindowStyle Hidden | Out-Null
    Write-WatchdogLog 'Started QQ Codex Companion.'
}

function Ensure-NapCat {
    if ($null -ne (Get-PortConnection $runtime.OneBotWsPort)) {
        return
    }
    if ((Get-ExactProcess $NapCatLauncher).Count -gt 0) {
        return
    }
    if ((Get-ExactProcess $QQPath).Count -gt 0) {
        Write-WatchdogLog 'QQ is running without the configured NapCat launcher; leaving it untouched to avoid closing an active QQ session.'
        return
    }
    foreach ($required in @($NapCatLauncher, $NapCatHook, $QQPath)) {
        if (-not (Test-Path -LiteralPath $required)) {
            Write-WatchdogLog "NapCat startup file is missing: $required"
            return
        }
    }
    $botQQ = Get-BotQQ
    if ([string]::IsNullOrWhiteSpace($botQQ)) {
        Write-WatchdogLog 'Cannot read bot_qq from config.local.toml.'
        return
    }

    $env:NAPCAT_PATCH_PACKAGE = Join-Path $NapCatRoot 'qqnt.json'
    $env:NAPCAT_LOAD_PATH = Join-Path $NapCatRoot 'loadNapCat.js'
    $env:NAPCAT_INJECT_PATH = $NapCatHook
    $env:NAPCAT_LAUNCHER_PATH = $NapCatLauncher
    $env:NAPCAT_MAIN_PATH = Join-Path $NapCatRoot 'napcat.mjs'
    $startNapCat = @{
        FilePath = $NapCatLauncher
        ArgumentList = @($QQPath, $NapCatHook, $botQQ)
        WorkingDirectory = $NapCatRoot
        WindowStyle = 'Hidden'
    }
    Start-Process @startNapCat | Out-Null
    Write-WatchdogLog 'Started NapCat with the saved QQ account for quick login.'
}

try {
    $HasMutex = $Mutex.WaitOne(0, $false)
    if (-not $HasMutex) {
        exit 0
    }
    Write-WatchdogLog 'Watchdog started.'
    while ($true) {
        Ensure-Bridge
        Start-Sleep -Seconds 2
        Ensure-NapCat
        Start-Sleep -Seconds ([Math]::Max(10, $IntervalSeconds))
    }
}
catch {
    Write-WatchdogLog ("Watchdog stopped after an unexpected error: " + $_.Exception.Message)
    throw
}
finally {
    if ($HasMutex) {
        $Mutex.ReleaseMutex()
    }
    $Mutex.Dispose()
}
