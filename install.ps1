# labpipe installer for Windows (PowerShell).
#
#   git clone https://github.com/ivanmaric465-alt/labpipe "$env:LOCALAPPDATA\labpipe\app"; & "$env:LOCALAPPDATA\labpipe\app\install.ps1"
#
# Safe to rerun. Needs no administrator rights and never touches raw data. Everything lives
# under $env:LABPIPE_HOME (default %LOCALAPPDATA%\labpipe): app\ (this code), venv\. It:
#   0. in the installation's own clone (%LOCALAPPDATA%\labpipe\app): switches to the newest release
#      (tag vX.Y.Z) and runs that release's installer; users get releases, never whatever is on
#      main (-Main: stay on the clone's current state; no release yet or local changes in the
#      clone: stays, says so),
#   1. finds Python >= 3.10 (>= 3.14 with -Full),
#   2. creates the virtual environment OUTSIDE synced folders,
#   3. installs labpipe: the slim host part (PyYAML only; the analysis runs in the container).
#      -Full (or $env:LABPIPE_FULL = "1") also installs the analysis libraries from
#      requirements.lock, for a native install without a container (needs Python 3.14),
#   4. adds the venv's Scripts folder to your user PATH,
#   5. marks app\ as an installation (.labpipe-installed),
#   6. asks the first questions (`labpipe setup --first`): container engine and agent tool
#      present?, models, login, your first project, the container. What needs admin rights is
#      shown for you to run, never run by this script.
# Options: -Main (the development state, not the newest release; or $env:LABPIPE_CHANNEL = "main"), -Full, -NoStart (no tool checks, login, build or start), -Answers FILE.yaml (CI, tests),
#          -NoSetup (stop after the environment; $env:LABPIPE_NO_SETUP = "1" does the same).
# Run:  powershell -ExecutionPolicy Bypass -File .\install.ps1
# (Windows is untested on real machines apart from CI: the container needs Podman or Docker
# Desktop with WSL2.)

param([switch]$Main, [switch]$Full, [switch]$NoStart, [switch]$NoSetup, [string]$Answers = "")

$ErrorActionPreference = "Stop"
$Repo = (Resolve-Path $PSScriptRoot).Path
$HomeDir = if ($env:LABPIPE_HOME) { $env:LABPIPE_HOME } else { Join-Path $env:LOCALAPPDATA "labpipe" }
$Venv = if ($env:LABPIPE_VENV) { $env:LABPIPE_VENV } else { Join-Path $HomeDir "venv" }
if ($env:LABPIPE_FULL -eq "1") { $Full = $true }
if ($env:LABPIPE_NO_SETUP -eq "1") { $NoSetup = $true }
if ($env:LABPIPE_CHANNEL -eq "main") { $Main = $true }
# The installation's own clone installs the newest release (CI and developers run the installer in
# their own checkout, which is not LABPIPE_HOME\app: nothing is switched there).
if ((-not $Main) -and (-not $env:LABPIPE_SWITCHED) -and (Test-Path (Join-Path $Repo ".git")) -and (Test-Path $HomeDir) -and
    ($Repo -eq ((Resolve-Path $HomeDir).Path + "\app")) -and (Get-Command git -ErrorAction SilentlyContinue)) {
    & git -C $Repo fetch --tags --quiet 2>$null
    if ($LASTEXITCODE -ne 0) { Write-Host "note: could not fetch the release tags (offline?); using the tags already here" }
    $Latest = @(& git -C $Repo tag --list "v*" | Where-Object { $_ -match '^v\d+\.\d+\.\d+$' } |
        Sort-Object { [version]$_.Substring(1) }) | Select-Object -Last 1
    if (-not $Latest) { Write-Host "no release yet: installing main" }
    elseif (& git -C $Repo status --porcelain) { Write-Host "note: $Repo has local changes: not switching to $Latest, installing the current state" }
    elseif ((& git -C $Repo rev-parse HEAD) -ne (& git -C $Repo rev-parse "$Latest^{commit}")) {
        Write-Host "==> Switching to the newest release, $Latest"
        & git -C $Repo -c advice.detachedHead=false checkout --quiet $Latest
        if ($LASTEXITCODE -eq 0) {
            $env:LABPIPE_SWITCHED = "1"
            $Forward = @{ Full = $Full; NoStart = $NoStart; NoSetup = $NoSetup }.GetEnumerator() | Where-Object { $_.Value } | ForEach-Object { "-$($_.Key)" }
            if ($Answers) { $Forward += @("-Answers", $Answers) }
            & (Get-Process -Id $PID).Path -ExecutionPolicy Bypass -File (Join-Path $Repo "install.ps1") @Forward
            exit $LASTEXITCODE
        }
        Write-Host "note: could not switch to ${Latest}: installing the current state"
    }
}
$MinMinor = if ($Full) { 14 } else { 10 }
function Say($text) { Write-Host "`n==> $text" }
function Test-Py($exe, $extra) {
    try { & $exe @extra -c "import sys; sys.exit(0 if sys.version_info >= (3, $MinMinor) else 1)" 2>$null; return $LASTEXITCODE -eq 0 }
    catch { return $false }
}

