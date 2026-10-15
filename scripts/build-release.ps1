param(
    [string]$OutputDirectory = ""
)

$ErrorActionPreference = 'Stop'
$ProjectRoot = [IO.Path]::GetFullPath((Split-Path -Parent $PSScriptRoot))
$PyProjectPath = Join-Path $ProjectRoot 'pyproject.toml'

if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $ProjectRoot 'dist'
} elseif (-not [IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory = Join-Path $ProjectRoot $OutputDirectory
}
$DistPath = [IO.Path]::GetFullPath($OutputDirectory)
New-Item -ItemType Directory -Path $DistPath -Force | Out-Null

$pyproject = Get-Content -Raw -LiteralPath $PyProjectPath
$versionMatch = [regex]::Match($pyproject, '(?m)^version\s*=\s*"([^"]+)"\s*$')
if (-not $versionMatch.Success) {
    throw 'Unable to read project.version from pyproject.toml.'
}
$Version = $versionMatch.Groups[1].Value

$pythonVersionText = Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot 'src\qq_codex_companion\__init__.py')
$pythonVersionMatch = [regex]::Match($pythonVersionText, '(?m)^__version__\s*=\s*"([^"]+)"\s*$')
$pluginVersion = (Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot 'plugin.json') | ConvertFrom-Json).version
$codexPluginVersion = (Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot '.codex-plugin\plugin.json') | ConvertFrom-Json).version
$declaredVersions = @($pythonVersionMatch.Groups[1].Value, $pluginVersion, $codexPluginVersion)
if ($declaredVersions | Where-Object { $_ -ne $Version }) {
    throw "Version mismatch. pyproject.toml is $Version; package and plugin manifests must match."
}

$changelog = Get-Content -Raw -LiteralPath (Join-Path $ProjectRoot 'CHANGELOG.md')
if ($changelog -notmatch [regex]::Escape("## [$Version]")) {
    throw "CHANGELOG.md has no section for version $Version."
}

$PackageName = "qq-codex-companion-$Version"
$StagingPath = [IO.Path]::GetFullPath((Join-Path $DistPath ".release-staging-$([guid]::NewGuid().ToString('N'))"))
$DistPrefix = $DistPath.TrimEnd([IO.Path]::DirectorySeparatorChar) + [IO.Path]::DirectorySeparatorChar
if (-not $StagingPath.StartsWith($DistPrefix, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Refusing to create a release staging directory outside the selected output directory.'
}
$PackagePath = Join-Path $StagingPath $PackageName

function Copy-ReleaseFile {
    param([Parameter(Mandatory)][string]$RelativePath)
    $source = Join-Path $ProjectRoot $RelativePath
    if (-not (Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Required release file not found: $RelativePath"
    }
    $destination = Join-Path $PackagePath $RelativePath
    New-Item -ItemType Directory -Path (Split-Path -Parent $destination) -Force | Out-Null
    Copy-Item -LiteralPath $source -Destination $destination
}

try {
    New-Item -ItemType Directory -Path $PackagePath -Force | Out-Null

    @(
        '.python-version'
        '.codex-plugin\plugin.json'
        'CHANGELOG.md'
        'CONTRIBUTING.md'
        'LICENSE'
        'README.md'
        'SECURITY.md'
        'config.example.toml'
        'plugin.json'
        'pyproject.toml'
        'uv.lock'
        'skills\qq-companion\SKILL.md'
        'workspace\AGENTS.md'
        'workspace\persona\.gitkeep'
    ) | ForEach-Object { Copy-ReleaseFile -RelativePath $_ }

    Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'src\qq_codex_companion') -File -Filter '*.py' |
        ForEach-Object {
            Copy-ReleaseFile -RelativePath ([IO.Path]::GetRelativePath($ProjectRoot, $_.FullName))
        }

    Get-ChildItem -LiteralPath (Join-Path $ProjectRoot 'scripts') -File -Filter '*.ps1' |
        Where-Object { $_.Name -ne 'runtime.local.psd1' } |
        ForEach-Object {
            Copy-ReleaseFile -RelativePath ([IO.Path]::GetRelativePath($ProjectRoot, $_.FullName))
        }

    $ZipPath = Join-Path $DistPath "$PackageName-windows.zip"
    Compress-Archive -LiteralPath $PackagePath -DestinationPath $ZipPath -CompressionLevel Optimal -Force

    $Hash = (Get-FileHash -LiteralPath $ZipPath -Algorithm SHA256).Hash.ToLowerInvariant()
    $ChecksumPath = "$ZipPath.sha256"
    Set-Content -LiteralPath $ChecksumPath -Value "$Hash  $([IO.Path]::GetFileName($ZipPath))" -Encoding ascii

    Write-Output "Release package: $ZipPath"
    Write-Output "SHA256: $Hash"
}
finally {
    if (Test-Path -LiteralPath $StagingPath) {
        Remove-Item -LiteralPath $StagingPath -Recurse -Force
    }
}
