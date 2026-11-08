param(
    # Defaults to this checkout's private engine. An official runtime can also be copied and branded.
    [Alias('BlenderDirectory')][string]$EngineDirectory = '',
    [string]$Output = 'dist/3D-Drawing'
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$outputRoot = [IO.Path]::GetFullPath((Join-Path $appRoot $Output))
if (-not $outputRoot.StartsWith($appRoot + [IO.Path]::DirectorySeparatorChar, [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Package output must stay inside the application checkout.'
}
if (Test-Path -LiteralPath $outputRoot) {
    throw "$outputRoot already exists. Choose a new output folder."
}
if (-not $EngineDirectory) { $EngineDirectory = Join-Path $appRoot 'engine' }
$engineSource = (Resolve-Path -LiteralPath $EngineDirectory).Path
if (-not ((Test-Path -LiteralPath (Join-Path $engineSource 'Drawing Intelligence.exe')) -or
          (Test-Path -LiteralPath (Join-Path $engineSource 'blender.exe')))) {
    throw 'The engine folder must contain a complete Blender 5.2.2 runtime. Run scripts/setup-engine.ps1 first.'
}
if ($outputRoot.StartsWith($engineSource.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)) {
    throw 'Package output cannot be inside the source engine.'
}
$startup = 'app/template/startup/bl_app_templates_system/drawing_intelligence/startup.blend'
if (-not (Test-Path -LiteralPath (Join-Path $appRoot $startup))) { throw "Missing $startup" }
New-Item -ItemType Directory -Path $outputRoot | Out-Null
foreach ($name in @('app', 'assets', 'docs', 'scripts', 'tests', 'cmake', 'launcher', 'wizard/icon',
    'launch.cmd', 'run.ps1', 'README.md', 'THIRD_PARTY_NOTICES.md', 'pyproject.toml')) {
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
$engineRoot = Join-Path $outputRoot 'engine'
New-Item -ItemType Directory -Path $engineRoot | Out-Null
Get-ChildItem -LiteralPath $engineSource -Force | Copy-Item -Destination $engineRoot -Recurse
Add-Type -Path (Join-Path $appRoot 'launcher/AppIdentity.cs'), (Join-Path $appRoot 'launcher/EngineBrand.cs')
if (-not [DrawingIntelligence.EngineBrand]::IsBranded($engineRoot)) {
    [void][DrawingIntelligence.EngineBrand]::Apply($engineRoot, (Join-Path $appRoot 'wizard/icon/app.ico'))
}
& (Join-Path $appRoot 'launcher/build.ps1') -Output (Join-Path $outputRoot 'Drawing Intelligence.exe')
Write-Output "Standalone package created: $outputRoot"
