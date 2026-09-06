<#
    Build the standalone Murmur for Windows.

        venv\Scripts\python.exe -m pip install pyinstaller
        .\build.ps1

    Produces dist\Murmur\Murmur.exe (with models\ beside it) and, unless you
    pass -NoZip, dist\Murmur-win64.zip to hand to someone else.

    The recipient unzips anywhere and double-clicks Murmur.exe. No Python, no
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
$dist = Join-Path $root 'dist\Murmur'
$zip = Join-Path $root 'dist\Murmur-win64.zip'

function Step ($m) { Write-Host "`n$m" -ForegroundColor Cyan }
function Good ($m) { Write-Host "  $m" -ForegroundColor Green }

if (-not (Test-Path $python)) { throw "No venv yet -- run .\Install.cmd first." }
if (-not (Test-Path (Join-Path $root 'models\kokoro-v1.0.onnx'))) {
    throw "models\ is empty -- run .\Install.cmd first."
}

Step '1/4  Icon and splash'
& $python (Join-Path $root 'make_assets.py')
if ($LASTEXITCODE -ne 0) { throw 'Could not generate the assets.' }

Step '2/4  PyInstaller'
Remove-Item (Join-Path $root 'build') -Recurse -Force -ErrorAction SilentlyContinue
Remove-Item $dist -Recurse -Force -ErrorAction SilentlyContinue
& $python -m PyInstaller --noconfirm --noconsole --name Murmur `
    --icon (Join-Path $root 'assets\murmur.ico') `
    --splash (Join-Path $root 'assets\splash.png') `
    --version-file (Join-Path $root 'version_info.txt') `
    --collect-all espeakng_loader --collect-all phonemizer --collect-all kokoro_onnx `
    (Join-Path $root 'murmur.py')
if ($LASTEXITCODE -ne 0) { throw 'PyInstaller failed.' }
Good 'Murmur.exe built'

Step '3/4  Voice model'
# Frozen, Murmur reads models\ from beside the exe, so it ships next to it
# rather than inside the archive -- 338 MB does not want to be unpacked to a
# temp folder on every launch.
Copy-Item (Join-Path $root 'models') (Join-Path $dist 'models') -Recurse -Force
# The exe bundles espeak-ng and phonemizer, both GPL-3.0, so the licence has to
# travel with the binary.
Copy-Item (Join-Path $root 'LICENSE') (Join-Path $dist 'LICENSE.txt') -Force
$mb = [math]::Round(((Get-ChildItem $dist -Recurse -File | Measure-Object Length -Sum).Sum / 1MB))
Good "dist\Murmur is $mb MB"

if ($NoZip) {
    Step '4/4  Skipping the zip (-NoZip)'
} else {
    Step '4/4  Zipping'
    if (Test-Path $zip) { [IO.File]::Delete($zip) }
    Add-Type -AssemblyName System.IO.Compression.FileSystem
    [System.IO.Compression.ZipFile]::CreateFromDirectory(
        $dist, $zip, [System.IO.Compression.CompressionLevel]::Optimal, $false)
    Good ("dist\Murmur-win64.zip is {0} MB" -f [math]::Round((Get-Item $zip).Length / 1MB))
}

Write-Host "`nDone." -ForegroundColor Green
Write-Host "  Unzip anywhere and run Murmur.exe. Windows will warn that it is"
Write-Host "  unsigned: More info -> Run anyway."
