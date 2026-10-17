$ErrorActionPreference = 'Stop'
. (Join-Path $env:USERPROFILE 'ci-tools\Enter-BuildEnvironment.ps1')
# Run from the user scheduler, outside a GitHub job's orphan-process ownership.
[Environment]::SetEnvironmentVariable('RUNNER_TRACKING_ID', $null, 'Process')
# Operator opt-in for a bounded qualification window, not a permanent CI switch.
if (Test-Path (Join-Path $env:USERPROFILE 'ci-tools\cache-diagnostics.enabled')) {
    $diagnostics = Join-Path $env:USERPROFILE 'ci-cache\diagnostics'
    New-Item -ItemType Directory -Force -Path $diagnostics | Out-Null
    $env:SCCACHE_LOG = 'sccache=debug'
    $env:SCCACHE_LOG_MILLIS = '1'
    $env:SCCACHE_ERROR_LOG = Join-Path $diagnostics 'daemon.log'
}
$info = [Diagnostics.ProcessStartInfo]::new($env:RUSTC_WRAPPER)
$info.UseShellExecute = $false
$info.ArgumentList.Add('--start-server')
$process = [Diagnostics.Process]::Start($info)
$process.WaitForExit()
# An existing shared server is healthy too; never restart/stop it here.
if ($process.ExitCode -ne 0) {
    & $env:RUSTC_WRAPPER --show-stats --stats-format=json | Out-Null
    if ($LASTEXITCODE -ne 0) { throw 'Shared cache server is unavailable' }
}
