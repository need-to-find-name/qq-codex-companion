$ErrorActionPreference = 'Stop'
$ProjectRoot = Split-Path -Parent $PSScriptRoot
$ConfigPath = Join-Path $ProjectRoot 'config.local.toml'
$RuntimeConfigPath = Join-Path $PSScriptRoot 'runtime.local.psd1'

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    throw 'uv is required. Install it first: https://docs.astral.sh/uv/'
}

if (-not (Test-Path -LiteralPath $ConfigPath)) {
    Copy-Item -LiteralPath (Join-Path $ProjectRoot 'config.example.toml') -Destination $ConfigPath
    Write-Output 'Created config.local.toml. Fill in the OneBot token and QQ numbers before starting.'
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

Write-Output 'Dependencies are ready. Review config.local.toml, then run: uv run qq-codex-companion'
