# NSIS overwrite-install smoke on a real Windows runner, without compiling.
#
# Re-bundles a candidate nowledge-co/mem source's NSIS installer from the
# binaries of a PUBLISHED installer (tauri bundle --bundles nsis only), then
# exercises the reinstall page non-interactively with /P (passive), which runs
# the same PageReinstall function a double-click install runs:
#   1. install the published base with /S;
#   2. replace the installed uninstall.exe with a sentinel that only records
#      that it was executed (the old uninstaller is what the reinstall page
#      would launch);
#   3. run the candidate installer with /P over it (upgrade), then again over
#      itself (same-version reinstall);
#   4. assert the sentinel never ran, the install dir and Start Menu shortcut
#      are kept, and DisplayVersion moved to the candidate.
# With -ExpectUninstall (control: an unpatched template), step 3's
# same-version reinstall must run the sentinel, proving the probe can see it.
# nowledge-co/mem#7283 / #7286.
param(
  [Parameter(Mandatory = $true)][string]$Source,      # nowledge-co/mem checkout
  [Parameter(Mandatory = $true)][string]$BaseInstaller,
  [switch]$ExpectUninstall,
  [string]$TauriCli = '2.11.4'
)
$ErrorActionPreference = 'Stop'
$failures = New-Object System.Collections.Generic.List[string]
function Check([bool]$ok, [string]$what) {
  if ($ok) { Write-Host "ok:   $what" } else { Write-Host "FAIL: $what"; $failures.Add($what) }
}
$uninstKey = 'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Nowledge Mem'
$work = Join-Path $env:RUNNER_TEMP 'nsis-smoke'
New-Item -ItemType Directory -Force -Path $work | Out-Null
$marker = Join-Path $work 'uninstall-ran.txt'

# 1. Base install (silent: never shows the reinstall page).
# Returns the exit code, or $null when the installer is still running after
# $timeoutSeconds (stuck on a dialog). Then it and any uninstaller it started
# are stopped, so the caller can still inspect what happened.
function Invoke-Installer([string]$path, [string]$arguments, [int]$timeoutSeconds = 600) {
  $p = Start-Process -FilePath $path -ArgumentList $arguments -PassThru
  if ($p.WaitForExit($timeoutSeconds * 1000)) { return $p.ExitCode }
  Get-Process | Where-Object { $_.Path -and ($_.Path -like "$env:LOCALAPPDATA\Nowledge Mem\*" -or $_.Path -like "$env:TEMP\*") -and $_.ProcessName -match '^(uninstall|Au_|Un_)' } | Stop-Process -Force -ErrorAction SilentlyContinue
  $p | Stop-Process -Force -ErrorAction SilentlyContinue
  Write-Host "$([IO.Path]::GetFileName($path)) $arguments still running after $timeoutSeconds s; stopped"
  return $null
}
$code = Invoke-Installer $BaseInstaller '/S'
if ($code -ne 0) { throw "base install exited $(if ($null -eq $code) { 'timeout' } else { $code })" }
$installDir = (Get-ItemProperty $uninstKey).InstallLocation.Trim('"')
$baseVersion = (Get-ItemProperty $uninstKey).DisplayVersion
Write-Host "base $baseVersion installed at $installDir"

# 2. Payload for re-bundling: the installed binaries, nothing compiled.
$graph = Join-Path $Source 'nowledge-graph'
$release = Join-Path $graph 'src-tauri\target\release'
New-Item -ItemType Directory -Force -Path $release, (Join-Path $graph 'rust-backend') | Out-Null
Copy-Item (Join-Path $installDir 'nowledge-mem.exe') $release
Copy-Item (Join-Path $installDir '_up_\rust-backend\*') (Join-Path $graph 'rust-backend') -Recurse -Force
Push-Location $graph
try {
  & npx -y "@tauri-apps/cli@$TauriCli" bundle --bundles nsis
  if ($LASTEXITCODE -ne 0) { throw "tauri bundle exited $LASTEXITCODE" }
} finally { Pop-Location }
$candidate = Get-ChildItem (Join-Path $release 'bundle\nsis') -Filter '*-setup.exe' | Select-Object -First 1
if (-not $candidate) { throw 'tauri bundle produced no NSIS installer' }
$candidateVersion = (Get-Content (Join-Path $graph 'src-tauri\tauri.conf.json') -Raw | ConvertFrom-Json).version
Write-Host "candidate $($candidate.Name) ($candidateVersion)"

