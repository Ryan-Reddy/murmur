<#
    Sets up cufflink on a fresh Windows machine.

    Clone the repo, right-click this file and pick "Run with PowerShell" (or run
    it from a terminal). It creates the virtual environment, installs the
    dependencies, downloads the two Kokoro model files -- which are far too big
    for git, so they are not in the clone -- and offers to start cufflink with
    Windows.

    Safe to run again: anything already in place is left alone.
#>

[CmdletBinding()]
param(
    # Skip the "start cufflink when I sign in" question and just do it.
    [switch] $Autostart,
    # Re-download the models even if they are already here and intact.
    [switch] $ForceModels,
    # Never ask anything -- for scripted installs and for testing.
    [switch] $Unattended,
    # Set everything up but do not start cufflink at the end.
    [switch] $NoLaunch,
    # Where to put cufflink when this is run straight from the web.
    [string] $InstallDir = (Join-Path $HOME 'cufflink')
)

$ErrorActionPreference = 'Stop'
# Windows PowerShell still negotiates TLS 1.0 on some machines, which GitHub
# refuses.
[Net.ServicePointManager]::SecurityProtocol = [Net.SecurityProtocolType]::Tls12

$REPO = 'https://github.com/Ryan-Reddy/cufflink'
$RELEASE = 'https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.0'
$MODELS = @(
    @{ Name = 'kokoro-v1.0.onnx'; Bytes = 325532387
       Sha  = '7d5df8ecf7d4b1878015a32686053fd0eebe2bc377234608764cc0ef3636a6c5' }
    @{ Name = 'voices-v1.0.bin';  Bytes = 28214398
       Sha  = 'bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d' }
)

function Say  ($m) { Write-Host "  $m" }
function Step ($m) { Write-Host "`n$m" -ForegroundColor Cyan }
function Good ($m) { Write-Host "  $m" -ForegroundColor Green }
function Warn ($m) { Write-Host "  $m" -ForegroundColor Yellow }

# --- 0. Bootstrap ----------------------------------------------------------
# Piped from the web there is no file on disk, so $PSCommandPath is empty and
# there is no project next to us yet. Fetch it, then hand over to the copy that
# lands there -- which takes the normal path below.
$here = if ($PSCommandPath) { Split-Path -Parent $PSCommandPath } else { $null }
if (-not $here -or -not (Test-Path (Join-Path $here 'cufflink.py'))) {
    # The banner belongs to the real run, which starts once this hands over.
    Step "Fetching cufflink into $InstallDir"

    if (Test-Path (Join-Path $InstallDir 'cufflink.py')) {
        Good 'Already downloaded'
        if ((Test-Path (Join-Path $InstallDir '.git')) -and
            (Get-Command git -ErrorAction SilentlyContinue)) {
            Say 'Updating to the latest version...'
            git -C $InstallDir pull --ff-only --quiet
        }
    } elseif (Get-Command git -ErrorAction SilentlyContinue) {
        git clone --depth 1 --quiet $REPO $InstallDir
        if ($LASTEXITCODE -ne 0) { throw "Could not clone $REPO." }
        Good 'Cloned'
    } else {
        # No git on this machine: GitHub will hand us a zip of the branch.
        Say 'No git here, taking the zip instead...'
        $zip = Join-Path $env:TEMP 'cufflink-main.zip'
        $stage = Join-Path $env:TEMP "cufflink-unpack-$PID"
        $progress = $ProgressPreference
        try {
            $ProgressPreference = 'SilentlyContinue'
            Invoke-WebRequest "$REPO/archive/refs/heads/main.zip" -OutFile $zip -UseBasicParsing
        } finally { $ProgressPreference = $progress }
        Expand-Archive $zip $stage -Force
        $parent = Split-Path $InstallDir
        if ($parent) { New-Item -ItemType Directory -Force -Path $parent | Out-Null }
        Move-Item (Join-Path $stage 'cufflink-main') $InstallDir
        Remove-Item $zip, $stage -Recurse -Force -ErrorAction SilentlyContinue
        Good 'Downloaded'
    }

    & (Join-Path $InstallDir 'install.ps1') @PSBoundParameters
    exit $LASTEXITCODE
}

