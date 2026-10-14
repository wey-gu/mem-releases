param([switch]$Apply)
$ErrorActionPreference = 'Stop'
$protected = @('dev\mem\nmem-rs\target', 'dev\mem\nowledge-graph\src-tauri\target', 'dev\mem\.context') |
    ForEach-Object { [IO.Path]::GetFullPath((Join-Path $env:USERPROFILE $_)).TrimEnd('\') }
$roots = @((Join-Path $env:LOCALAPPDATA 'pip\Cache'), (Join-Path $env:USERPROFILE 'ci-tools\downloads'))
$cutoff = (Get-Date).AddDays(-30)
$bytes = 0L
$count = 0
$failed = 0
foreach ($root in $roots) {
    if (!(Test-Path -LiteralPath $root)) { continue }
    $full = [IO.Path]::GetFullPath($root).TrimEnd('\')
    foreach ($path in $protected) {
        if ($full -eq $path -or $full.StartsWith($path + '\', [StringComparison]::OrdinalIgnoreCase) -or
            $path.StartsWith($full + '\', [StringComparison]::OrdinalIgnoreCase)) { throw 'Cleanup intersects a protected test path' }
    }
    $pending = [Collections.Generic.Stack[string]]::new()
    $ancestor = Get-Item -LiteralPath $full -Force
    $reparse = $false
    while ($ancestor) {
        if ($ancestor.Attributes -band [IO.FileAttributes]::ReparsePoint) { $reparse = $true; break }
        $ancestor = $ancestor.Parent
    }
    if ($reparse) { continue }
    $pending.Push($full)
    while ($pending.Count -gt 0) {
        $dir = $pending.Pop()
        $item = Get-Item -LiteralPath $dir -Force
        if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
        foreach ($entry in Get-ChildItem -LiteralPath $dir -Force) {
            if ($entry.Attributes -band [IO.FileAttributes]::ReparsePoint) { continue }
            if ($entry.PSIsContainer) { $pending.Push($entry.FullName); continue }
            if ($entry.LastWriteTime -ge $cutoff) { continue }
            $length = $entry.Length
            if ($Apply) {
                try { Remove-Item -LiteralPath $entry.FullName -Force -ErrorAction Stop }
                catch { $failed++; continue }
            }
            $bytes += $length
            $count++
        }
    }
}
[pscustomobject]@{applied=[bool]$Apply;files=$count;bytes=$bytes;lockedOrFailed=$failed;
    freeGiB=[math]::Round((Get-PSDrive C).Free/1GB,2);compilerCacheLimit='8G'} | ConvertTo-Json -Compress
