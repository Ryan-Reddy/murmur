<#
    Build the MSIX package for the Microsoft Store.

        .\package.ps1                 # pack, ready to upload
        .\package.ps1 -SelfSign       # also sign it, so you can install it here
        .\package.ps1 -Rebuild        # run build.ps1 first, even if dist exists

    Needs the Windows SDK for makeappx.exe, makepri.exe and signtool.exe:

        winget install Microsoft.WindowsSDK.10.0.22621

    Produces dist\Murmur.msix. That file is what you upload to Partner Center;
    Microsoft signs it on the way through, which is what makes the SmartScreen
    warning go away -- the thing a certificate of your own would cost about
    EUR 300 a year to fix, and would not fix immediately even then.

    Before the first run, copy packaging\identity.example.json to
    packaging\identity.json and fill it in from Partner Center.
#>

[CmdletBinding()]
param(
    # Sign with a self-signed certificate so the package can be installed on
    # this machine for testing. Never for submission -- the Store signs it.
    [switch] $SelfSign,
    # Rebuild the PyInstaller output even if dist\Murmur already exists.
    [switch] $Rebuild
)

$ErrorActionPreference = 'Stop'
$root = Split-Path -Parent $MyInvocation.MyCommand.Path
$staging = Join-Path $root 'build\msix'
$msix = Join-Path $root 'dist\Murmur.msix'

function Step ($m) { Write-Host "`n$m" -ForegroundColor Cyan }
function Good ($m) { Write-Host "  $m" -ForegroundColor Green }
function Note ($m) { Write-Host "  $m" -ForegroundColor DarkGray }

# -- the SDK ---------------------------------------------------------------

function Find-SdkTool ($name) {
    <# Newest SDK first. The tools are versioned per SDK, and an old makeappx
       will reject manifest namespaces a newer one accepts. #>
    $bases = @(
        "${env:ProgramFiles(x86)}\Windows Kits\10\bin",
        "$env:ProgramFiles\Windows Kits\10\bin"
    ) | Where-Object { Test-Path $_ }
    foreach ($base in $bases) {
        $found = Get-ChildItem $base -Directory -ErrorAction SilentlyContinue |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "x64\$name" } |
            Where-Object { Test-Path $_ } |
            Select-Object -First 1
        if ($found) { return $found }
    }
    $onPath = Get-Command $name -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }
    return $null
}

Step '1/6  Windows SDK'
$makeappx = Find-SdkTool 'makeappx.exe'
$makepri = Find-SdkTool 'makepri.exe'
if (-not $makeappx) {
    throw @"
makeappx.exe not found. Install the Windows SDK:

    winget install Microsoft.WindowsSDK.10.0.22621

then open a new shell and run this again.
"@
}
Good "makeappx: $makeappx"
if ($makepri) { Good "makepri:  $makepri" } else { Note 'makepri not found - packing without resources.pri' }

# -- identity --------------------------------------------------------------

Step '2/6  Identity'
$identityFile = Join-Path $root 'packaging\identity.json'
if (-not (Test-Path $identityFile)) {
    throw @"
No packaging\identity.json yet.

    copy packaging\identity.example.json packaging\identity.json

then fill in Name, Publisher and Version from Partner Center >
your app > Product management > Product identity.
"@
}
$identity = Get-Content $identityFile -Raw | ConvertFrom-Json
foreach ($field in 'name', 'publisher', 'version', 'displayName') {
    if (-not $identity.$field) { throw "packaging\identity.json has no '$field'." }
}
if ($identity.version -notmatch '^\d+\.\d+\.\d+\.0$') {
    throw "Version must be four numbers ending in 0, e.g. 1.0.0.0 - the Store reserves the last field."
}
Good "$($identity.displayName)  -  $($identity.name)  $($identity.version)"
Note $identity.publisher

# -- the app itself --------------------------------------------------------

Step '3/6  Application payload'
$built = Join-Path $root 'dist\Murmur'
if ($Rebuild -or -not (Test-Path (Join-Path $built 'Murmur.exe'))) {
    Note 'running build.ps1 (this takes a few minutes)'
    & (Join-Path $root 'build.ps1') -NoZip
    if ($LASTEXITCODE -ne 0) { throw 'build.ps1 failed.' }
}
if (-not (Test-Path (Join-Path $built 'Murmur.exe'))) { throw 'No dist\Murmur\Murmur.exe to package.' }
Good "$([math]::Round((Get-ChildItem $built -Recurse | Measure-Object Length -Sum).Sum / 1MB)) MB"

# -- lay it out ------------------------------------------------------------

Step '4/6  Staging'
if (Test-Path $staging) { Remove-Item -LiteralPath $staging -Recurse -Force }
New-Item -ItemType Directory -Path $staging -Force | Out-Null

