param(
    [int]$TimeoutSeconds = 75
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'common.ps1')
$runtime = Get-QQBotRuntimeConfig
$BridgeExe = Get-QQBotBridgeExecutable
$all = @(Get-CimInstance Win32_Process)
$roots = @($all | Where-Object { $_.ExecutablePath -eq $BridgeExe })

foreach ($root in $roots) {
    $ids = [System.Collections.Generic.List[int]]::new()
    $ids.Add([int]$root.ProcessId)
    do {
        $before = $ids.Count
        foreach ($process in $all) {
            if (
                $ids.Contains([int]$process.ParentProcessId) -and
                -not $ids.Contains([int]$process.ProcessId)
            ) {
                $ids.Add([int]$process.ProcessId)
            }
        }
    } while ($ids.Count -gt $before)
    $ids | Sort-Object -Descending | ForEach-Object {
        Stop-Process -Id $_ -Force -ErrorAction SilentlyContinue
    }
}

$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue
if ($null -eq $task) {
    & (Join-Path $PSScriptRoot 'install-qqbot-autostart.ps1')
} elseif ($task.State -ne 'Running') {
    Start-ScheduledTask -TaskName $TaskName
}

Write-Output '已请求重启 Codex 桥接，正在等待 NapCat 重新连接……'
$deadline = (Get-Date).AddSeconds([Math]::Max(15, $TimeoutSeconds))
$ready = $false
do {
    Start-Sleep -Seconds 3
    $listener = Get-PortListener $runtime.OneBotWsPort
    $connection = Get-PortConnection $runtime.OneBotWsPort
    $ready = $null -ne $listener -and $null -ne $connection
} while (-not $ready -and (Get-Date) -lt $deadline)

if (-not $ready) {
    Write-Error '等待超时。请运行 qqbot-status.ps1 查看是桥接、NapCat 还是 QQ 登录未恢复。'
    exit 1
}

Write-Output "重启完成：$($runtime.OneBotWsPort) 已监听，NapCat 已重新连接。"