$root = $here
$modelDir = Join-Path $root 'models'
$venv = Join-Path $root 'venv'
$pythonw = Join-Path $venv 'Scripts\pythonw.exe'

Write-Host "`ncufflink setup" -ForegroundColor Cyan
Say "Reads your selected text aloud, offline. Nothing leaves this machine."
Say $root

# --- 1. Python -------------------------------------------------------------
Step '1/4  Looking for Python 3.10 or newer'

function Find-Python {
    # One call, and no stderr redirection: in Windows PowerShell, redirecting a
    # native command's stderr turns each line into an error record. The probe
    # avoids % and double quotes too -- both get mangled on the way to python.
    $probe = 'import sys; print(sys.executable); print(sys.version_info.major); print(sys.version_info.minor)'
    foreach ($candidate in @(
        @{ Exe = 'py';     Pre = @('-3') }
        @{ Exe = 'python'; Pre = @() }
    )) {
        if (-not (Get-Command $candidate.Exe -ErrorAction SilentlyContinue)) { continue }
        $out = $null
        try { $out = & $candidate.Exe @($candidate.Pre + @('-c', $probe)) } catch { continue }
        if ($LASTEXITCODE -ne 0) { continue }
        $lines = @($out)
        if ($lines.Count -lt 3) { continue }
        $exe = "$($lines[0])".Trim()
        # The Microsoft Store stub sits on PATH but is not a real interpreter.
        if (-not $exe -or $exe -like '*WindowsApps*') { continue }
        $version = [version] "$($lines[1]).$($lines[2])"
        if ($version -ge [version]'3.10') { return @{ Exe = $exe; Version = $version } }
    }
    return $null
}

$python = Find-Python
if (-not $python) {
    Warn 'No suitable Python found.'
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        Say 'Installing Python 3.12 with winget (this opens its own progress)...'
        winget install --id Python.Python.3.12 --source winget `
            --accept-package-agreements --accept-source-agreements
        # winget updates PATH only for new shells, so pick it up by hand.
        $env:Path = [Environment]::GetEnvironmentVariable('Path', 'Machine') + ';' +
                    [Environment]::GetEnvironmentVariable('Path', 'User')
        $python = Find-Python
    }
    if (-not $python) {
        Warn 'Install Python from https://www.python.org/downloads/ (tick'
        Warn '"Add python.exe to PATH"), then run this script again.'
        if (-not $Unattended) { Read-Host "`nPress Enter to close" }
        exit 1
    }
}
Good "Python $($python.Version) at $($python.Exe)"

# --- 2. Virtual environment + dependencies ---------------------------------
Step '2/4  Setting up the virtual environment'
if (-not (Test-Path $pythonw)) {
    & $python.Exe -m venv $venv
    if ($LASTEXITCODE -ne 0) { throw 'Could not create the virtual environment.' }
    Good 'Created venv\'
} else {
    Good 'venv\ already there'
}

$pip = Join-Path $venv 'Scripts\python.exe'
Say 'Installing dependencies (a few minutes the first time)...'
& $pip -m pip install --upgrade pip --quiet --disable-pip-version-check
& $pip -m pip install -r (Join-Path $root 'requirements.txt') --quiet --disable-pip-version-check
if ($LASTEXITCODE -ne 0) { throw 'Dependency install failed.' }
Good 'Dependencies installed'

# --- 3. Model files --------------------------------------------------------
Step '3/4  Fetching the voice model (338 MB, one time)'
New-Item -ItemType Directory -Force -Path $modelDir | Out-Null