Copy-Item $built (Join-Path $staging 'Murmur') -Recurse
$stagedAssets = Join-Path $staging 'assets'
New-Item -ItemType Directory -Path $stagedAssets -Force | Out-Null
$storeAssets = Join-Path $root 'assets\store'
if (-not (Test-Path $storeAssets)) {
    Note 'assets\store missing - generating'
    & (Join-Path $root 'venv\Scripts\python.exe') (Join-Path $root 'make_assets.py') | Out-Null
}
Copy-Item (Join-Path $storeAssets '*.png') $stagedAssets

# The manifest names assets\Square150x150Logo.png; on disk they are the
# .scale-N variants and Windows resolves between them. It still wants the
# unqualified name to exist, so the 100% scale is copied to it.
Get-ChildItem $stagedAssets -Filter '*.scale-100.png' | ForEach-Object {
    Copy-Item $_.FullName (Join-Path $stagedAssets ($_.Name -replace '\.scale-100', ''))
}

$manifest = Get-Content (Join-Path $root 'packaging\AppxManifest.xml') -Raw
$manifest = $manifest.Replace('__IDENTITY_NAME__', $identity.name)
$manifest = $manifest.Replace('__DISPLAY_NAME__', $identity.displayName)
$manifest = $manifest.Replace('__IDENTITY_PUBLISHER__', $identity.publisher)
$manifest = $manifest.Replace('__VERSION__', $identity.version)
Set-Content -LiteralPath (Join-Path $staging 'AppxManifest.xml') -Value $manifest -Encoding utf8
Good "staged to build\msix"

if ($makepri) {
    $priConfig = Join-Path $staging 'priconfig.xml'
    & $makepri createconfig /cf $priConfig /dq en-GB /o | Out-Null
    & $makepri new /pr $staging /cf $priConfig /of (Join-Path $staging 'resources.pri') /o | Out-Null
    Remove-Item -LiteralPath $priConfig -Force -ErrorAction SilentlyContinue
    Good 'resources.pri'
}

# -- pack ------------------------------------------------------------------

Step '5/6  Packing'
New-Item -ItemType Directory -Path (Join-Path $root 'dist') -Force | Out-Null
& $makeappx pack /d $staging /p $msix /o
if ($LASTEXITCODE -ne 0) { throw 'makeappx failed.' }
Good "dist\Murmur.msix is $([math]::Round((Get-Item $msix).Length / 1MB)) MB"

# -- optionally sign, for local testing only -------------------------------

Step '6/6  Signing'
if (-not $SelfSign) {
    Note 'skipped - upload this to Partner Center and Microsoft signs it'
} else {
    $signtool = Find-SdkTool 'signtool.exe'
    if (-not $signtool) { throw 'signtool.exe not found in the SDK.' }
    # The certificate subject must equal the manifest Publisher exactly, or
    # Windows refuses the package as signed by someone else.
    $cert = Get-ChildItem Cert:\CurrentUser\My |
        Where-Object { $_.Subject -eq $identity.publisher } |
        Select-Object -First 1
    if (-not $cert) {
        Note "creating a self-signed certificate for $($identity.publisher)"
        $cert = New-SelfSignedCertificate -Type Custom -Subject $identity.publisher `
            -KeyUsage DigitalSignature -FriendlyName 'Murmur test signing' `
            -CertStoreLocation 'Cert:\CurrentUser\My' `
            -TextExtension @('2.5.29.37={text}1.3.6.1.5.5.7.3.3', '2.5.29.19={text}')
    }
    $pfx = Join-Path $staging 'test.pfx'
    $password = ConvertTo-SecureString -String 'murmur' -Force -AsPlainText
    Export-PfxCertificate -Cert $cert -FilePath $pfx -Password $password | Out-Null
    & $signtool sign /fd SHA256 /a /f $pfx /p 'murmur' $msix
    if ($LASTEXITCODE -ne 0) { throw 'signtool failed.' }
    Remove-Item -LiteralPath $pfx -Force
    Good 'signed with a test certificate'
    Note 'To install it here, trust the certificate once (elevated):'
    Note "  Export-Certificate -Cert (Get-ChildItem Cert:\CurrentUser\My | ? Subject -eq '$($identity.publisher)') -FilePath murmur-test.cer"
    Note '  Import-Certificate -FilePath murmur-test.cer -CertStoreLocation Cert:\LocalMachine\TrustedPeople'
    Note "  Add-AppxPackage '$msix'"
}

Write-Host "`nDone." -ForegroundColor Cyan
Write-Host "  dist\Murmur.msix" -ForegroundColor Green
if (-not $SelfSign) {
    Write-Host "  Upload at partner.microsoft.com/dashboard - see STORE.md for the rest." -ForegroundColor DarkGray
}
