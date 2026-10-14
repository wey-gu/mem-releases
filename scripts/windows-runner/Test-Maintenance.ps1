$ErrorActionPreference = 'Stop'
$savedHome = $env:USERPROFILE
$savedLocal = $env:LOCALAPPDATA
$fixture = Join-Path $savedHome ('ci-cache\maintenance-test-' + [guid]::NewGuid())
$script = Join-Path $PSScriptRoot 'Maintain-Disk.ps1'
try {
    $env:USERPROFILE = $fixture
    $env:LOCALAPPDATA = Join-Path $fixture 'AppData\Local'
    $cache = Join-Path $env:LOCALAPPDATA 'pip\Cache'
    $protected = Join-Path $fixture 'dev\mem\nmem-rs\target'
    New-Item -ItemType Directory -Force -Path $cache,$protected | Out-Null
    $old = Join-Path $cache 'old.cache'
    $fresh = Join-Path $cache 'fresh.cache'
    $sentinel = Join-Path $protected 'keep.cache'
    foreach ($path in @($old,$fresh,$sentinel)) { 'fixture' | Set-Content $path }
    (Get-Item $old).LastWriteTime = (Get-Date).AddDays(-40)
    (Get-Item $sentinel).LastWriteTime = (Get-Date).AddDays(-40)
    $link = Join-Path $cache 'protected-link'
    New-Item -ItemType Junction -Path $link -Target $protected | Out-Null
    & $script -Apply | Out-Null
    if ((Test-Path $old) -or !(Test-Path $fresh) -or !(Test-Path $sentinel)) {
        throw 'Age/reparse-point cleanup guard failed'
    }
    $env:LOCALAPPDATA = Join-Path $fixture 'dev\mem\.context'
    New-Item -ItemType Directory -Force -Path (Join-Path $env:LOCALAPPDATA 'pip\Cache') | Out-Null
    $refused = $false
    try { & $script -Apply | Out-Null } catch {
        if ($_.Exception.Message -match 'protected test path') { $refused = $true } else { throw }
    }
    if (!$refused) { throw 'Protected overlap was admitted' }
    Write-Output 'Disk maintenance guards: PASS (age, junction, protected overlap)'
} finally {
    $env:USERPROFILE = $savedHome
    $env:LOCALAPPDATA = $savedLocal
    if ($link -and (Test-Path $link)) { (Get-Item $link).Delete() }
    if (Test-Path $fixture) { Remove-Item -LiteralPath $fixture -Recurse -Force }
}
