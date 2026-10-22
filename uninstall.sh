#!/usr/bin/env bash
# Remove labpipe from this computer (Linux, macOS). Your projects and your data are never touched.
#
#   ~/.local/share/labpipe/app/uninstall.sh            # shows what it removes, asks once
#   ~/.local/share/labpipe/app/uninstall.sh --dry-run  # only shows
#
# Removes, after one yes:
#   - every labpipe container (image localhost/labpipe…), its home volume (<name>-home) and the
#     images, with Podman and/or Docker, whichever is installed;
#   - the stored login (podman secret labpipe-claude-token; Docker: ~/.config/labpipe/claude.env);
#   - the pop-up proxy and the retired native units (~/.config/systemd/user/labpipe-*);
#   - the installation (LABPIPE_HOME, default ~/.local/share/labpipe: code and environment), the
#     `labpipe` link in ~/.local/bin (only if it points into that installation) and
#     ~/.config/labpipe (your defaults; keep them with --keep-config).
# Never: a project folder, a data folder, anything with sudo. A read-only view (full native
# install) and `podman-restart.service` (Podman's own, shared by all your containers) are only
# named, with the commands; you decide.
#
# Options: --yes (do not ask), --dry-run, --keep-config. Environment: LABPIPE_HOME, LABPIPE_BIN.

set -euo pipefail

main() {
    local yes=0 dry=0 keep_config=0
    for arg in "$@"; do
        case "$arg" in
            --yes|-y) yes=1 ;;
            --dry-run|-n) dry=1 ;;
            --keep-config) keep_config=1 ;;
            -h|--help) sed -n '2,19p' "$0" | sed 's/^# \{0,1\}//'; return 0 ;;
            *) echo "unknown option: $arg (see --help)" >&2; return 2 ;;
        esac
    done

    local home_dir="${LABPIPE_HOME:-$HOME/.local/share/labpipe}"
    local bin="${LABPIPE_BIN:-$HOME/.local/bin}"
    local config="${XDG_CONFIG_HOME:-$HOME/.config}/labpipe"
    local units="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
    local -a plan=() steps=()

    add() { plan+=("$1"); steps+=("$2"); }

    # --- containers, volumes, images, the login ------------------------------------------------
    local engine name image
    for engine in podman docker; do
        command -v "$engine" >/dev/null 2>&1 || continue
        "$engine" info >/dev/null 2>&1 || { echo "($engine is installed but not running: skipped)"; continue; }
        while read -r name image; do
            [ -n "$name" ] || continue
            case "$image" in localhost/labpipe*|labpipe*) ;; *) continue ;; esac
            add "$engine container $name (and its volume $name-home, if there)" \
                "$engine rm -f $name >/dev/null; $engine volume rm $name-home >/dev/null 2>&1 || true"
        done < <("$engine" ps -a --format '{{.Names}} {{.Image}}' 2>/dev/null)
        while read -r image; do
            [ -n "$image" ] && add "$engine image $image" "$engine image rm -f $image >/dev/null"
        done < <("$engine" images --format '{{.Repository}}:{{.Tag}}' 2>/dev/null \
                 | grep -E '^(localhost/)?labpipe(:|$)' | sort -u)
        if [ "$engine" = podman ] && podman secret inspect labpipe-claude-token >/dev/null 2>&1; then
            add "podman secret labpipe-claude-token (the stored login)" \
                "podman secret rm labpipe-claude-token >/dev/null"
        fi
    done

    # --- user units (pop-up proxy; retired native automation) --------------------------------
    local unit
    for unit in "$units"/labpipe-*; do
        [ -e "$unit" ] || continue
        add "systemd user unit $(basename "$unit")" \
            "systemctl --user disable --now $(basename "$unit") >/dev/null 2>&1 || true; rm -f '$unit'"
    done

    # --- the installation, the link, the defaults ---------------------------------------------
    [ -d "$home_dir" ] && add "the installation $home_dir (code and environment)" "rm -rf '$home_dir'"
    if [ -L "$bin/labpipe" ]; then
        case "$(readlink "$bin/labpipe")" in
            "$home_dir"/*) add "the link $bin/labpipe" "rm -f '$bin/labpipe'" ;;
            *) echo "($bin/labpipe points elsewhere: not labpipe's installation, left alone)" ;;
        esac
    fi
    if [ -d "$config" ]; then
        if [ "$keep_config" = 1 ]; then
            echo "(kept: $config, your defaults)"
        else
            add "$config (your defaults for new projects, the Docker login)" "rm -rf '$config'"
        fi
    fi

    if [ ${#plan[@]} -eq 0 ]; then
        echo "Nothing of labpipe found here."
    else
        echo "This removes:"
        printf '  - %s\n' "${plan[@]}"
        echo "Never touched: your project folders (e.g. ~/labpipe-projects/*) and your data folders."
    fi
    _notes "$units"
    [ ${#plan[@]} -eq 0 ] && return 0
    if [ "$dry" = 1 ]; then echo "(dry run: nothing removed)"; return 0; fi
    if [ "$yes" != 1 ]; then
        local answer
        read -r -p "Remove all of this? [y/N] " answer </dev/tty || answer=""
        case "$answer" in y|Y|yes|YES) ;; *) echo "Nothing removed."; return 0 ;; esac
    fi
    local i failed=0
    for i in "${!steps[@]}"; do
        if bash -c "${steps[$i]}"; then echo "removed: ${plan[$i]}"
        else echo "FAILED: ${plan[$i]}" >&2; failed=1; fi
    done
    [ "$failed" = 0 ] && echo "labpipe is uninstalled. To remove a project too, delete its folder yourself."
    return "$failed"
}

_notes() {
    # what is only named: it is shared with other things, or needs admin rights
    local units="$1"
    if command -v systemctl >/dev/null 2>&1 \
            && systemctl --user is-enabled podman-restart.service >/dev/null 2>&1; then
        echo "Left alone: podman-restart.service (enabled; it restarts ALL your Podman containers"
        echo "  with a restart policy, not only labpipe's). If nothing else needs it:"
        echo "    systemctl --user disable podman-restart.service"
    fi
    if [ -f /etc/fstab ] && grep -q 'labdata-ro' /etc/fstab 2>/dev/null; then
        echo "Left alone: a read-only view in /etc/fstab (full native install). To remove it (admin):"
        echo "    sudo umount <view>   # then delete its line from /etc/fstab"
    fi
}

# Everything runs from inside main, read in full before it starts: the script may delete the
# folder it lives in.
main "$@"; exit $?
