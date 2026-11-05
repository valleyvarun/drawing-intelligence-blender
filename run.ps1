param(
    [string]$Blender = '',
    [switch]$Background,
    [switch]$EnableEventSimulation,
    [string]$Profile = '',
    [string]$Script = '',
    [string]$Project = ''
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
if (-not $Blender) { $Blender = $env:DRAWING_BLENDER }
if (-not $Blender -and (Test-Path -LiteralPath (Join-Path $appRoot 'engine/blender.exe'))) {
    $Blender = Join-Path $appRoot 'engine/blender.exe'
}
if (-not $Blender) {
    $candidates = Get-ChildItem -LiteralPath 'C:/Program Files/Blender Foundation' -Directory -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -match '^Blender (\d+\.\d+)' -and [version]$Matches[1] -ge [version]'5.2' } |
        Sort-Object { [version]([regex]::Match($_.Name, '\d+\.\d+').Value) } -Descending
    foreach ($candidate in $candidates) {
        $executable = Join-Path $candidate.FullName 'blender.exe'
        if (Test-Path -LiteralPath $executable) { $Blender = $executable; break }
    }
}
if (-not $Blender -or -not (Test-Path -LiteralPath $Blender)) {
    throw 'Blender 5.2 or newer is required. Pass -Blender with the path to blender.exe.'
}
$runtime = Join-Path $appRoot 'runtime'
if ($Profile) {
    if ($Profile -notmatch '^[a-zA-Z0-9_-]+$') { throw 'Profile names can contain letters, numbers, underscores, and hyphens.' }
    $runtime = Join-Path $appRoot ".checks/profiles/$Profile"
}
foreach ($name in @('config', 'scripts', 'datafiles', 'extensions', 'temp', 'logs')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $runtime $name) | Out-Null
}
$env:BLENDER_USER_RESOURCES = $runtime
$env:BLENDER_USER_CONFIG = Join-Path $runtime 'config'
$env:BLENDER_USER_SCRIPTS = Join-Path $runtime 'scripts'
$env:BLENDER_USER_DATAFILES = Join-Path $runtime 'datafiles'
$env:BLENDER_USER_EXTENSIONS = Join-Path $runtime 'extensions'
$env:TEMP = Join-Path $runtime 'temp'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:DRAWING_APP_ROOT = $appRoot
$env:DRAWING_APP_CONFIG = Join-Path $runtime 'config'
$arguments = @('--factory-startup', '--disable-autoexec', '--python-exit-code', '1')
if ($EnableEventSimulation) { $arguments += '--enable-event-simulate' }
if ($Background) { $arguments += '--background' }
else { $arguments += @('--window-geometry', '20', '40', '1600', '900') }
if ($Project) { $arguments += (Resolve-Path -LiteralPath $Project).Path }
$arguments += @('--python', (Join-Path $appRoot 'app/bootstrap.py'))
if ($Script) { $arguments += @('--python', (Resolve-Path -LiteralPath $Script).Path) }
& $Blender @arguments
exit $LASTEXITCODE
