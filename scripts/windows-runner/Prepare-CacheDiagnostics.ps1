$ErrorActionPreference = 'Stop'
$diag = Join-Path $env:RUNNER_TEMP 'sccache-diag'
New-Item -ItemType Directory -Force -Path $diag | Out-Null
$log = Join-Path $env:USERPROFILE 'ci-cache\diagnostics\daemon.log'
if (!(Test-Path $log)) { throw 'Operator must enable shared-cache diagnostic logging while compilers are idle' }
"SCCACHE_DIAGNOSTIC_LOG=$log" | Out-File $env:GITHUB_ENV -Append -Encoding utf8
"SCCACHE_DIAGNOSTIC_OFFSET=$((Get-Item $log).Length)" | Out-File $env:GITHUB_ENV -Append -Encoding utf8
& $env:RUSTC_WRAPPER --show-stats --stats-format json | Out-File (Join-Path $diag 'stats-before.json') -Encoding utf8
if ($LASTEXITCODE -ne 0) { throw 'Could not read shared cache statistics' }
