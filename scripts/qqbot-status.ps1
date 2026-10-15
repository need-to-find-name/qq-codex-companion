$ErrorActionPreference = 'SilentlyContinue'
. (Join-Path $PSScriptRoot 'common.ps1')
$runtime = Get-QQBotRuntimeConfig
$BridgeExe = Get-QQBotBridgeExecutable
$NapCatExe = if ($runtime.NapCatRoot) { Join-Path $runtime.NapCatRoot 'NapCatWinBootMain.exe' } else { '' }

$bridge = Get-ExactProcess $BridgeExe
$napcat = Get-ExactProcess $NapCatExe
$qq = Get-ExactProcess $runtime.QQPath
$listener = Get-PortListener $runtime.OneBotWsPort
$connection = Get-PortConnection $runtime.OneBotWsPort
$codexChild = @(Get-CimInstance Win32_Process | Where-Object {
    $_.Name -ieq 'codex.exe' -and $_.CommandLine -match 'app-server --listen stdio://'
})
$task = Get-ScheduledTask -TaskName $TaskName -ErrorAction SilentlyContinue

[pscustomobject]@{
    '登录自启动任务' = if ($task) { $task.State } else { '未安装' }
    '守护进程'       = if (@(Get-CimInstance Win32_Process | Where-Object { $_.CommandLine -match 'qqbot-watchdog\.ps1' }).Count) { '运行中' } else { '未运行' }
    'Codex桥接进程'  = if ($bridge.Count) { '运行中' } else { '未运行' }
    'Codex应用服务'  = if ($codexChild.Count) { '运行中' } else { '未运行' }
    'OneBot WS监听'  = if ($listener) { "正常：$($runtime.OneBotWsPort) (PID $($listener.OwningProcess))" } else { '无' }
    'NapCat连接'     = if ($connection) { '已建立' } else { '未建立' }
    'NapCat启动器'   = if ($napcat.Count) { '运行中' } else { '未运行' }
    'QQ进程'         = if ($qq.Count) { '运行中' } else { '未运行' }
    '本地运行配置'   = if (Test-Path -LiteralPath $RuntimeConfigPath) { '已配置' } else { '未配置' }
} | Format-List

Write-Output '最近桥接日志：'
$bridgeLog = Join-Path $ProjectRoot 'logs\bridge.log'
if (Test-Path -LiteralPath $bridgeLog) { Get-Content -LiteralPath $bridgeLog -Tail 8 } else { Write-Output '暂无' }
Write-Output '最近守护日志：'
$watchdogLog = Join-Path $ProjectRoot 'logs\watchdog.log'
if (Test-Path -LiteralPath $watchdogLog) { Get-Content -LiteralPath $watchdogLog -Tail 8 } else { Write-Output '暂无' }
