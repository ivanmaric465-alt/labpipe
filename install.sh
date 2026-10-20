#!/usr/bin/env bash
# labpipe installer for Linux and macOS.
#
#   git clone https://github.com/ivanmaric465-alt/labpipe ~/.local/share/labpipe/app && ~/.local/share/labpipe/app/install.sh
#
# Safe to rerun. Never uses sudo and never touches raw data. Everything lives under
# LABPIPE_HOME (default ~/.local/share/labpipe): app/ (this code), venv/. It:
#   0. in the installation's own clone (LABPIPE_HOME/app): switches to the newest release (tag
#      vX.Y.Z) and runs that release's installer; users get releases, never whatever is on main
#      (--main: stay on the clone's current state; --keep-checkout: stay on the release that is
#      checked out, which is what `labpipe update` uses after it switched the release itself;
#      no release yet: stays, says so; local changes in the clone: stays, says so),
#   1. finds Python >= 3.10 (>= 3.14 with --full),
#   2. creates the virtual environment (OUTSIDE synced folders),
#   3. installs labpipe: the slim host part (PyYAML only; the analysis runs in the
#      container). --full (or LABPIPE_FULL=1) also installs the analysis libraries from
#      requirements.lock, for a native install without a container (needs Python 3.14),
#   4. links `labpipe` into ~/.local/bin (override with LABPIPE_BIN),
#   5. marks app/ as an installation (`.labpipe-installed`),
#   6. asks the first questions (`labpipe setup --first`): container engine and agent tool
#      present?, models, login, your first project, the container. What needs admin rights
#      is shown for you to run, never run by this script.
#
# Options:  --main        install the development state (main), not the newest release
#                         (or LABPIPE_CHANNEL=main)
#           --keep-checkout   install what is checked out, switch nothing (or LABPIPE_CHANNEL=keep)
#           --full        also install the analysis libraries (native, Python 3.14)
#           --no-start    do not check tools, log in, build or start the container
#           --answers F   answer the questions from a YAML file (CI, tests)
#           --no-setup    stop after the environment (LABPIPE_NO_SETUP=1 does the same)
# Environment: LABPIPE_HOME, LABPIPE_VENV, LABPIPE_BIN, LABPIPE_PYTHON, LABPIPE_FULL,
#              LABPIPE_CONFIG_HOME, LABPIPE_ALLOW_SYNCED.

set -euo pipefail

ORIG_ARGS=("$@")

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
HOME_DIR="${LABPIPE_HOME:-$HOME/.local/share/labpipe}"
VENV="${LABPIPE_VENV:-$HOME_DIR/venv}"
BIN="${LABPIPE_BIN:-$HOME/.local/bin}"
FULL="${LABPIPE_FULL:-}"
NO_SETUP="${LABPIPE_NO_SETUP:-}"
CHANNEL="${LABPIPE_CHANNEL:-release}"
SETUP_ARGS=()

while [ $# -gt 0 ]; do
    case "$1" in
        --full) FULL=1 ;;
        --main) CHANNEL=main ;;
        --keep-checkout) CHANNEL=keep ;;
        --no-setup) NO_SETUP=1 ;;
        --no-start) SETUP_ARGS+=(--no-start) ;;
        --answers)
            [ $# -ge 2 ] || { echo "--answers needs a file" >&2; exit 2; }
            shift
            SETUP_ARGS+=(--answers "$(cd "$(dirname "$1")" && pwd -P)/$(basename "$1")") ;;
        -h|--help) sed -n '2,/^set -euo/p' "${BASH_SOURCE[0]}" | sed '$d' | sed 's/^# \{0,1\}//'; exit 0 ;;
        *) echo "unknown option: $1 (see --help)" >&2; exit 2 ;;
    esac
    shift
done

# The installation's own clone installs the newest release. (CI and developers run the installer in
# their own checkout, which is not LABPIPE_HOME/app: nothing is switched there.) A function, so that
# bash has read all of it before the checkout replaces this very file; then it starts over from the tag.
use_release() {
    [ "$CHANNEL" != main ] && [ "$CHANNEL" != keep ] && [ -z "${LABPIPE_SWITCHED:-}" ] && [ -e "$REPO/.git" ] &&
        [ -d "$HOME_DIR" ] && [ "$REPO" = "$(cd "$HOME_DIR" && pwd -P)/app" ] || return 0
    command -v git >/dev/null 2>&1 || return 0
    git -C "$REPO" fetch --tags --quiet 2>/dev/null ||
        echo "note: could not fetch the release tags (offline?); using the tags already here" >&2
    local latest
    latest="$(git -C "$REPO" tag --list 'v*' | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' |
              sort -t. -k1.2,1n -k2,2n -k3,3n | tail -n 1 || true)"
    if [ -z "$latest" ]; then
        echo "no release yet: installing main"
        return 0
    fi
    if [ -n "$(git -C "$REPO" status --porcelain)" ]; then
        echo "note: $REPO has local changes: not switching to $latest, installing the current state" >&2
        return 0
    fi
    if [ "$(git -C "$REPO" rev-parse HEAD)" != "$(git -C "$REPO" rev-parse "$latest^{commit}")" ]; then
        echo "==> Switching to the newest release, $latest"
        git -C "$REPO" -c advice.detachedHead=false checkout --quiet "$latest" ||
            { echo "note: could not switch to $latest: installing the current state" >&2; return 0; }
        LABPIPE_SWITCHED=1 exec bash "$REPO/install.sh" ${ORIG_ARGS[@]+"${ORIG_ARGS[@]}"}
    fi
    echo "    release $latest"
}
use_release

if [ "$FULL" = 1 ]; then MIN_MINOR=14; else MIN_MINOR=10; fi

