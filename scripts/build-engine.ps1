param(
    [string]$Source = '',
    [string]$Dependencies = '',
    [string]$CMake = '',
    [ValidateRange(1, 64)][int]$Jobs = 2
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $Source) { $Source = Join-Path $appRoot 'vendor/blender' }
$Source = [IO.Path]::GetFullPath($Source)
$buildRoot = [IO.Path]::GetFullPath((Join-Path $appRoot 'build/native'))
$installRoot = Join-Path $appRoot 'build/native-install'
if (-not $buildRoot.StartsWith($appRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Build output must stay inside blender-app.'
}
if (-not (Test-Path -LiteralPath (Join-Path $Source 'source/blender/draw/engines/workbench'))) {
    throw 'Blender source is missing. Run scripts/setup-source.ps1 to prepare vendor/blender first.'
}
if (-not $Dependencies) { $Dependencies = Join-Path $Source 'lib/windows_x64' }
if (-not (Test-Path -LiteralPath (Join-Path $Dependencies 'python'))) {
    throw 'The matching Blender Windows dependency bundle is missing. Supply its directory with -Dependencies; this script does not modify the Blender checkout.'
}
if (-not $CMake) {
    $command = Get-Command cmake.exe -ErrorAction SilentlyContinue
    if ($command) { $CMake = $command.Source }
    else { $CMake = 'C:/Program Files/Microsoft Visual Studio/2022/Community/Common7/IDE/CommonExtensions/Microsoft/CMake/CMake/bin/cmake.exe' }
}
if (-not (Test-Path -LiteralPath $CMake)) { throw 'CMake and Visual Studio C++ build tools are required.' }
& $CMake -S $Source -B $buildRoot -G 'Visual Studio 17 2022' -A x64 "-DDRAWING_BLENDER_SOURCE=$Source" "-DLIBDIR=$Dependencies" "-DCMAKE_INSTALL_PREFIX=$installRoot" -C (Join-Path $appRoot 'cmake/drawing.cmake')
if ($LASTEXITCODE -ne 0) { throw 'Engine configuration failed.' }
& $CMake --build $buildRoot --config Release --target INSTALL --parallel $Jobs
if ($LASTEXITCODE -ne 0) { throw 'Engine build failed.' }
& (Join-Path $PSScriptRoot 'setup-engine.ps1') -From $installRoot -Force
if (-not $?) { throw 'Native engine preparation failed.' }
Write-Output "Native engine installed inside $(Join-Path $appRoot 'engine')"
