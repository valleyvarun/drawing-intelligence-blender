param(
    # Official Blender 5.2.2 Windows x64 ZIP: a local path or an https URL. Its SHA-256 is always checked.
    [string]$BlenderZip = '',
    # Copy an existing Blender 5.2.2 folder instead of using the official ZIP.
    [string]$From = '',
    # Replace an engine that is already installed.
    [switch]$Force
)
# Installs the private drawing engine in engine\ and builds Drawing Intelligence.exe.
# Nothing outside this folder is used once setup finishes.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$engine = Join-Path $appRoot 'engine'
$version = '5.2.2'
$officialUrl = 'https://download.blender.org/release/Blender5.2/blender-5.2.2-windows-x64.zip'
# https://download.blender.org/release/Blender5.2/blender-5.2.2.sha256
$officialSha256 = '3849d17a682cba006075aaa3f3597ecb5c9c30ec31035b2e092c53e40679b535'
$icon = Join-Path $appRoot 'wizard\icon\app.ico'
Add-Type -Path (Join-Path $appRoot 'launcher\AppIdentity.cs'), (Join-Path $appRoot 'launcher\EngineBrand.cs')

function Test-Inside([string]$Path, [string]$Parent) {
    $full = [IO.Path]::GetFullPath($Path).TrimEnd('\')
    return $full.StartsWith($Parent.TrimEnd('\') + '\', [StringComparison]::OrdinalIgnoreCase)
}

if ((Test-Path -LiteralPath $engine) -and -not $Force) {
    if ([DrawingIntelligence.EngineBrand]::IsBranded($engine)) {
        Write-Output 'The engine is already installed.'
    }
    elseif ((Test-Path -LiteralPath (Join-Path $engine 'Drawing Intelligence.exe')) -or (Test-Path -LiteralPath (Join-Path $engine 'blender.exe'))) {
        Write-Output 'Updating the engine branding'
        [void][DrawingIntelligence.EngineBrand]::Apply($engine, $icon)
    }
    else {
        throw "engine\ exists but does not contain an engine. Run again with -Force to replace it."
    }
}
else {
    $staging = Join-Path $appRoot 'build\engine-staging'
    if (-not (Test-Inside $staging $appRoot)) { throw 'Staging must stay inside the application folder.' }
    if (Test-Path -LiteralPath $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
    New-Item -ItemType Directory -Path $staging | Out-Null
    if ($From) {
        $source = (Resolve-Path -LiteralPath $From).Path
        $sourceExe = Join-Path $source 'blender.exe'
        if (-not (Test-Path -LiteralPath $sourceExe)) { throw "$source does not contain blender.exe." }
        $reported = (& $sourceExe --version 2>$null | Select-Object -First 1)
        if ($reported -notmatch "^Blender $([regex]::Escape($version))\b") { throw "Expected Blender $version, found: $reported" }
        Write-Output "Copying Blender $version from $source"
        $unpacked = Join-Path $staging 'engine'
        Copy-Item -LiteralPath $source -Destination $unpacked -Recurse
    }
    else {
        $zip = $BlenderZip
        if (-not $zip -or $zip -match '^https://') {
            $downloads = Join-Path $appRoot 'build\downloads'
            New-Item -ItemType Directory -Force -Path $downloads | Out-Null
            $cached = Join-Path $downloads "blender-$version-windows-x64.zip"
            if (-not (Test-Path -LiteralPath $cached)) {
                $address = if ($zip) { $zip } else { $officialUrl }
                Write-Output "Downloading $address"
                [Net.ServicePointManager]::SecurityProtocol = [Net.ServicePointManager]::SecurityProtocol -bor [Net.SecurityProtocolType]::Tls12
                $partial = "$cached.part"
                (New-Object Net.WebClient).DownloadFile($address, $partial)
                Move-Item -LiteralPath $partial -Destination $cached -Force
            }
            $zip = $cached
        }
        $zip = (Resolve-Path -LiteralPath $zip).Path
        Write-Output 'Verifying the Blender SHA-256 checksum'
        $hash = (Get-FileHash -LiteralPath $zip -Algorithm SHA256).Hash.ToLowerInvariant()
        if ($hash -ne $officialSha256) { throw "Checksum mismatch for $zip. Delete it and run setup again." }
        Write-Output 'Unpacking'
        Add-Type -AssemblyName System.IO.Compression.FileSystem
        [IO.Compression.ZipFile]::ExtractToDirectory($zip, $staging)
        $folders = @(Get-ChildItem -LiteralPath $staging -Directory)
        if ($folders.Count -ne 1) { throw 'The Blender archive has an unexpected layout.' }
        $unpacked = $folders[0].FullName
    }
    Write-Output 'Branding the engine'
    [void][DrawingIntelligence.EngineBrand]::Apply($unpacked, $icon)
    if (Test-Path -LiteralPath $engine) {
        if (-not (Test-Inside $engine $appRoot)) { throw 'The engine must stay inside the application folder.' }
        Remove-Item -LiteralPath $engine -Recurse -Force
    }
    Move-Item -LiteralPath $unpacked -Destination $engine
    Remove-Item -LiteralPath $staging -Recurse -Force
    Write-Output "Engine installed in $engine"
}

& (Join-Path $appRoot 'launcher\build.ps1')
if (-not $?) { throw 'The launcher could not be built.' }
