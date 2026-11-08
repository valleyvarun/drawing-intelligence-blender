Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$testRoot = Join-Path $appRoot '.checks/launcher-tests'
New-Item -ItemType Directory -Force -Path $testRoot | Out-Null
if (-not ('DrawingIntelligence.NativeMethods' -as [type])) {
    Add-Type -Path (Join-Path $appRoot 'launcher/Launcher.cs'), (Join-Path $appRoot 'launcher/AppIdentity.cs'),
        (Join-Path $appRoot 'app/EngineSafety.cs') -ReferencedAssemblies System.Drawing, System.Windows.Forms, System.Web.Extensions
}
$sessionType = [DrawingIntelligence.NativeMethods].Assembly.GetType('DrawingIntelligence.EngineSession')
$method = $sessionType.GetMethod('SavedGeometry', [Reflection.BindingFlags]'NonPublic,Static')
$preferences = Join-Path $testRoot 'preferences.json'
$invalidCases = @('null', '{', '[]', '{"window_geometry":[-2147483648,0,800,600]}',
    '{"window_geometry":[0,0,120,600]}', '{"window_geometry":[0,0,"800",600]}')
foreach ($json in $invalidCases) {
    [IO.File]::WriteAllText($preferences, $json)
    $arguments = [object[]]@([string]$preferences, $true)
    $result = $method.Invoke($null, $arguments)
    if ($null -ne $result) { throw "Invalid geometry was accepted: $json" }
}
[IO.File]::WriteAllText($preferences, '{"window_geometry":[-1200,40,1000,800],"window_maximized":false}')
$arguments = [object[]]@([string]$preferences, $true)
$result = $method.Invoke($null, $arguments)
if (($result -join ',') -ne '-1200,40,1000,800' -or $arguments[1] -ne $false) {
    throw 'Valid saved window geometry was not restored.'
}
Write-Output 'Launcher preference checks passed without starting the engine or opening windows.'