# Sentinel uninstaller: records its arguments, then removes the main binary
# from the `_?=<dir>` it was given (a real uninstaller does; upstream checks it
# and would otherwise block on an "unable to uninstall" dialog), and exits 0.
$sentinelSrc = Join-Path $work 'Sentinel.cs'
$sentinelExe = Join-Path $work 'sentinel.exe'
Set-Content -Path $sentinelSrc -Encoding ASCII -Value @"
class Sentinel { static int Main(string[] a) {
  System.IO.File.AppendAllText(@"$marker", string.Join(" ", a) + System.Environment.NewLine);
  foreach (var x in a) { if (x.StartsWith("_?=")) {
    var exe = System.IO.Path.Combine(x.Substring(3), "nowledge-mem.exe");
    if (System.IO.File.Exists(exe)) System.IO.File.Delete(exe); } }
  return 0; } }
"@
& "$env:WINDIR\Microsoft.NET\Framework64\v4.0.30319\csc.exe" /nologo /out:$sentinelExe $sentinelSrc
if ($LASTEXITCODE -ne 0) { throw 'sentinel compile failed' }

function Run-Candidate([string]$label) {
  Copy-Item $sentinelExe (Join-Path $installDir 'uninstall.exe') -Force
  Remove-Item $marker -ErrorAction SilentlyContinue
  $code = Invoke-Installer $candidate.FullName '/P' $(if ($ExpectUninstall) { 300 } else { 600 })
  $ran = Test-Path $marker
  $reg = Get-ItemProperty $uninstKey
  Write-Host "[$label] exit=$(if ($null -eq $code) { 'stuck' } else { $code }) uninstaller-ran=$ran version=$($reg.DisplayVersion) dir=$($reg.InstallLocation)"
  if ($ran) { Write-Host "[$label] uninstaller args: $(Get-Content $marker -Raw)" }
  return [pscustomobject]@{ Exit = $code; Ran = $ran; Reg = $reg }
}

$upgrade = Run-Candidate 'upgrade /P'
$same = Run-Candidate 'same-version /P'
$shortcut = Join-Path $env:APPDATA 'Microsoft\Windows\Start Menu\Programs\Nowledge Mem.lnk'

if ($ExpectUninstall) {
  # Upstream runs the previous uninstaller on a same-version /P reinstall
  # (and, here, stays on a dialog afterwards). Either way the probe saw it.
  Check ($same.Ran) 'control: unpatched template runs the old uninstaller on a same-version /P reinstall'
} else {
  Check ($upgrade.Exit -eq 0) 'upgrade /P exits 0'
  Check (-not $upgrade.Ran) 'upgrade /P never runs the previous uninstaller'
  Check ($same.Exit -eq 0) 'same-version /P exits 0'
  Check (-not $same.Ran) 'same-version /P never runs the previous uninstaller'
  Check ($same.Reg.DisplayVersion -eq $candidateVersion) "DisplayVersion is $candidateVersion"
  Check ($same.Reg.InstallLocation.Trim('"') -eq $installDir) 'install dir is kept'
  Check (Test-Path (Join-Path $installDir 'nowledge-mem.exe')) 'main binary present'
  Check (Test-Path (Join-Path $installDir '_up_\rust-backend\nmem-server.exe')) 'backend present'
  Check (Test-Path $shortcut) 'Start Menu shortcut present'
}
if ($failures.Count) { Write-Host "$($failures.Count) check(s) failed"; exit 1 }
Write-Host 'all checks passed'
