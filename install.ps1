# labpipe installer for Windows (PowerShell).
#
# Safe to rerun. Needs no administrator rights and never touches raw data. It:
#   1. finds Python >= 3.14 (py launcher or python on PATH),
#   2. creates a virtual environment OUTSIDE synced folders
#      (default %LOCALAPPDATA%\labpipe\venv; override with $env:LABPIPE_VENV),
#   3. installs the pinned versions from requirements.lock and labpipe itself,
#   4. adds the venv's Scripts folder to your user PATH,
#   5. creates config\local.yaml if missing (`labpipe init`),
#   6. offers the guided setup (`labpipe setup`): raw-data folder and its read-only
#      view, sandbox check, models per agent role, automatic processing. Commands
#      needing administrator rights are shown for you to run, never run by the
#      script. Skip it with $env:LABPIPE_NO_SETUP = "1",
#   7. runs `labpipe doctor`, which lists whatever is still left.
# Run:  powershell -ExecutionPolicy Bypass -File .\install.ps1

$ErrorActionPreference = "Stop"
$Repo = $PSScriptRoot
$Venv = if ($env:LABPIPE_VENV) { $env:LABPIPE_VENV } else { Join-Path $env:LOCALAPPDATA "labpipe\venv" }
function Say($text) { Write-Host "`n==> $text" }
function Test-Py($exe, $extra) {
    try { & $exe @extra -c "import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)" 2>$null; return $LASTEXITCODE -eq 0 }
    catch { return $false }
}

Say "1/7 Looking for Python >= 3.14"
$Py = $null; $PyArgs = @()
if ($env:LABPIPE_PYTHON -and (Test-Py $env:LABPIPE_PYTHON @())) { $Py = $env:LABPIPE_PYTHON }
elseif ((Get-Command py -ErrorAction SilentlyContinue) -and (Test-Py "py" @("-3.14"))) { $Py = "py"; $PyArgs = @("-3.14") }
elseif ((Get-Command python -ErrorAction SilentlyContinue) -and (Test-Py "python" @())) { $Py = "python" }
if (-not $Py) { throw "Python 3.14 or newer not found. Install it from python.org (tick 'Add to PATH') or set LABPIPE_PYTHON." }

Say "2/7 Virtual environment: $Venv"
if ($Venv.ToLower() -match "(^|[\\/])(onedrive|dropbox|google drive|googledrive|icloud|nextcloud|owncloud|koofr|syncthing|megasync|mega([\\/]|$)|seafile)") {
    if ($env:LABPIPE_ALLOW_SYNCED -ne "1") { throw "$Venv looks like a synced folder; choose another LABPIPE_VENV." }
}
$VenvPy = Join-Path $Venv "Scripts\python.exe"
if ((Test-Path $VenvPy) -and (Test-Py $VenvPy @())) { Write-Host "    exists, reusing" }
else {
    if (Test-Path $Venv) { throw "$Venv exists but is not a Python >= 3.14 environment; move it away first." }
    & $Py @PyArgs -m venv $Venv
    if ($LASTEXITCODE -ne 0) { throw "creating the virtual environment failed" }
}

Say "3/7 Installing pinned dependencies and labpipe"
& $VenvPy -m pip install --quiet --upgrade pip
& $VenvPy -m pip install --quiet -r (Join-Path $Repo "requirements.lock")
if ($LASTEXITCODE -ne 0) { throw "installing requirements.lock failed" }
& $VenvPy -m pip install --quiet --no-deps -e $Repo
if ($LASTEXITCODE -ne 0) { throw "installing labpipe failed" }

Say "4/7 Putting 'labpipe' on your user PATH"
$Scripts = Join-Path $Venv "Scripts"
$UserPath = [Environment]::GetEnvironmentVariable("Path", "User")
if (-not ($UserPath -split ";" | Where-Object { $_ -eq $Scripts })) {
    [Environment]::SetEnvironmentVariable("Path", (($UserPath, $Scripts) -ne "" -join ";"), "User")
    Write-Host "    added $Scripts (open a new terminal for it to take effect)"
} else { Write-Host "    already on PATH" }
$env:Path = "$Scripts;$env:Path"

Say "5/7 Machine settings (config\local.yaml)"
& (Join-Path $Scripts "labpipe.exe") init

Say "6/7 Guided setup (labpipe setup)"
if (-not [Console]::IsInputRedirected -and $env:LABPIPE_NO_SETUP -ne "1") {
    $answer = Read-Host "    Set up the raw-data folder, sandbox, agents and automation now? [Y/n]"
    if ($answer -eq "" -or $answer -match "^[Yy]") { & (Join-Path $Scripts "labpipe.exe") setup }
    else { Write-Host "    skipped; run 'labpipe setup' any time" }
} else { Write-Host "    skipped (no terminal, or LABPIPE_NO_SETUP=1); run 'labpipe setup' any time" }

Say "7/7 Setup check (labpipe doctor)"
& (Join-Path $Scripts "labpipe.exe") doctor
Write-Host "`nInstalled. 'labpipe doctor' above lists what is left. Easiest: open this folder in your AI agent (e.g. Claude Code) and say 'set this up for me'."
exit 0
