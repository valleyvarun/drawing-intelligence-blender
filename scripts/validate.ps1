param(
    [string]$Engine = '',
    [switch]$EngineTests,
    [switch]$UI,
    [string]$Python = '',
    [string]$Node = '',
    [ValidateRange(1, 64)][int]$Threads = 2,
    [ValidateRange(1, 3600)][int]$TimeoutSeconds = 180
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $Python) {
    $Python = Join-Path $appRoot 'engine/5.2/python/bin/python.exe'
    if (-not (Test-Path -LiteralPath $Python)) {
        $command = Get-Command python.exe -ErrorAction SilentlyContinue
        if ($command) { $Python = $command.Source }
    }
}
if (-not (Test-Path -LiteralPath $Python -PathType Leaf)) {
    throw 'A Python 3.11+ interpreter is required. Supply its path with -Python; validation does not download tools.'
}
if (-not $Node) {
    $command = Get-Command node.exe -ErrorAction SilentlyContinue
    if ($command) { $Node = $command.Source }
    else { $Node = Join-Path $env:ProgramFiles 'nodejs/node.exe' }
}
$pyright = Join-Path $appRoot '.checks/python-tools/pyright/dist/index.js'
if (-not (Test-Path -LiteralPath $pyright)) {
    $pyright = & $Python -c "import pyright; from pathlib import Path; print(Path(pyright.__file__).parent / 'dist' / 'index.js')"
}
if (-not $pyright -or -not (Test-Path -LiteralPath $pyright) -or -not (Test-Path -LiteralPath $Node)) {
    throw 'Locally installed Pyright and Node.js are required. Supply Node with -Node; validation does not download tools.'
}
$previousPythonPath = $env:PYTHONPATH
$previousRayonThreads = $env:RAYON_NUM_THREADS
$toolRoot = Join-Path $appRoot '.checks/python-tools'
if (Test-Path -LiteralPath $toolRoot) {
    $env:PYTHONPATH = $toolRoot + $(if ($previousPythonPath) { [IO.Path]::PathSeparator + $previousPythonPath } else { '' })
}
$env:RAYON_NUM_THREADS = '2'
Push-Location -LiteralPath $appRoot
try {
    & $Python scripts/static_check.py
    if ($LASTEXITCODE -ne 0) { throw 'Python syntax checks failed.' }
    & $Python -m black --check --workers 1 app tests scripts
    if ($LASTEXITCODE -ne 0) { throw 'Python formatting checks failed.' }
    & $Python -m ruff check app tests scripts
    if ($LASTEXITCODE -ne 0) { throw 'Python lint checks failed.' }
    foreach ($config in @('pyproject.toml', '.checks/editor-pyrightconfig.json')) {
        & $Node --max-old-space-size=1024 $pyright --project $config
        if ($LASTEXITCODE -ne 0) { throw "Python type checks failed: $config" }
    }
    foreach ($test in @('test_desktop_lifecycle.py', 'test_touch_lifecycle.py', 'test_layout_lifecycle.py', 'test_fly_guides.py', 'test_icon_rendering.py', 'test_zoom_navigation.py', 'test_stroke_updates.py', 'test_pencil_toolbar.py', 'test_axis_spokes.py', 'test_pipe_editing.py', 'test_curve_fill.py', 'test_fill_process.py', 'test_fill_viewport.py', 'test_api_registry.py', 'test_agent_cli.py', 'test_agent_chat.py', 'test_agent_mcp.py', 'test_shortcuts.py', 'test_print_field.py', 'test_extrusion.py', 'test_erase_geometry.py', 'test_sketch_surface.py')) {
        & $Python (Join-Path $appRoot "tests/$test")
        if ($LASTEXITCODE -ne 0) { throw "Lightweight regression failed: $test" }
    }
    & (Join-Path $appRoot 'launcher/tests/validate.ps1')
    if (-not $?) { throw 'Launcher checks failed.' }
    Write-Output 'Static and lightweight checks passed. Blender has not been started.'
    if ($EngineTests -or $UI) {
        $lockPath = Join-Path $appRoot '.checks/engine-tests.lock'
        try { $testLock = [IO.File]::Open($lockPath, 'OpenOrCreate', 'ReadWrite', 'None') }
        catch [IO.IOException] { throw 'Another engine validation is running in this checkout.' }
        try {
            $tests = @('test_drawing.py', 'test_navigation.py', 'test_project.py', 'test_agent_native.py', 'test_sketch_mesh_native.py')
            if ($UI) { $tests += @('test_ui.py', 'test_ui_lifecycle.py', 'test_sketch_mesh_ui.py', 'test_cursor_views_ui.py') }
            foreach ($testName in $tests) {
                $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $appRoot 'run.ps1'),
                    '-ProfileName', $testName.Replace('.py', ''), '-Script', (Join-Path $appRoot "tests/$testName"),
                    '-Threads', $Threads.ToString(), '-TimeoutSeconds', $TimeoutSeconds.ToString())
                if ($testName -match '(^test_ui|_ui\.py$)') { $arguments += '-EnableEventSimulation' }
                else { $arguments += '-Background' }
                if ($Engine) { $arguments += @('-Engine', $Engine) }
                & powershell.exe @arguments
                if ($LASTEXITCODE -ne 0) { throw "Engine regression failed: $testName" }
            }
            if ($UI) {
                foreach ($reportName in @('ui-input-report.json', 'ui-lifecycle-report.json', 'sketch-mesh-ui-report.json', 'cursor-views-ui-report.json')) {
                    $report = Get-Content -LiteralPath (Join-Path $appRoot ".checks/$reportName") -Raw | ConvertFrom-Json
                    if (-not $report.passed) { throw "UI assertions failed; inspect .checks/$reportName" }
                }
            }
        }
        finally { $testLock.Dispose() }
        Write-Output 'Requested engine regression checks passed.'
    }
}
finally {
    Pop-Location
    $env:PYTHONPATH = $previousPythonPath
    $env:RAYON_NUM_THREADS = $previousRayonThreads
}
