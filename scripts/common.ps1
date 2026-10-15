$Script:ProjectRoot = Split-Path -Parent $PSScriptRoot
$Script:ConfigPath = Join-Path $Script:ProjectRoot 'config.local.toml'
$Script:RuntimeConfigPath = Join-Path $PSScriptRoot 'runtime.local.psd1'
$Script:TaskName = 'QQ Codex Companion Watchdog'

function Get-QQBotRuntimeConfig {
    $values = @{
        NapCatRoot = ''
        QQPath = ''
        OneBotWsPort = 8643
    }
    if (Test-Path -LiteralPath $Script:RuntimeConfigPath) {
        $localValues = Import-PowerShellDataFile -LiteralPath $Script:RuntimeConfigPath
        foreach ($name in $values.Keys) {
            if ($null -ne $localValues[$name]) {
                $values[$name] = $localValues[$name]
            }
        }
    }
    [pscustomobject]$values
}

function Get-QQBotBridgeExecutable {
    Join-Path $Script:ProjectRoot '.venv\Scripts\qq-codex-companion.exe'
}

function Get-ExactProcess([string]$ExecutablePath) {
    if ([string]::IsNullOrWhiteSpace($ExecutablePath)) {
        return @()
    }
    @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.ExecutablePath -eq $ExecutablePath
    })
}

function Get-PortListener([int]$Port) {
    Get-NetTCPConnection -State Listen -LocalPort $Port -ErrorAction SilentlyContinue |
        Select-Object -First 1
}

function Get-PortConnection([int]$Port) {
    Get-NetTCPConnection -State Established -ErrorAction SilentlyContinue | Where-Object {
        $_.LocalPort -eq $Port -or $_.RemotePort -eq $Port
    } | Select-Object -First 1
}