say() { printf '\n==> %s\n' "$*"; }
die() { printf '\nERROR: %s\n' "$*" >&2; exit 1; }

# Never overwrite somebody else's `labpipe` (checked first, before anything is installed).
if [ -e "$BIN/labpipe" ] || [ -L "$BIN/labpipe" ]; then
    [ "$(readlink "$BIN/labpipe" || true)" = "$VENV/bin/labpipe" ] ||
        die "$BIN/labpipe exists and points elsewhere; remove it or set LABPIPE_BIN."
fi

say "1/6 Looking for Python >= 3.$MIN_MINOR"
PY=""
for candidate in "${LABPIPE_PYTHON:-}" python3.14 python3.13 python3.12 python3.11 python3.10 python3 python; do
    [ -n "$candidate" ] || continue
    if command -v "$candidate" >/dev/null 2>&1 &&
       "$candidate" -c "import sys; sys.exit(0 if sys.version_info >= (3, $MIN_MINOR) else 1)" 2>/dev/null; then
        PY="$(command -v "$candidate")"
        break
    fi
done
if [ -z "$PY" ]; then
    if [ "$FULL" = 1 ]; then
        die "Python 3.14 or newer not found (needed for --full). Install it (Linux: your package manager,
       e.g. 'sudo dnf install python3.14' or 'sudo apt install python3.14 python3.14-venv';
       macOS: 'brew install python@3.14' or python.org), or set LABPIPE_PYTHON=/path/to/python."
    fi
    die "Python 3.10 or newer not found. Install it (Linux: your package manager,
       e.g. 'sudo dnf install python3' or 'sudo apt install python3 python3-venv';
       macOS: 'brew install python' or python.org), or set LABPIPE_PYTHON=/path/to/python."
fi
echo "    $PY ($("$PY" -c 'import sys; print(sys.version.split()[0])'))"
"$PY" -c 'import venv, ensurepip' 2>/dev/null ||
    die "$PY cannot create virtual environments. Debian/Ubuntu: 'sudo apt install python3-venv'
       (or python3.X-venv for that version), then rerun this installer."

say "2/6 Virtual environment: $VENV"
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
if { synced_segment "$VENV" || synced_segment "$REPO"; } && [ "${LABPIPE_ALLOW_SYNCED:-}" != 1 ]; then
    die "$VENV (or $REPO) looks like a synced folder. Sync clients corrupt virtual environments;
       choose another LABPIPE_HOME (or set LABPIPE_ALLOW_SYNCED=1 if you are sure)."
fi
if [ -x "$VENV/bin/python" ] &&
   "$VENV/bin/python" -c "import sys; sys.exit(0 if sys.version_info >= (3, $MIN_MINOR) else 1)"; then
    echo "    exists, reusing"
else
    [ ! -e "$VENV" ] || die "$VENV exists but is not a Python >= 3.$MIN_MINOR environment; move it away first."
    mkdir -p "$(dirname "$VENV")"
    "$PY" -m venv "$VENV"
fi

if [ "$FULL" = 1 ]; then KIND="full: with the analysis libraries"; else KIND="host part: PyYAML only"; fi
say "3/6 Installing labpipe ($KIND)"
"$VENV/bin/python" -m pip install --quiet --upgrade pip
if [ "$FULL" = 1 ]; then
    "$VENV/bin/python" -m pip install --quiet -r "$REPO/requirements.lock"
    "$VENV/bin/python" -m pip install --quiet --no-deps -e "$REPO"
else
    "$VENV/bin/python" -m pip install --quiet -e "$REPO"
fi

say "4/6 Putting 'labpipe' on PATH: $BIN/labpipe"
mkdir -p "$BIN"
ln -sfn "$VENV/bin/labpipe" "$BIN/labpipe"
case ":$PATH:" in
    *":$BIN:"*) echo "    $BIN is on PATH" ;;
    *) echo "    NOTE: $BIN is not on PATH. Add this line to ~/.bashrc or ~/.zshrc:"
       echo "          export PATH=\"$BIN:\$PATH\"" ;;
esac

say "5/6 Installation marker"
mkdir -p "$HOME_DIR"
# How this installation was made (`labpipe update` reinstalls the same way)
if [ "$FULL" = 1 ]; then printf '{"mode": "full"}\n' > "$HOME_DIR/install.json"; else printf '{"mode": "host"}\n' > "$HOME_DIR/install.json"; fi
if [ "$REPO" = "$(cd "$HOME_DIR" && pwd -P)/app" ]; then
    touch "$REPO/.labpipe-installed"
    echo "    $REPO is the installation's app folder"
else
    echo "    $REPO is not $HOME_DIR/app: treated as a repository (developer install)"
    if [ "$FULL" = 1 ] && [ ! -f "$REPO/config/local.yaml" ]; then "$VENV/bin/labpipe" init >/dev/null; fi
fi

say "6/6 First questions (labpipe setup --first)"
if [ "$NO_SETUP" = 1 ]; then
    echo "    skipped; run 'labpipe setup --first' any time"
elif [ ${#SETUP_ARGS[@]} -eq 0 ] && { [ ! -t 0 ] || [ ! -t 1 ]; }; then
    echo "    skipped (no terminal); run 'labpipe setup --first' any time"
else
    # from the home folder, so that a project folder we happen to be in is not the context
    rc=0
    (cd "$HOME_DIR" && "$VENV/bin/labpipe" setup --first ${SETUP_ARGS[@]+"${SETUP_ARGS[@]}"}) || rc=$?
    [ "$rc" = 0 ] || exit "$rc"     # it said what to do (e.g. what to install first)
fi

cat <<EOF

Installed. More projects: labpipe project new <folder> --data <data folder>
EOF
