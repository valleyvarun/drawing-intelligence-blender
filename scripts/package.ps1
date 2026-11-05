param([string]$BlenderDirectory = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$outputRoot = [IO.Path]::GetFullPath((Join-Path $appRoot 'dist/3D-Drawing'))
if (-not $outputRoot.StartsWith($appRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Package output must stay inside blender-app.'
}
if (Test-Path -LiteralPath $outputRoot) {
    throw 'dist/3D-Drawing already exists. Choose a new package version or remove the previous package manually.'
}
if ($BlenderDirectory -and -not (Test-Path -LiteralPath (Join-Path $BlenderDirectory 'blender.exe'))) {
    throw 'BlenderDirectory must contain blender.exe and its complete matching runtime.'
}
New-Item -ItemType Directory -Path $outputRoot | Out-Null
foreach ($name in @('app', 'assets', 'docs', 'scripts', 'tests', 'cmake', 'launch.cmd', 'run.ps1', 'README.md', 'THIRD_PARTY_NOTICES.md', 'pyproject.toml')) {
    $sourcePath = Join-Path $appRoot $name
    if (-not (Test-Path -LiteralPath $sourcePath)) { continue }
    if (Test-Path -LiteralPath $sourcePath -PathType Container) {
        foreach ($item in Get-ChildItem -LiteralPath $sourcePath -Recurse -File) {
            $relativePath = $item.FullName.Substring($appRoot.Length + 1)
            if ($relativePath -match '(^|[\\/])(\.checks|__pycache__)([\\/]|$)' -or $item.Extension -eq '.pyc') { continue }
            $destinationPath = Join-Path $outputRoot $relativePath
            New-Item -ItemType Directory -Force -Path (Split-Path -Parent $destinationPath) | Out-Null
            Copy-Item -LiteralPath $item.FullName -Destination $destinationPath
        }
    }
    else { Copy-Item -LiteralPath $sourcePath -Destination $outputRoot }
}
if ($BlenderDirectory) {
    $engineRoot = Join-Path $outputRoot 'engine'
    New-Item -ItemType Directory -Path $engineRoot | Out-Null
    Get-ChildItem -LiteralPath $BlenderDirectory -Force | Copy-Item -Destination $engineRoot -Recurse
}
Write-Output "Package created: $outputRoot"
if (-not $BlenderDirectory) { Write-Output 'This package uses an installed Blender. Pass -BlenderDirectory to bundle the engine.' }
