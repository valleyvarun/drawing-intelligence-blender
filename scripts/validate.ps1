param([string]$Blender = '', [switch]$UI)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$tests = @('test_drawing.py', 'test_navigation.py', 'test_project.py')
foreach ($testName in $tests) {
    $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $appRoot 'run.ps1'), '-Background', '-Profile', $testName.Replace('.py',''), '-Script', (Join-Path $appRoot "tests/$testName"))
    if ($Blender) { $arguments += @('-Blender', $Blender) }
    & powershell.exe @arguments
    if ($LASTEXITCODE -ne 0) { throw "Blender regression failed: $testName" }
}
if ($UI) {
    foreach ($testName in @('test_ui.py', 'test_ui_lifecycle.py')) {
        $arguments = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $appRoot 'run.ps1'), '-EnableEventSimulation', '-Profile', $testName.Replace('.py',''), '-Script', (Join-Path $appRoot "tests/$testName"))
        if ($Blender) { $arguments += @('-Blender', $Blender) }
        & powershell.exe @arguments
        if ($LASTEXITCODE -ne 0) { throw "Blender UI regression failed: $testName" }
    }
    foreach ($reportName in @('ui-input-report.json', 'ui-lifecycle-report.json')) {
        $report = Get-Content -LiteralPath (Join-Path $appRoot ".checks/$reportName") -Raw | ConvertFrom-Json
        if (-not $report.passed) { throw "UI assertions failed; inspect .checks/$reportName" }
    }
}
Write-Output 'Blender regression checks passed.'
