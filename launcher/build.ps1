param([string]$Output = '')
# Compiles Drawing Intelligence.exe with the C# compiler included in Windows (.NET Framework 4.x).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
if (-not $Output) { $Output = Join-Path $appRoot 'Drawing Intelligence.exe' }
$csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework64\v4.0.30319\csc.exe'
if (-not (Test-Path -LiteralPath $csc)) { $csc = Join-Path $env:WINDIR 'Microsoft.NET\Framework\v4.0.30319\csc.exe' }
if (-not (Test-Path -LiteralPath $csc)) { throw 'The .NET Framework 4.8 C# compiler was not found.' }
$icon = Join-Path $appRoot 'wizard\icon\app.ico'
$logo = Join-Path $appRoot 'wizard\icon\app.png'
New-Item -ItemType Directory -Force -Path (Split-Path -Parent $Output) | Out-Null
& $csc /nologo /warnaserror+ /optimize+ /target:winexe /platform:anycpu "/out:$Output" "/win32icon:$icon" `
    "/win32manifest:$(Join-Path $PSScriptRoot 'app.manifest')" `
    "/resource:$icon,DrawingIntelligence.app.ico" "/resource:$logo,DrawingIntelligence.app.png" `
    /r:System.dll /r:System.Drawing.dll /r:System.Windows.Forms.dll /r:System.Web.Extensions.dll `
    (Join-Path $PSScriptRoot 'Launcher.cs') (Join-Path $PSScriptRoot 'AppIdentity.cs') (Join-Path $appRoot 'app/EngineSafety.cs')
if ($LASTEXITCODE -ne 0) { throw 'The launcher did not compile.' }
Write-Output "Built $Output"
