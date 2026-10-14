param([switch]$Persist)
$ErrorActionPreference = 'Stop'
$tools = Join-Path $env:USERPROFILE 'ci-tools'
$cache = Join-Path $env:USERPROFILE 'ci-cache'
$paths = @((Join-Path $tools 'bin'), (Join-Path $tools 'pwsh'),
    (Join-Path $tools 'python312'), (Join-Path $tools 'python312\Scripts'),
    (Join-Path $tools 'nasm'), (Join-Path $tools 'nsis'),
    'C:\Program Files\LLVM\bin', 'C:\Program Files\Git\bin')
$settings = @{
    RUSTC_WRAPPER = Join-Path $tools 'bin\sccache.exe'
    SCCACHE_DIR = Join-Path $cache 'compiler'
    SCCACHE_CACHE_SIZE = '8G'
    SCCACHE_IDLE_TIMEOUT = '600'
    SCCACHE_IGNORE_SERVER_IO_ERROR = '1'
    CMAKE_C_COMPILER_LAUNCHER = Join-Path $tools 'bin\sccache.exe'
    CMAKE_CXX_COMPILER_LAUNCHER = Join-Path $tools 'bin\sccache.exe'
}
if (!(Test-Path $settings.RUSTC_WRAPPER)) { throw 'Install runner tools first' }
New-Item -ItemType Directory -Force -Path $cache, $settings.SCCACHE_DIR | Out-Null
if ($Persist) {
    $backup = Join-Path $tools 'environment-before.json'
    if (!(Test-Path $backup)) {
        $previous = @{}
        foreach ($name in $settings.Keys) { $previous[$name] = [Environment]::GetEnvironmentVariable($name, 'User') }
        $previous['Path'] = [Environment]::GetEnvironmentVariable('Path', 'User')
        $previous | ConvertTo-Json | Set-Content $backup -Encoding utf8
    }
    foreach ($name in $settings.Keys) { [Environment]::SetEnvironmentVariable($name, $settings[$name], 'User') }
    $userPath = [Environment]::GetEnvironmentVariable('Path', 'User')
    [Environment]::SetEnvironmentVariable('Path', (($paths + @($userPath -split ';') | Select-Object -Unique) -join ';'), 'User')
}
foreach ($name in $settings.Keys) { [Environment]::SetEnvironmentVariable($name, $settings[$name], 'Process') }
$env:Path = (($paths + @($env:Path -split ';') | Select-Object -Unique) -join ';')
# Do not share Cargo target directories or change developer incremental settings.
# Rust incremental/check/linker invocations may compile normally without cache hits.

