<#
    Build the standalone cufflink for Windows.

        venv\Scripts\python.exe -m pip install pyinstaller
        .\build.ps1

    Produces dist\cufflink\cufflink.exe (with models\ beside it) and, unless you
    pass -NoZip, dist\cufflink-win64.zip to hand to someone else.

    The recipient unzips anywhere and double-clicks cufflink.exe. No Python, no
    install, no network.
#>

[CmdletBinding()]
param(
    # Leave the folder alone and skip the (slow, ~365 MB) zip step.
    [switch] $NoZip
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$python = Join-Path $root 'venv\Scripts\python.exe'
$dist = Join-Path $root 'dist\cufflink'
$zip = Join-Path $root 'dist\cufflink-win64.zip'

function Step ($m) { Write-Host "`n$m" -ForegroundColor Cyan }
function Good ($m) { Write-Host "  $m" -ForegroundColor Green }

if (-not (Test-Path $python)) { throw "No venv yet -- run .\Install.cmd first." }
if (-not (Test-Path (Join-Path $root 'models\kokoro-v1.0.onnx'))) {
    throw "models\ is empty -- run .\Install.cmd first."
}
if (-not (Test-Path (Join-Path $root 'models\whisper-base\model.bin'))) {
    throw "models\whisper-base is missing -- run .\Install.cmd to fetch it."
}

Step '1/4  Icon and splash'
& $python (Join-Path $root 'make_assets.py')
if ($LASTEXITCODE -ne 0) { throw 'Could not generate the assets.' }

Step '2/4  PyInstaller'
Remove-Item (Join-Path $root 'build') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $dist -Recurse -Force -ErrorAction SilentlyContinue
& $python -m PyInstaller --noconfirm --noconsole --name cufflink `
    --icon (Join-Path $root 'assets\cufflink.ico') `
    --splash (Join-Path $root 'assets\splash.png') `
    --version-file (Join-Path $root 'version_info.txt') `
    --collect-all espeakng_loader --collect-all phonemizer --collect-all kokoro_onnx `
    (Join-Path $root 'cufflink.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
Good 'cufflink.exe built'

Step '3/4  Voice model'
# Frozen, cufflink reads models\ from beside the exe, so it ships next to it
# rather than inside the archive -- 338 MB does not want to be unpacked to a
# temp folder on every launch.
#
# Named files, not the whole folder. models\ is also where anything else in
# this repo caches its own downloads, and copying it wholesale once put 1.4 GB
# of unrelated speech-to-text weights into the package: 612 MB became 1823 MB
# and nobody noticed until the MSIX came out at 1.6 GB.
#
# whisper-base is the listening half, and is named here for the same reason:
# the folder beside it is where faster-whisper used to cache whatever else it
# had been asked for.
$modelFiles = @(
    'kokoro-v1.0.onnx'
    'voices-v1.0.bin'
    'whisper-base\model.bin'
    'whisper-base\config.json'
    'whisper-base\tokenizer.json'
    'whisper-base\vocabulary.txt'
)
$modelDir = Join-Path $dist 'models'
New-Item -ItemType Directory -Path $modelDir -Force | Out-Null
foreach ($file in $modelFiles) {
    $from = Join-Path $root "models\$file"
    if (-not (Test-Path $from)) { throw "models\$file is missing -- run .\Install.cmd" }
    $to = Join-Path $modelDir $file
    New-Item -ItemType Directory -Path (Split-Path $to -Parent) -Force | Out-Null
    Copy-Item $from $to -Force
}
# The tray icon is a drawing rather than something generated, so it travels as
# a file. cufflink falls back to the flat mark if it is missing, which means a
# build without this looks wrong rather than failing -- hence the check.
$artDir = Join-Path $dist 'assets'
New-Item -ItemType Directory -Path $artDir -Force | Out-Null
$trayArt = Join-Path $root 'assets\cufflink-tray.png'
if (-not (Test-Path $trayArt)) { throw 'assets\cufflink-tray.png is missing.' }
Copy-Item $trayArt (Join-Path $artDir 'cufflink-tray.png') -Force

# The exe bundles espeak-ng and phonemizer, both GPL-3.0, so the licence has to
# travel with the binary.
Copy-Item (Join-Path $root 'LICENSE') (Join-Path $dist 'LICENSE.txt') -Force
$mb = [math]::Round(((Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))
Good "dist\cufflink is $mb MB"

if ($NoZip) {
    Step '4/4  Skipping the zip (-NoZip)'
} else {
    Step '4/4  Zipping'
    if (Test-Path $zip) { [IO.File]::Delete($zip) }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $dist, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $false)
    Good ("dist\cufflink-win64.zip is {0} MB" -f [math]::Round((Get-Item $zip).Length / 1MB))
}

Write-Host "`nDone." -ForegroundColor Green
Write-Host "  Unzip anywhere and run cufflink.exe. Windows will warn that it is"
Write-Host "  unsigned: More info -> Run anyway."
