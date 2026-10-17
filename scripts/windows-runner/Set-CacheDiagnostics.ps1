param([switch]$Enable)
$ErrorActionPreference = 'Stop'
. (Join-Path $env:USERPROFILE 'ci-tools\Enter-BuildEnvironment.ps1')
# Operator-only: CI jobs must never stop the shared server.
$compilers = @('cargo','rustc','clang','clang-cl','cl','cmake','ninja','nasm','ml64','gcc','g++','cc1','cc1plus','link','lld-link')
if (@(Get-Process -Name $compilers -ErrorAction SilentlyContinue).Count -gt 0) {
    throw 'Compiler clients are active; defer diagnostic mode change without stopping them'
}
$marker = Join-Path $env:USERPROFILE 'ci-tools\cache-diagnostics.enabled'
if ($Enable) { New-Item -ItemType File -Force -Path $marker | Out-Null }
elseif (Test-Path $marker) { Remove-Item $marker }
& $env:RUSTC_WRAPPER --stop-server | Out-Null
# No cache/target contents are removed. Restart under the independent scheduler.
Start-ScheduledTask -TaskName 'Nowledge-Mem-Compiler-Cache'
