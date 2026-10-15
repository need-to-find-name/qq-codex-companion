$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ConfigPath = Join-Path $ProjectRoot 'config.local.toml'
$RuntimeConfigPath = Join-Path $PSScriptRoot 'runtime.local.psd1'

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required. Install it first: https://docs.astral.sh/uv/'
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    $template = Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot 'config.example.toml')
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = [BitConverter]::ToString($bytes).Replace('-', '')
    $localConfig = $template.Replace(
        'access_token = "replace-me"',
        "access_token = `"$token`""
    )
    Set-Content -LiteralPath $ConfigPath -Value $localConfig -Encoding utf8 -NoNewline
    Write-Output 'Created config.local.toml with a random OneBot token. Fill in the QQ numbers before starting.'
}

if (-not (Test-Path -LiteralPath $RuntimeConfigPath)) {
    Copy-Item -LiteralPath (Join-Path $PSScriptRoot 'runtime-config.example.psd1') -Destination $RuntimeConfigPath
    Write-Output 'Created scripts/runtime.local.psd1. Set the NapCat and QQ paths before enabling autostart.'
}

Push-Location $ProjectRoot
try {
    uv sync
}
finally {
    Pop-Location
}

Write-Output ''
Write-Output 'Initialization complete.'
Write-Output '1. Edit config.local.toml: set the OneBot token, bot QQ, and trusted QQ account.'
Write-Output '2. Configure the same reverse WebSocket URL and token in NapCat.'
Write-Output '3. Check the environment: uv run qq-codex-doctor'
Write-Output '4. Start the bridge: uv run qq-codex-companion'
