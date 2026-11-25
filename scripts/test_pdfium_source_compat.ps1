$ErrorActionPreference = 'Stop'
$workflow = Join-Path $PSScriptRoot '../.github/workflows/release-desktop.yml'
$lines = Get-Content -LiteralPath $workflow
$starts = @(0..($lines.Count - 1) | Where-Object { $lines[$_].Trim() -eq '# TEST-EXTRACT-PDFIUM-COMPAT-START' })
$ends = @(0..($lines.Count - 1) | Where-Object { $lines[$_].Trim() -eq '# TEST-EXTRACT-PDFIUM-COMPAT-END' })
if ($starts.Count -ne 1 -or $ends.Count -ne 1 -or $ends[0] -le $starts[0]) { throw 'PDFium compatibility markers are missing or ambiguous' }
$start = $starts[0]
$end = $ends[0]
$body = ($lines[($start + 1)..($end - 1)] | ForEach-Object { $_ -replace '^ {10}', '' }) -join "`n"
$root = Join-Path ([IO.Path]::GetTempPath()) ('pdfium source compat ' + [guid]::NewGuid())
New-Item -ItemType Directory -Path $root | Out-Null
$fixed = '$resolvedCandidates = @((Join-Path $resolvedDir $PdfiumLib))'
$modern = '$PdfiumSearch += $EmbeddedOutputDir'
$producer = @'
def stage_pdfium_from_cargo(messages, output_dir, target):
    "--message-format=json-render-diagnostics"
    stage_pdfium_from_cargo(messages, output_dir, target)
'@
$legacy = '            $candidate = Join-Path $resolvedDir $PdfiumLib' + "`n" +
          '            if (Test-Path $candidate) { $PdfiumSrc = $candidate }'

function Invoke-Compatibility {
    param([string]$Name, [string]$Source, [string]$Builder, [string]$Step = $body)
    $work = Join-Path $root $Name
    $bundle = Join-Path $work 'nowledge-graph/scripts/build-rust-bundle.ps1'
    $builderPath = Join-Path $work 'integrations/hawdb/build-embedded-server.py'
    New-Item -ItemType Directory -Force -Path (Split-Path $bundle), (Split-Path $builderPath) | Out-Null
    [IO.File]::WriteAllText($bundle, $Source)
    [IO.File]::WriteAllText($builderPath, $Builder)
    $command = Join-Path $work 'compat-step.ps1'
    [IO.File]::WriteAllText($command, '$ErrorActionPreference = ''Stop''' + "`n" + $Step)
    $stdout = Join-Path $work 'stdout.txt'
    $stderr = Join-Path $work 'stderr.txt'
    $process = Start-Process -FilePath (Get-Process -Id $PID).Path -ArgumentList @('-NoProfile', '-File', "`"$command`"") -WorkingDirectory $work -Wait -PassThru -RedirectStandardOutput $stdout -RedirectStandardError $stderr
    [pscustomobject]@{ Exit = $process.ExitCode; Bytes = [IO.File]::ReadAllText($bundle); Output = [IO.File]::ReadAllText($stdout) + [IO.File]::ReadAllText($stderr) }
}

function Require {
    param([bool]$Condition, [string]$Message)
    if (-not $Condition) { throw $Message }
}

try {
    # Production-only mutation: remove the new admission and retain the exact
    # same valid current-source fixture and terminal success oracle.
    $needle = 'if ($scriptContent.Contains($producerMarker)) {'
    Require ($body.Contains($needle)) 'Producer admission mutation did not match'
    $red = Invoke-Compatibility 'mutation' $modern $producer ($body.Replace($needle, 'if ($false) {'))
    Require ($red.Exit -ne 0 -and $red.Output.Contains('found 0')) 'RED must reject the modern source through the legacy-block refusal'
    Write-Host 'RED: current producer source was refused without producer admission'

    $current = Invoke-Compatibility 'current' $modern $producer
    Require ($current.Exit -eq 0 -and $current.Bytes -ceq $modern) 'Current producer source must pass without being rewritten'
    $oldFixed = Invoke-Compatibility 'old-fixed' $fixed ''
    Require ($oldFixed.Exit -eq 0 -and $oldFixed.Bytes -ceq $fixed) 'Already-fixed historical source must remain byte-identical'
    $old = Invoke-Compatibility 'legacy' $legacy ''
    Require ($old.Exit -eq 0 -and $old.Bytes.Contains($fixed) -and $old.Bytes.Contains('"bin"')) 'The exact historical block must receive its sibling-bin backport'
    foreach ($case in @(
        @{ Name = 'unknown'; Source = 'unknown source'; Builder = '' },
        @{ Name = 'duplicate'; Source = ($legacy + "`n" + $legacy); Builder = '' },
        @{ Name = 'partial-producer'; Source = $modern; Builder = 'def stage_pdfium_from_cargo():' }
    )) {
        $result = Invoke-Compatibility $case.Name $case.Source $case.Builder
        Require ($result.Exit -ne 0 -and $result.Bytes -ceq $case.Source) ("Unsafe source must be refused without mutation: " + $case.Name)
    }
    Write-Host 'GREEN: all 6 PDFium source compatibility scenarios passed'
} finally {
    Remove-Item -LiteralPath $root -Recurse -Force
}