Say "1/6 Looking for Python >= 3.$MinMinor"
$Py = $null; $PyArgs = @()
if ($env:LABPIPE_PYTHON -and (Test-Py $env:LABPIPE_PYTHON @())) { $Py = $env:LABPIPE_PYTHON }
elseif (Get-Command py -ErrorAction SilentlyContinue) {
    foreach ($minor in 14, 13, 12, 11, 10) {
        if (($minor -ge $MinMinor) -and (Test-Py "py" @("-3.$minor"))) { $Py = "py"; $PyArgs = @("-3.$minor"); break }
    }
}
if (-not $Py -and (Get-Command python -ErrorAction SilentlyContinue) -and (Test-Py "python" @())) { $Py = "python" }
if (-not $Py) { throw "Python 3.$MinMinor or newer not found. Install it from python.org (tick 'Add to PATH') or with: winget install Python.Python.3.14 ; or set LABPIPE_PYTHON." }

Say "2/6 Virtual environment: $Venv"
foreach ($path in $Venv, $Repo) {
    if ($path.ToLower() -match "(^|[\\/])(onedrive|dropbox|google drive|googledrive|icloud|nextcloud|owncloud|koofr|syncthing|megasync|mega([\\/]|$)|seafile)") {
        if ($env:LABPIPE_ALLOW_SYNCED -ne "1") { throw "$path looks like a synced folder; choose another LABPIPE_HOME." }
    }
}
$VenvPy = Join-Path $Venv "Scripts\python.exe"
if ((Test-Path $VenvPy) -and (Test-Py $VenvPy @())) { Write-Host "    exists, reusing" }
else {
    if (Test-Path $Venv) { throw "$Venv exists but is not a Python >= 3.$MinMinor environment; move it away first." }
    New-Item -ItemType Directory -Force -Path (Split-Path $Venv) | Out-Null
    & $Py @PyArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "creating the virtual environment failed" }
}

Say "3/6 Installing labpipe ($(if ($Full) { 'full: with the analysis libraries' } else { 'host part: PyYAML only' }))"
& $VenvPy -m pip install --quiet --upgrade pip
if ($Full) {
    & $VenvPy -m pip install --quiet -r (Join-Path $Repo "requirements.lock")
    if ($LASTEXITCODE -ne 0) { throw "installing requirements.lock failed" }
    & $VenvPy -m pip install --quiet --no-deps -e $Repo
} else {
    & $VenvPy -m pip install --quiet -e $Repo
}
if ($LASTEXITCODE -ne 0) { throw "installing labpipe failed" }

Say "4/6 Putting 'labpipe' on your user PATH"
$Scripts = Join-Path $Venv "Scripts"
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not ($UserPath -split ";" | Where-Object { $_ -eq $Scripts })) {
    [Environment]::SetEnvironmentVariable("Path", (($UserPath, $Scripts) -ne "" -join ";"), "User")
    Write-Host "    added $Scripts (open a new terminal for it to take effect)"
} else { Write-Host "    already on PATH" }
$env:Path = "$Scripts;$env:Path"
$LabPipe = Join-Path $Scripts "labpipe.exe"

Say "5/6 Installation marker"
New-Item -ItemType Directory -Force -Path $HomeDir | Out-Null
if ($Repo -eq ((Resolve-Path $HomeDir).Path + "\app")) {
    New-Item -ItemType File -Force -Path (Join-Path $Repo ".labpipe-installed") | Out-Null
    Write-Host "    $Repo is the installation's app folder"
} else {
    Write-Host "    $Repo is not $HomeDir\app: treated as a repository (developer install)"
    if ($Full -and -not (Test-Path (Join-Path $Repo "config\local.yaml"))) { & $LabPipe init | Out-Null }
}

Say "6/6 First questions (labpipe setup --first)"
$SetupArgs = @("setup", "--first")
if ($NoStart) { $SetupArgs += "--no-start" }
if ($Answers) { $SetupArgs += @("--answers", (Resolve-Path $Answers).Path) }
if ($NoSetup) { Write-Host "    skipped; run 'labpipe setup --first' any time" }
elseif (-not $Answers -and [Console]::IsInputRedirected) { Write-Host "    skipped (no terminal); run 'labpipe setup --first' any time" }
else {
    Push-Location $HomeDir
    try { & $LabPipe @SetupArgs; $rc = $LASTEXITCODE } finally { Pop-Location }
    if ($rc -ne 0) { exit $rc }
}
Write-Host "`nInstalled. More projects: labpipe project new <folder> --data <data folder>"
exit 0
