$ErrorActionPreference = 'Stop'
. (Join-Path $env:USERPROFILE 'ci-tools\Enter-BuildEnvironment.ps1')
$root = Join-Path (Join-Path $env:USERPROFILE 'ci-cache\validation') ('run-' + (Get-Date -Format 'yyyyMMdd-HHmmss'))
New-Item -ItemType Directory -Force -Path (Join-Path $root 'fixture\src') | Out-Null
@'
[package]
name="cache-safety-proof"
version="0.1.0"
edition="2024"
[workspace]
'@ | Set-Content (Join-Path $root 'fixture\Cargo.toml') -Encoding ascii
'pub fn answer() -> u32 { 42 }' | Set-Content (Join-Path $root 'fixture\src\lib.rs') -Encoding ascii
'fn main() { assert_eq!(cache_safety_proof::answer(), 42); }' |
    Set-Content (Join-Path $root 'fixture\src\main.rs') -Encoding ascii
& cargo +1.98.1 check --offline --manifest-path (Join-Path $root 'fixture\Cargo.toml')
if ($LASTEXITCODE -ne 0) { throw 'sccache broke cargo check/incremental compatibility' }
& cargo +1.98.1 build --offline --manifest-path (Join-Path $root 'fixture\Cargo.toml')
if ($LASTEXITCODE -ne 0) { throw 'sccache broke developer incremental compilation' }
& (Join-Path $root 'fixture\target\debug\cache-safety-proof.exe')
if ($LASTEXITCODE -ne 0) { throw 'Developer fixture runtime failed' }
$manifest = Join-Path $env:USERPROFILE 'dev\mem\nmem-rs\crates\nmem-wire\Cargo.toml'
$statsBefore = & $env:RUSTC_WRAPPER --show-stats --stats-format=json
$samples = @()
foreach ($name in @('local-test', 'runner')) {
    $target = Join-Path $root $name
    if (Test-Path $target) { throw 'Use fresh benchmark target directories; refusing to delete previous results' }
    $watch = [Diagnostics.Stopwatch]::StartNew()
    $log = Join-Path $root ($name + '.log')
    # Start-Process -Wait also waits for descendants on Windows, including an
    # auto-started cache daemon. Measure the Cargo process, not daemon lifetime.
    $info = [Diagnostics.ProcessStartInfo]::new((Get-Command cargo).Source)
    $info.UseShellExecute = $false
    $info.RedirectStandardError = $true
    foreach ($arg in @('+1.98.1', 'build', '-p', 'nmem-wire', '--release', '--locked', '--offline',
        '--manifest-path', $manifest, '--target-dir', $target)) { $info.ArgumentList.Add($arg) }
    $process = [Diagnostics.Process]::Start($info)
    $stderr = $process.StandardError.ReadToEndAsync()
    $process.WaitForExit()
    [IO.File]::WriteAllText($log, $stderr.GetAwaiter().GetResult())
    if ($process.ExitCode -ne 0) { throw "Representative build failed; inspect local $log" }
    $samples += [pscustomobject]@{context=$name;seconds=[math]::Round($watch.Elapsed.TotalSeconds,3);exitCode=$process.ExitCode}
}
$statsAfter = & $env:RUSTC_WRAPPER --show-stats --stats-format=json
$result = @{samples=$samples;before=($statsBefore|ConvertFrom-Json);after=($statsAfter|ConvertFrom-Json);
    note='Same nmem-wire source/profile/toolchain, separate fresh target dirs. This measures cache reuse, not whole-release speed.'}
$result | ConvertTo-Json -Depth 12 | Set-Content (Join-Path $root 'result.json') -Encoding utf8
$result | ConvertTo-Json -Depth 12 | Set-Content (Join-Path $env:USERPROFILE 'ci-tools\last-cache-validation.json') -Encoding utf8
$samples | ConvertTo-Json -Compress