foreach ($model in $MODELS) {
    $path = Join-Path $modelDir $model.Name
    if ((Test-Path $path) -and -not $ForceModels) {
        if ((Get-Item $path).Length -eq $model.Bytes) {
            Say "$($model.Name) already here, checking it..."
            if ((Get-FileHash $path -Algorithm SHA256).Hash.ToLower() -eq $model.Sha) {
                Good "$($model.Name) verified"
                continue
            }
            Warn "$($model.Name) is corrupt, downloading again"
        } else {
            Warn "$($model.Name) is the wrong size, downloading again"
        }
    }
    $tmp = "$path.part"
    Say "Downloading $($model.Name) ($([math]::Round($model.Bytes / 1MB)) MB)..."
    $progress = $ProgressPreference
    try {
        # Invoke-WebRequest's progress bar makes a large download several times
        # slower; the message above is enough.
        $ProgressPreference = 'SilentlyContinue'
        Invoke-WebRequest -Uri "$RELEASE/$($model.Name)" -OutFile $tmp -UseBasicParsing
    } finally { $ProgressPreference = $progress }

    if ((Get-Item $tmp).Length -ne $model.Bytes) {
        Remove-Item $tmp -Force
        throw "$($model.Name) downloaded the wrong size. Check the connection and retry."
    }
    if ((Get-FileHash $tmp -Algorithm SHA256).Hash.ToLower() -ne $model.Sha) {
        Remove-Item $tmp -Force
        throw "$($model.Name) failed its checksum. Nothing was installed."
    }
    Move-Item $tmp $path -Force
    Good "$($model.Name) downloaded and verified"
}

# --- 4. Shortcuts ----------------------------------------------------------
Step '4/4  Shortcuts'

function New-Shortcut ($linkPath, $description) {
    # Points at cufflink.cmd rather than pythonw so the banner shows on every
    # launch, including at sign-in. The cmd closes itself; cufflink stays in the
    # tray.
    $shell = New-Object -ComObject WScript.Shell
    $link = $shell.CreateShortcut($linkPath)
    $link.TargetPath = Join-Path $root 'cufflink.cmd'
    $link.WorkingDirectory = $root
    $link.Description = $description
    $icon = Join-Path $root 'assets\cufflink.ico'
    if (Test-Path $icon) { $link.IconLocation = $icon }
    $link.Save()
}

$desktop = Join-Path ([Environment]::GetFolderPath('Desktop')) 'cufflink.lnk'
New-Shortcut $desktop 'Read the selected text aloud'
Good 'Desktop shortcut created'

$wantsAutostart = $Autostart
if (-not $wantsAutostart -and -not $Unattended) {
    $answer = Read-Host '  Start cufflink automatically when you sign in? [Y/n]'
    $wantsAutostart = $answer -notmatch '^\s*n'
}
if ($wantsAutostart) {
    $startup = Join-Path ([Environment]::GetFolderPath('Startup')) 'cufflink.lnk'
    New-Shortcut $startup 'Read the selected text aloud'
    Good 'cufflink will start with Windows'
} else {
    Say 'Skipped autostart -- launch it from the desktop shortcut.'
}

# --- done ------------------------------------------------------------------
Write-Host "`nReady." -ForegroundColor Green
Say 'cufflink lives in the system tray (a purple speaker, green while reading).'
Say ''
Say '  Ctrl+Alt+M       read whatever text is selected'
Say '  Ctrl+Alt+Space   pause / resume'
Say '  Ctrl+Alt+S       stop'
Say ''
Say 'The first launch takes a few seconds to load the model.'

$launch = $true
if ($NoLaunch) {
    $launch = $false
} elseif (-not $Unattended) {
    $launch = (Read-Host "`n  Start cufflink now? [Y/n]") -notmatch '^\s*n'
}
if ($launch) {
    Start-Process -FilePath $pythonw `
        -ArgumentList ('"' + (Join-Path $root 'cufflink.py') + '"') -WorkingDirectory $root
    Good 'Starting -- watch for the tray icon.'
}
if (-not $Unattended) { Read-Host "`nPress Enter to close" }
