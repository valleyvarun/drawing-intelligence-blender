param([switch]$Integration, [string]$BlenderZip = '')
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '../..'))
$testRoot = Join-Path $appRoot '.checks/wizard-tests'
$setup = Join-Path $appRoot 'wizard/dist/Drawing-Intelligence-Blender-Setup.exe'
New-Item -ItemType Directory -Force -Path $testRoot | Out-Null
$env:TEMP = $testRoot
$env:TMP = $testRoot
$install = Join-Path $testRoot 'Install With Spaces'

function Invoke-Setup([string[]]$Arguments, [int]$Expected) {
    $process = Start-Process -FilePath $setup -ArgumentList $Arguments -WindowStyle Hidden -PassThru -Wait
    if ($process.ExitCode -ne $Expected) { throw "Expected exit $Expected; got $($process.ExitCode): $Arguments" }
}
function Assert-File([string]$Path) {
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) { throw "Missing expected file: $Path" }
}

# Invalid inputs must not install anything.
Invoke-Setup @('/silent') 4
Invoke-Setup @('/silent', '/unknown') 2
Invoke-Setup @('/silent', '/accepteula', '/dir=C:\') 2
Invoke-Setup @('/silent', '/accepteula', '/dir=relative') 2
$occupied = Join-Path $testRoot 'Occupied'
New-Item -ItemType Directory -Force -Path $occupied | Out-Null
[IO.File]::WriteAllText((Join-Path $occupied 'keep.txt'), 'keep')
Invoke-Setup @('/silent', '/accepteula', ('/dir="' + $occupied + '"')) 2
Assert-File (Join-Path $occupied 'keep.txt')
$bad = Join-Path $testRoot 'bad.zip'
[IO.File]::WriteAllText($bad, 'invalid engine')
Invoke-Setup @('/silent', '/accepteula', ('/dir="' + $install + '"'), ('/blenderzip="' + $bad + '"')) 3
if (Test-Path -LiteralPath $install) { throw 'Failed download created an installation.' }
if ((Get-Content -Raw (Join-Path $testRoot 'Drawing-Intelligence-Blender-Setup.log')) -notmatch 'checksum did not match') {
    throw 'Checksum mismatch was not reported.'
}
Write-Output 'Passed invalid-option, path, occupied-folder, and checksum checks.'
if (-not $Integration) { exit 0 }

# Full integration uses only a fresh test installation and its own product identity.
$key = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\valleyvarun.drawing-intelligence-blender'
$startLink = Join-Path ([Environment]::GetFolderPath('Programs')) 'Drawing Intelligence (Blender).lnk'
$desktopLink = Join-Path ([Environment]::GetFolderPath('DesktopDirectory')) 'Drawing Intelligence (Blender).lnk'
if ((Test-Path $key) -or (Test-Path -LiteralPath $startLink) -or (Test-Path -LiteralPath $desktopLink)) {
    throw 'An existing product registration/shortcut must not be changed by this test.'
}
if (-not $BlenderZip) { $BlenderZip = Join-Path $appRoot '.checks/blender-5.2.2-windows-x64.zip' }
Assert-File $BlenderZip
$source = Join-Path $testRoot ('source-' + [guid]::NewGuid().ToString('N'))
New-Item -ItemType Directory -Path $source | Out-Null
foreach ($name in @('app', 'launch.cmd', 'run.ps1', 'README.md', 'THIRD_PARTY_NOTICES.md')) {
    $sourcePath = Join-Path $appRoot $name
    if (Test-Path -LiteralPath $sourcePath -PathType Container) {
        & robocopy.exe $sourcePath (Join-Path $source $name) /E /XD .checks __pycache__ /XF *.pyc /R:1 /W:1 /NFL /NDL /NJH /NJS /NP
        if ($LASTEXITCODE -ge 8) { throw 'Could not prepare the source archive.' }
    }
    else { Copy-Item -LiteralPath $sourcePath -Destination $source }
}
Add-Type -AssemblyName System.IO.Compression.FileSystem
$sourceZip = $source + '.zip'
[IO.Compression.ZipFile]::CreateFromDirectory($source, $sourceZip)
$arguments = @('/silent', '/accepteula', '/nodesktop', ('/dir="' + $install + '"'), ('/sourcezip="' + $sourceZip + '"'), ('/blenderzip="' + $BlenderZip + '"'))
try {
    Invoke-Setup $arguments 0
    Assert-File (Join-Path $install 'engine/blender.exe')
    Assert-File (Join-Path $install 'app/bootstrap.py')
    Assert-File (Join-Path $install 'uninstall.exe')
    Assert-File $startLink
    if (Test-Path -LiteralPath $desktopLink) { throw '/nodesktop created a desktop shortcut.' }
    if ((Get-ItemProperty -LiteralPath $key).InstallLocation -ne $install) { throw 'Incorrect uninstall registration.' }
    $shell = New-Object -ComObject WScript.Shell
    $shortcut = $shell.CreateShortcut($startLink)
    if ($shortcut.TargetPath -ne (Join-Path $install 'launch.cmd')) { throw 'Incorrect shortcut target.' }
    New-Item -ItemType Directory -Path (Join-Path $install 'runtime') | Out-Null
    [IO.File]::WriteAllText((Join-Path $install 'runtime/keep.txt'), 'preferences')
    [IO.File]::WriteAllText((Join-Path $install 'keep.blend'), 'test project')
    [IO.File]::WriteAllText((Join-Path $install 'app/obsolete.txt'), 'old application file')
    Invoke-Setup $arguments 0
    if (Test-Path -LiteralPath (Join-Path $install 'app/obsolete.txt')) { throw 'Update kept stale application files.' }
    Assert-File (Join-Path $install 'runtime/keep.txt')
    Assert-File (Join-Path $install 'keep.blend')
    # A failed update must leave the installed app intact.
    Invoke-Setup @('/silent', '/accepteula', ('/dir="' + $install + '"'), ('/blenderzip="' + $bad + '"')) 3
    Assert-File (Join-Path $install 'engine/blender.exe')
    Assert-File (Join-Path $install 'app/bootstrap.py')
    & powershell.exe -NoProfile -ExecutionPolicy Bypass -File (Join-Path $install 'run.ps1') -Background -Profile wizard-smoke
    if ($LASTEXITCODE -ne 0) { throw 'Installed application could not launch.' }
}
finally {
    if (Test-Path -LiteralPath (Join-Path $install '.drawing-intelligence-blender-install')) {
        Invoke-Setup @('/uninstall', '/silent', ('/dir="' + $install + '"')) 0
    }
}
Assert-File (Join-Path $install 'runtime/keep.txt')
Assert-File (Join-Path $install 'keep.blend')
if ((Test-Path $key) -or (Test-Path -LiteralPath $startLink) -or (Test-Path -LiteralPath (Join-Path $install 'engine'))) {
    throw 'Uninstall left managed engine files or integration behind.'
}
Write-Output 'Passed install, shortcut/registration, update, failed-update preservation, launch, and uninstall checks.'
