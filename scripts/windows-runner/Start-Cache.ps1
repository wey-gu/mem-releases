$ErrorActionPreference = 'Stop'
. (Join-Path $env:USERPROFILE 'ci-tools\Enter-BuildEnvironment.ps1')
# Run from the user scheduler, outside a GitHub job's orphan-process ownership.
[Environment]::SetEnvironmentVariable('RUNNER_TRACKING_ID', $null, 'Process')
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
