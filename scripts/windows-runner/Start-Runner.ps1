$ErrorActionPreference = 'Stop'
$root = Join-Path $env:USERPROFILE 'ci-runner'
. (Join-Path $env:USERPROFILE 'ci-tools\Enter-BuildEnvironment.ps1')
New-Item -ItemType Directory -Force -Path (Join-Path $root 'logs') | Out-Null
# One shared local server owns the bounded disk cache; never stop it from CI.
# Its first compilation starts the server automatically.
Start-ScheduledTask 'Nowledge-Mem-Compiler-Cache'
$env:CARGO_BUILD_JOBS = '4'
$env:CARGO_TARGET_DIR = Join-Path $env:USERPROFILE 'ci-cache\runner-target'
$env:RUNNER_TOOL_CACHE = Join-Path $env:USERPROFILE 'ci-tools\actions-toolcache'
$env:NMEM_RUNNER_TOOLS = Join-Path $env:USERPROFILE 'ci-tools'
Set-Location $root
& (Join-Path $root 'bin\Runner.Listener.exe') run *>> (Join-Path $root 'logs\listener.log')
exit $LASTEXITCODE
