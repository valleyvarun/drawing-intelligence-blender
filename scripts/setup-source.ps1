param(
    # Optional existing Git checkout to export without downloading or modifying it.
    [string]$Repository = '',
    [switch]$IncludeTestData
)
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$appRoot = [IO.Path]::GetFullPath((Join-Path $PSScriptRoot '..'))
$manifest = Get-Content -LiteralPath (Join-Path $appRoot 'vendor/blender-source.json') -Raw | ConvertFrom-Json
$sourceRoot = Join-Path $appRoot 'vendor/blender'
$marker = Join-Path $sourceRoot 'drawing-source-commit.txt'
if (Test-Path -LiteralPath $sourceRoot) {
    if ((Test-Path -LiteralPath $marker) -and ((Get-Content -LiteralPath $marker -Raw).Trim() -eq $manifest.commit)) {
        Write-Output "Blender $($manifest.tag) source is already available in $sourceRoot"
        exit 0
    }
    throw "Source directory already exists without the expected revision marker: $sourceRoot. Keep any edits before preparing another checkout."
}
$workRoot = Join-Path $appRoot ('build/source-' + [Guid]::NewGuid().ToString('N'))
$staging = Join-Path $workRoot 'source'
New-Item -ItemType Directory -Path $staging -Force | Out-Null
try {
    if ($Repository) {
        $Repository = (Resolve-Path -LiteralPath $Repository).Path
    } else {
        $Repository = Join-Path $workRoot 'repository'
        & git init --quiet $Repository
        if ($LASTEXITCODE -ne 0) { throw 'Could not initialize the source download.' }
        & git -C $Repository fetch --depth=1 $manifest.repository "refs/tags/$($manifest.tag):refs/tags/$($manifest.tag)"
        if ($LASTEXITCODE -ne 0) { throw 'Could not download the pinned Blender source tag.' }
    }
    $revision = & git -c "safe.directory=$($Repository.Replace('\', '/'))" -C $Repository rev-parse "$($manifest.tag)^{commit}"
    if ($LASTEXITCODE -ne 0 -or $revision -ne $manifest.commit) { throw 'Blender source revision does not match the pinned engine.' }
    $archive = Join-Path $workRoot 'source.tar'
    $lfsStorage = Join-Path $appRoot 'build/source-lfs'
    # Export pointers by default; archive filters must not silently download large LFS data.
    $gitOptions = @('-c', "safe.directory=$($Repository.Replace('\', '/'))", '-c', "lfs.storage=$lfsStorage",
        '-c', 'lfs.concurrenttransfers=1')
    if (-not $IncludeTestData) {
        $gitOptions += @('-c', 'filter.lfs.process=', '-c', 'filter.lfs.smudge=', '-c', 'filter.lfs.required=false')
    }
    else { $gitOptions += @('-c', 'lfs.fetchexclude=') }
    & git @gitOptions -C $Repository archive --format=tar "--output=$archive" $manifest.commit
    if ($LASTEXITCODE -ne 0) { throw 'Could not export the Blender source.' }
    & tar -xf $archive -C $staging
    if ($LASTEXITCODE -ne 0) { throw 'Could not extract the Blender source.' }
    if (-not (Test-Path -LiteralPath (Join-Path $staging 'source/blender/draw/engines/workbench'))) {
        throw 'The source export is missing the native viewport.'
    }
    [IO.File]::WriteAllText((Join-Path $staging 'drawing-source-commit.txt'), $manifest.commit + [Environment]::NewLine)
    Move-Item -LiteralPath $staging -Destination $sourceRoot
    Write-Output "Blender $($manifest.tag) source prepared in $sourceRoot"
} finally {
    $resolvedWork = [IO.Path]::GetFullPath($workRoot)
    $buildPrefix = [IO.Path]::GetFullPath((Join-Path $appRoot 'build')) + [IO.Path]::DirectorySeparatorChar
    if (-not $resolvedWork.StartsWith($buildPrefix, [StringComparison]::OrdinalIgnoreCase)) {
        throw 'Source staging cleanup escaped the application build directory.'
    }
    if (Test-Path -LiteralPath $resolvedWork) { Remove-Item -LiteralPath $resolvedWork -Recurse -Force }
}
