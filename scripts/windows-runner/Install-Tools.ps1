$ErrorActionPreference='Stop'
$root=Join-Path $env:USERPROFILE 'ci-tools'
$downloads=Join-Path $root 'downloads'
New-Item -ItemType Directory -Force -Path $downloads,(Join-Path $root 'bin')|Out-Null
function Get-VerifiedFile($Url,$Name,$Hash){
 $path=Join-Path $downloads $Name
 if((Test-Path $path) -and $Hash -and (Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Hash.ToLowerInvariant()){Remove-Item -LiteralPath $path -Force}
 if(!(Test-Path $path)){Invoke-WebRequest $Url -OutFile $path -UseBasicParsing -TimeoutSec 180}
 if($Hash -and (Get-FileHash $path -Algorithm SHA256).Hash.ToLowerInvariant() -ne $Hash.ToLowerInvariant()){throw "Checksum mismatch: $Name"}
 return $path
}
$sccache=Get-VerifiedFile 'https://github.com/mozilla/sccache/releases/download/v0.18.0/sccache-v0.18.0-x86_64-pc-windows-msvc.zip' 'sccache-0.18.0.zip' '8965c74d5e8a225244f741e18ad2f3f504f48228dc1bac948fc22761a348363d'
if(!(Test-Path (Join-Path $root 'bin\sccache.exe'))){
 $stage=Join-Path $root 'sccache-0.18.0'
 Expand-Archive $sccache $stage -Force
 $binary=Get-ChildItem $stage -Filter sccache.exe -Recurse -File|Select-Object -First 1
 Copy-Item $binary.FullName (Join-Path $root 'bin\sccache.exe')
}
$pwsh=Get-VerifiedFile 'https://github.com/PowerShell/PowerShell/releases/download/v7.6.7/PowerShell-7.6.7-win-x64.zip' 'pwsh-7.6.7.zip' 'd2f952ebdbf24386cb8923553aa58e41a846db35a6155ea04c58142a4b16dbb7'
if(!(Test-Path (Join-Path $root 'pwsh\pwsh.exe'))){Expand-Archive $pwsh (Join-Path $root 'pwsh') -Force}
$pythonExe=Join-Path $root 'python312\python.exe'
if(!(Test-Path $pythonExe)){
 $installer=Get-VerifiedFile 'https://www.python.org/ftp/python/3.12.10/python-3.12.10-amd64.exe' 'python312-installer.exe' ''
 $signature=Get-AuthenticodeSignature $installer
 if($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Subject -notmatch 'Python Software Foundation'){throw 'Python installer signature invalid'}
 $pythonDir=Join-Path $root 'python312'
 $process=Start-Process $installer -ArgumentList @('/quiet','InstallAllUsers=0','PrependPath=0','Include_launcher=0','Include_test=0','Include_pip=1','Shortcuts=0',"TargetDir=$pythonDir") -Wait -PassThru
 if($process.ExitCode -notin @(0,3010)){throw "Python installer exit $($process.ExitCode)"}
}
$nasm=Get-VerifiedFile 'https://www.nasm.us/pub/nasm/releasebuilds/2.16.03/win64/nasm-2.16.03-installer-x64.exe' 'nasm-installer.exe' '657E1252676CFB26A008835C20A760F731C8E0414469A4ED0F83F0FB059CDD35'
if(!(Test-Path (Join-Path $root 'nasm\nasm.exe'))){
 $dir=Join-Path $root 'nasm'
 $process=Start-Process $nasm -ArgumentList @('/S',"/D=$dir") -Wait -PassThru
 if($process.ExitCode -ne 0){throw 'NASM installer failed'}
 if(!(Test-Path (Join-Path $root 'nasm\nasm.exe'))){
  $installed=Join-Path $env:ProgramFiles 'NASM\nasm.exe'
  if(!(Test-Path $installed)){throw 'NASM installer did not provide its compiler'}
  New-Item -ItemType Directory -Force -Path $dir|Out-Null
  Copy-Item $installed (Join-Path $dir 'nasm.exe')
 }
}
$nsis=Get-VerifiedFile 'https://github.com/tauri-apps/binary-releases/releases/download/nsis-3.11/nsis-3.11.zip' 'nsis-3.11.zip' 'c7d27f780ddb6cffb4730138cd1591e841f4b7edb155856901cdf5f214394fa1'
if(!(Test-Path (Join-Path $root 'nsis\makensis.exe'))){
 $dir=Join-Path $root 'nsis'
 $stage=Join-Path $root 'nsis-archive'
 Expand-Archive $nsis $stage -Force
 $binary=Get-ChildItem $stage -Filter makensis.exe -Recurse -File|Select-Object -First 1
 if(!$binary){throw 'NSIS archive lacks compiler'}
 New-Item -ItemType Directory -Force -Path $dir|Out-Null
 Copy-Item (Join-Path $binary.DirectoryName '*') $dir -Recurse -Force
}
& (Join-Path $root 'bin\sccache.exe') --version
& (Join-Path $root 'pwsh\pwsh.exe') -NoProfile -Command '$PSVersionTable.PSVersion.ToString()'
& $pythonExe --version
if(!(Test-Path (Join-Path $root 'python312\python3.exe'))){Copy-Item $pythonExe (Join-Path $root 'python312\python3.exe')}
& (Join-Path $root 'nasm\nasm.exe') -v
& (Join-Path $root 'nsis\makensis.exe') /VERSION
Write-Output 'Runner tool bootstrap finished'
