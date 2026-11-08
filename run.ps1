param(
    # Development and test entry point. Users start "Drawing Intelligence.exe" (see launch.cmd).
    [string]$Engine = '',
    [switch]$Background,
    [switch]$EnableEventSimulation,
    [string]$ProfileName = '',
    [string]$Script = '',
    [string]$Project = '',
    [ValidateRange(1, 64)][int]$Threads = 2,
    [ValidateRange(0, 3600)][int]$TimeoutSeconds = 0,
    # Start from the engine's factory screen instead of the application's startup file.
    [switch]$NoTemplate
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$appRoot = $PSScriptRoot
if (-not $Engine) { $Engine = Join-Path $appRoot 'engine\Drawing Intelligence.exe' }
if (-not (Test-Path -LiteralPath $Engine)) {
    throw 'The drawing engine is missing from engine\. Run scripts\setup-engine.ps1 first.'
}
if (-not ('DrawingIntelligence.EngineSafety' -as [type])) {
    Add-Type -Path (Join-Path $appRoot 'app/EngineSafety.cs')
}
[DrawingIntelligence.EngineSafety]::EnsureThumbnailDirectory()
$runtime = Join-Path $appRoot 'runtime'
if ($ProfileName) {
    if ($ProfileName -notmatch '^[a-zA-Z0-9_-]+$') { throw 'Profile names can contain letters, numbers, underscores, and hyphens.' }
    $runtime = Join-Path $appRoot ".checks/profiles/$ProfileName"
}
foreach ($name in @('config', 'scripts', 'datafiles', 'extensions', 'temp', 'logs')) {
    New-Item -ItemType Directory -Force -Path (Join-Path $runtime $name) | Out-Null
}
# Keep in sync with launcher/Launcher.cs.
$env:BLENDER_USER_RESOURCES = $runtime
$env:BLENDER_USER_CONFIG = Join-Path $runtime 'config'
$env:BLENDER_USER_SCRIPTS = Join-Path $runtime 'scripts'
$env:BLENDER_USER_DATAFILES = Join-Path $runtime 'datafiles'
$env:BLENDER_USER_EXTENSIONS = Join-Path $runtime 'extensions'
$env:BLENDER_SYSTEM_SCRIPTS = Join-Path $appRoot 'app/template'
$env:TEMP = Join-Path $runtime 'temp'
$env:TMP = $env:TEMP
$env:PYTHONDONTWRITEBYTECODE = '1'
$env:DRAWING_APP_ROOT = $appRoot
$env:DRAWING_APP_CONFIG = Join-Path $runtime 'config'
$arguments = @('--factory-startup', '--disable-autoexec', '--python-exit-code', '1', '--threads', $Threads.ToString())
if (-not $NoTemplate) { $arguments += @('--app-template', 'drawing_intelligence') }
if ($EnableEventSimulation) { $arguments += '--enable-event-simulate' }
if ($Background) { $arguments += '--background' }
else { $arguments += @('--window-geometry', '20', '40', '1600', '900') }
if ($Project) { $arguments += (Resolve-Path -LiteralPath $Project).Path }
$arguments += @('--python', (Join-Path $appRoot 'app/bootstrap.py'))
if ($Script) { $arguments += @('--python', (Resolve-Path -LiteralPath $Script).Path) }
if ($TimeoutSeconds -gt 0) {
    # Quote Windows process arguments without passing them through a command shell.
    $quoted = foreach ($argument in $arguments) {
        if ($argument.Length -gt 0 -and $argument -notmatch '[\s"]') { $argument }
        else { '"' + [regex]::Replace([regex]::Replace($argument, '(\\*)"', '$1$1\"'), '(\\+)$', '$1$1') + '"' }
    }
    $stdout = Join-Path $runtime 'logs/test-output.log'
    $stderr = Join-Path $runtime 'logs/test-error.log'
    $windowStyle = if ($Background) { 'Hidden' } else { 'Normal' }
    $process = Start-Process -FilePath $Engine -ArgumentList $quoted -WorkingDirectory $appRoot -WindowStyle $windowStyle `
        -RedirectStandardOutput $stdout -RedirectStandardError $stderr -PassThru
    # Keep the handle open so Windows PowerShell retains the exit code after waiting.
    $processHandle = $process.Handle
    try {
        if (-not $process.WaitForExit($TimeoutSeconds * 1000)) {
            throw "Engine test exceeded $TimeoutSeconds seconds and was stopped. Logs: $($runtime)\logs"
        }
        $exitCode = $process.ExitCode
    }
    finally {
        if (-not $process.HasExited) { $process.Kill(); [void]$process.WaitForExit(5000) }
        $process.Dispose()
        foreach ($log in @($stdout, $stderr)) {
            if (Test-Path -LiteralPath $log) { Get-Content -LiteralPath $log -Tail 80 | Out-Host }
        }
    }
    exit $exitCode
}
# The engine is a windows-subsystem program; reading its output makes PowerShell wait for it.
& $Engine @arguments | Out-Host
exit $LASTEXITCODE
