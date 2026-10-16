#!/usr/bin/env bash
# labpipe installer for Linux and macOS.
#
# Safe to rerun. Never uses sudo and never touches raw data. It:
#   1. finds Python >= 3.14,
#   2. creates a virtual environment OUTSIDE synced folders
#      (default ~/.local/share/labpipe/venv; override with LABPIPE_VENV),
#   3. installs the pinned versions from requirements.lock and labpipe itself,
#   4. links `labpipe` into ~/.local/bin (override with LABPIPE_BIN),
#   5. creates config/local.yaml if missing (`labpipe init`),
#   6. offers the guided setup (`labpipe setup`): the raw-data folder and its
#      read-only view, the sandbox check, models per agent role, and automatic
#      processing. Each part asks first; commands needing admin rights are shown
#      for you to run, never run by the script. Skip it with LABPIPE_NO_SETUP=1,
#   7. runs `labpipe doctor`, which lists whatever is still left.

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
VENV="${LABPIPE_VENV:-$HOME/.local/share/labpipe/venv}"
BIN="${LABPIPE_BIN:-$HOME/.local/bin}"

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

say "1/7 Looking for Python >= 3.14"
PY=""
for candidate in "${LABPIPE_PYTHON:-}" python3.14 python3 python; do
    [ -n "$candidate" ] || continue
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)' 2>/dev/null; then
        PY="$(command -v "$candidate")"
        break
    fi
done
[ -n "$PY" ] || die "Python 3.14 or newer not found. Install it (Linux: your package manager,
       e.g. 'sudo dnf install python3.14' or 'sudo apt install python3.14 python3.14-venv';
       macOS: 'brew install python@3.14' or python.org), or set LABPIPE_PYTHON=/path/to/python."
echo "    $PY ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"

say "2/7 Virtual environment: $VENV"
synced_segment() {  # a path segment named like a sync client's folder
    local IFS=/ seg
    for seg in $1; do
        case "$(printf '%s' "$seg" | tr '[:upper:]' '[:lower:]')" in
            dropbox*|onedrive*|"google drive"*|googledrive*|icloud*|nextcloud*|owncloud*|koofr*|syncthing*|mega|megasync*|"seafile"*)
                return 0 ;;
        esac
    done
    return 1
}
if synced_segment "$VENV" && [ "${LABPIPE_ALLOW_SYNCED:-}" != 1 ]; then
    die "$VENV looks like a synced folder. Sync clients corrupt virtual environments;
       choose another LABPIPE_VENV (or set LABPIPE_ALLOW_SYNCED=1 if you are sure)."
fi
if [ -x "$VENV/bin/python" ] &&
   "$VENV/bin/python" -c 'import sys; sys.exit(0 if sys.version_info >= (3, 14) else 1)'; then
    echo "    exists, reusing"
else
    [ ! -e "$VENV" ] || die "$VENV exists but is not a Python >= 3.14 environment; move it away first."
    "$PY" -m venv "$VENV"
fi

say "3/7 Installing pinned dependencies and labpipe"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
"$VENV/bin/python" -m pip install --quiet -r "$REPO/requirements.lock"
"$VENV/bin/python" -m pip install --quiet --no-deps -e "$REPO"

say "4/7 Putting 'labpipe' on PATH: $BIN/labpipe"
mkdir -p "$BIN"
if [ -e "$BIN/labpipe" ] && [ "$(readlink "$BIN/labpipe" || true)" != "$VENV/bin/labpipe" ]; then
    die "$BIN/labpipe exists and points elsewhere; remove it or set LABPIPE_BIN."
fi
ln -sfn "$VENV/bin/labpipe" "$BIN/labpipe"
case ":$PATH:" in
    *":$BIN:"*) echo "    $BIN is on PATH" ;;
    *) echo "    NOTE: $BIN is not on PATH. Add this line to ~/.bashrc or ~/.zshrc:"
       echo "          export PATH=\"$BIN:\$PATH\"" ;;
esac

say "5/7 Machine settings (config/local.yaml)"
"$VENV/bin/labpipe" init

say "6/7 Guided setup (labpipe setup)"
if [ -t 0 ] && [ -t 1 ] && [ "${LABPIPE_NO_SETUP:-}" != 1 ]; then
    read -r -p "    Set up the raw-data folder, sandbox, agents and automation now? [Y/n] " answer
    case "${answer:-y}" in
        [Yy]*) "$VENV/bin/labpipe" setup || true ;;
        *) echo "    skipped; run 'labpipe setup' any time" ;;
    esac
else
    echo "    skipped (no terminal, or LABPIPE_NO_SETUP=1); run 'labpipe setup' any time"
fi

say "7/7 Setup check (labpipe doctor)"
"$VENV/bin/labpipe" doctor || true

cat <<EOF

Installed. 'labpipe doctor' above lists what is left (file naming, references,
a first analysis). Easiest: open this folder in your AI agent (e.g. Claude Code)
and say "set this up for me"; it reads 'labpipe doctor' and guides you through it.
EOF
