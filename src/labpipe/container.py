"""Container mode: build, start and use the labpipe container (Podman or Docker).

Container mode is ON for this machine when config/local.yaml has a `container:` block
(`labpipe container setup` writes it). Then most `labpipe` commands typed on the host are
run inside the running container (`forward`), so every analysis uses the same image and
raw data is only read through the read-only mount. See docs/design/container.md.

Everything that builds a command line is a plain function (`run_args`, `forward_argv`),
so it can be tested without an engine.
"""

from __future__ import annotations

import getpass
import hashlib
import json
import os
import re
import secrets
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path, PurePosixPath

import yaml

from labpipe import safety
from labpipe.safety import SetupError, load_pipeline_config

REPO_MOUNT = "/labpipe"
HOME_MOUNT = "/home/labpipe"
HASH_LABEL = "labpipe.container_hash"
SECRET_NAME = "labpipe-claude-token"
SAFE_ID = re.compile(r"[A-Za-z0-9_.-]+")
# Commands that always run on the host (the guard must be fast; the rest are host tasks).
LOCAL_COMMANDS = {"guard", "container", "shell", "setup", "init", "doctor", "agents",
                  "readonly-view", "methods"}
# Only the entrypoint (root) uses them, to set up the firewall and proxy; it then drops
# every one (SETPCAP lets it empty the bounding set) before running anything as the user.
CAPABILITIES = ("NET_ADMIN", "SETUID", "SETGID", "CHOWN", "DAC_OVERRIDE", "FOWNER", "KILL",
                "SETPCAP")
HARNESS_PROGRAM = {"claude-code": "claude", "codex": "codex"}
UNIT_NAME = "labpipe-dbus-proxy.service"
BUS_DIR = "labpipe-bus"           # under $XDG_RUNTIME_DIR on the host
BUS_MOUNT = "/run/labpipe-bus"
DEFAULTS = {"name": "labpipe", "image": "localhost/labpipe", "port": 8765, "allowed_hosts": {}}


class ContainerError(RuntimeError):
    pass


class ForwardError(ContainerError):
    pass


def _root() -> Path:
    return Path(safety.PROJECT_ROOT)


def _say(value) -> None:
    print(json.dumps(value, indent=1, ensure_ascii=False))


# ---- configuration -------------------------------------------------------------------

def local_container_block() -> dict | None:
    """The `container:` block of config/local.yaml, or None (container mode is off)."""
    path = _root() / "config" / "local.yaml"
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return None
    block = data.get("container") if isinstance(data, dict) else None
    return block if isinstance(block, dict) else None


def container_config(config: dict) -> dict:
    """pipeline.yaml's `container:` block with this machine's `container:` block over it."""
    merged = {**DEFAULTS, **(config.get("container") or {})}
    merged.update(local_container_block() or {})
    return merged


def load_config() -> dict:
    config = load_pipeline_config()
    config["container"] = container_config(config)
    return config


def detect_engine(configured: str | None = None) -> str:
    """The engine from local.yaml, else podman if installed, else docker."""
    if configured:
        if configured not in ("podman", "docker"):
            raise ContainerError(f"container.engine must be podman or docker, not {configured!r}")
        return configured
    for engine in ("podman", "docker"):
        if shutil.which(engine):
            return engine
    raise ContainerError("neither podman nor docker is installed (install Podman: "
                         "https://podman.io/docs/installation)")


def engine_version(engine: str) -> str:
    """'podman 5.8.7'-style string (empty if the engine does not answer)."""
    try:
        out = subprocess.run([engine, "--version"], capture_output=True, text=True,
                             timeout=30).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""
    match = re.search(r"(\d+\.\d+(?:\.\d+)?)", out)
    return f"{engine} {match.group(1)}" if match else out.strip()


def current_engine() -> str:
    return detect_engine((local_container_block() or {}).get("engine"))


# ---- image hash ----------------------------------------------------------------------

def container_hash() -> str:
    """12 hex chars over every file under container/ and pyproject.toml (paths + contents)."""
    root = _root()
    files = sorted(p for p in (root / "container").rglob("*") if p.is_file())
    files.append(root / "pyproject.toml")
    digest = hashlib.sha256()
    for path in sorted(files, key=lambda p: p.relative_to(root).as_posix()):
        digest.update(path.relative_to(root).as_posix().encode() + b"\0")
        digest.update(path.read_bytes() + b"\0")
    return digest.hexdigest()[:12]


def image_tag(cfg: dict) -> str:
    return f"{cfg['image']}:{container_hash()}"


# ---- the run command -----------------------------------------------------------------

def _host_path(path: Path) -> str:
    """`~/…` for a path under the home folder (as people know it), else the full path."""
    try:
        return "~/" + path.resolve().relative_to(Path.home().resolve()).as_posix()
    except ValueError:
        return str(path)


def host_ids() -> tuple[int, int]:
    """User and group id the container writes files as (Windows has none: 1000/1000)."""
    if hasattr(os, "getuid"):
        return os.getuid(), os.getgid()
    return 1000, 1000


def host_timezone() -> str | None:
    """IANA zone name: $TZ, else the /etc/localtime link (Linux, macOS), else None."""
    if os.environ.get("TZ"):
        return os.environ["TZ"]
    try:
        target = os.path.realpath("/etc/localtime")
    except OSError:
        return None
    if "zoneinfo/" in target:
        return target.split("zoneinfo/", 1)[1]
    return None


def claude_env_file() -> Path:
    return Path.home() / ".config" / "labpipe" / "claude.env"


def claude_login_present(engine: str) -> bool:
    if engine == "podman":
        result = subprocess.run([engine, "secret", "inspect", SECRET_NAME],
                                capture_output=True)
        return result.returncode == 0
    return claude_env_file().exists()


def raw_mounts(config: dict) -> list[tuple[str, Path]]:
    """(root id, host folder) for every raw root; the source folder if set, else the path."""
    mounts = []
    for root in config.get("raw_roots") or []:
        root_id = str(root["id"])
        if not SAFE_ID.fullmatch(root_id) or root_id in (".", ".."):
            raise ContainerError(f"raw root id {root_id!r} is not usable with the container: "
                                 "use only letters, digits, '_', '.' and '-'")
        mounts.append((root_id, Path(str(root.get("source") or root["path"])).expanduser()))
    return mounts


def allowed_hosts(config: dict) -> list[str]:
    hosts = (config.get("container") or {}).get("allowed_hosts") or {}
    harness = (config.get("agents") or {}).get("harness") or "claude-code"
    chosen = [*(hosts.get("common") or []), *(hosts.get(harness) or [])]
    return list(dict.fromkeys(chosen))


def bus_runtime_dir() -> str | None:
    """Host folder that holds the pop-up bus proxy's socket folder (Linux only)."""
    if not sys.platform.startswith("linux"):
        return None
    return os.environ.get("XDG_RUNTIME_DIR") or None


def page_url(port, token: str) -> str:
    try:
        from labpipe import inbox
        return inbox.url(port, token)
    except Exception:  # the page module is optional here
        return f"http://localhost:{port}/?token={token}"


def run_args(config: dict, engine: str, *, image_id: str = "", engine_text: str = "",
             claude_login: bool = False, tag: str | None = None,
             runtime_dir: str | None = None) -> list[str]:
    """The full `<engine> run` command line (pure: all machine facts come in as arguments)."""
    cfg = config.get("container") or DEFAULTS
    uid, gid = host_ids()
    mounts = raw_mounts(config)
    args = [engine, "run", "-d", "--name", cfg["name"], "--restart", "unless-stopped",
            # a minimal init as PID 1: passes the stop signal on (PID 1 itself ignores
            # SIGTERM) and reaps exited children such as the proxy
            "--init",
            "--cap-drop=ALL", *(f"--cap-add={cap}" for cap in CAPABILITIES),  # docker: one each
            "--security-opt", "no-new-privileges",
            # no :z / :Z on any mount: relabelling would change metadata of the raw files
            "--security-opt", "label=disable"]
    if engine == "podman":
        args += ["--userns=keep-id", "--user", "0:0"]
    env = {"LABPIPE_UID": uid, "LABPIPE_GID": gid,
           "LABPIPE_ROOT_PATHS": ",".join(f"{i}=/data/{i}" for i, _ in mounts),
           "LABPIPE_ALLOWED_HOSTS": ",".join(allowed_hosts(config)),
           "LABPIPE_IMAGE_ID": image_id, "LABPIPE_ENGINE": engine_text,
           "LABPIPE_HOST_ROOT": _host_path(_root()),   # for messages that name the folder
           "LABPIPE_PORT": cfg["port"], "LABPIPE_PAGE_TOKEN": cfg.get("page_token") or ""}
    bus_dir = runtime_dir if runtime_dir is not None else bus_runtime_dir()
    popups = bool(cfg.get("popups")) and bool(bus_dir)
    if popups:   # the proxy's socket folder: only the notification service is reachable
        env["DBUS_SESSION_BUS_ADDRESS"] = f"unix:path={BUS_MOUNT}/bus"
    zone = host_timezone()
    if zone:
        env["TZ"] = zone
    for key, value in env.items():
        args += ["-e", f"{key}={value}"]
    args += ["-v", f"{_root()}:{REPO_MOUNT}"]
    if popups:
        args += ["-v", f"{bus_dir}/{BUS_DIR}:{BUS_MOUNT}"]
    for root_id, folder in mounts:
        args += ["-v", f"{folder}:/data/{root_id}:ro"]
    args += ["-v", f"{cfg['name']}-home:{HOME_MOUNT}",
             "-p", f"127.0.0.1:{cfg['port']}:{cfg['port']}"]
    if claude_login:
        if engine == "podman":
            args += ["--secret", f"{SECRET_NAME},type=env,target=CLAUDE_CODE_OAUTH_TOKEN"]
        else:
            args += ["--env-file", str(claude_env_file())]
    return args + [tag or image_tag(cfg)]


# ---- running commands inside the container -------------------------------------------

def _exec_prefix(engine: str, name: str, uid: int, gid: int, tty: bool,
                 workdir: str | None = None) -> list[str]:
    args = [engine, "exec", "-i"]
    if tty:
        args.append("-t")
    args += ["--user", f"{uid}:{gid}"]
    if workdir:
        args += ["-w", workdir]
    return [*args, name]


def _inside(path: Path, repo: Path) -> str | None:
    """`/labpipe/<relative>` for a path inside the repository, else None."""
    try:
        relative = path.resolve().relative_to(repo.resolve())
    except ValueError:
        return None
    return str(PurePosixPath(REPO_MOUNT, *relative.parts))


def _map_arg(arg: str, cwd: Path, repo: Path) -> str:
    """Rewrite an argument that is a path (also `--opt=PATH`) to its place in the container.
    Existing paths and absolute paths must be inside the repository."""
    prefix, value = "", arg
    if arg.startswith("-"):
        if not arg.startswith("--") or "=" not in arg:
            return arg
        prefix, value = arg.split("=", 1)
        prefix += "="
    if not value:
        return arg
    path = Path(value).expanduser()
    full = path if path.is_absolute() else cwd / path
    if not path.is_absolute() and not full.exists():
        return arg  # not a path (an entry id, a number, a word)
    mapped = _inside(full, repo)
    if mapped is None:
        raise ForwardError("paths outside the repository can't be used with the container: "
                           + value)
    return prefix + mapped


def forward_argv(argv: list[str], cwd: Path | str, repo_root: Path | str, engine: str,
                 name: str, uid: int, gid: int, tty: bool) -> list[str] | None:
    """The engine command that runs `labpipe argv` in the container; None = run locally."""
    words = [a for a in argv if not a.startswith("-")]
    command = words[0] if words else None
    if command is None or command in LOCAL_COMMANDS or "--help" in argv or "-h" in argv:
        return None
    if command == "automation" and words[1:2] and words[1] in ("watch", "setup"):
        raise ForwardError("container mode is on: the container runs the watcher and the "
                           "nightly pass. Stop the native services: systemctl --user disable "
                           "--now labpipe-watch.service labpipe-nightly.timer")
    cwd, repo = Path(cwd), Path(repo_root)
    workdir = _inside(cwd, repo) or REPO_MOUNT
    mapped = [_map_arg(arg, cwd, repo) for arg in argv]
    return [*_exec_prefix(engine, name, uid, gid, tty, workdir), "labpipe", *mapped]


def inspect(engine: str, name: str) -> dict | None:
    """The container's inspect record, or None if there is no container of that name."""
    result = subprocess.run([engine, "container", "inspect", name], capture_output=True,
                            text=True)
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except ValueError:
        return None
    return data[0] if data else None


def running(engine: str, name: str) -> bool:
    record = inspect(engine, name)
    return bool(record and (record.get("State") or {}).get("Running"))


def forward(argv: list[str]) -> int | None:
    """Run `labpipe argv` in the container when container mode is on; None = run locally."""
    if os.environ.get("LABPIPE_IN_CONTAINER") or not argv:
        return None
    block = local_container_block()
    if block is None:
        return None
    try:
        engine = detect_engine(block.get("engine"))
        name = container_config({"container": pipeline_container_block()})["name"]  # + local.yaml
        uid, gid = host_ids()
        tty = sys.stdin.isatty() and sys.stdout.isatty()
        command = forward_argv(argv, Path.cwd(), _root(), engine, name, uid, gid, tty)
        if command is None:
            return None
        if not running(engine, name):
            _say({"error": "container not running: labpipe container start"})
            return 2
    except (ContainerError, SetupError) as exc:
        _say({"error": str(exc)})
        return 2
    return subprocess.run(command).returncode


def pipeline_container_block() -> dict:
    try:
        text = (_root() / "config" / "pipeline.yaml").read_text(encoding="utf-8")
        return (yaml.safe_load(text) or {}).get("container") or {}
    except (OSError, yaml.YAMLError):
        return {}


# ---- commands ------------------------------------------------------------------------

def _prepare() -> tuple[dict, dict, str]:
    config = load_config()
    return config, config["container"], current_engine()


def image_exists(engine: str, tag: str) -> bool:
    return subprocess.run([engine, "image", "inspect", tag], capture_output=True
                          ).returncode == 0


def build(engine: str, cfg: dict) -> str:
    """Build the image (the engine's output goes to stderr); return its tag."""
    tag, digest = image_tag(cfg), container_hash()
    command = [engine, "build", "-f", "container/Dockerfile", "-t", tag,
               "--label", f"{HASH_LABEL}={digest}", str(_root())]
    if subprocess.run(command, cwd=_root(), stdout=sys.stderr).returncode != 0:
        raise ContainerError(f"build failed: {' '.join(command)}")
    return tag


def cmd_build(args) -> int:
    _, cfg, engine = _prepare()
    _say({"built": build(engine, cfg), "engine": engine})
    return 0


def _image_id(engine: str, tag: str) -> str:
    result = subprocess.run([engine, "image", "inspect", "--format", "{{.Id}}", tag],
                            capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else ""


def ensure_page_token() -> bool:
    """Make sure local.yaml's `container:` block has a page_token. True if one was added."""
    block = dict(local_container_block() or {})
    if block.get("page_token"):
        return False
    block["page_token"] = secrets.token_urlsafe(16)
    safety.set_local_block("container", block)
    return True


def cmd_start(args) -> int:
    ensure_page_token()
    config, cfg, engine = _prepare()
    name, tag, current = cfg["name"], image_tag(cfg), container_hash()
    existing = inspect(engine, name)
    if existing and (existing.get("State") or {}).get("Running"):
        labels = (existing.get("Config") or {}).get("Labels") or {}
        old = labels.get(HASH_LABEL, "unknown")
        if not args.replace:
            _say({"error": "container already running"
                           + ("" if old == current else
                              f" with an older image ({old}, current {current})"),
                  "next": "labpipe container start --replace"})
            return 1
        subprocess.run([engine, "stop", name], capture_output=True)
    if existing:
        subprocess.run([engine, "rm", "-f", name], capture_output=True)
    if not image_exists(engine, tag):
        build(engine, cfg)
    command = run_args(config, engine, image_id=_image_id(engine, tag),
                       engine_text=engine_version(engine),
                       claude_login=claude_login_present(engine), tag=tag)
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0:
        _say({"error": "start failed", "detail": result.stderr.strip()})
        return 1
    _say({"started": name, "image": tag, "port": cfg["port"],
          "page": page_url(cfg["port"], cfg.get("page_token", "")),
          "popups": bool(cfg.get("popups")) and bool(bus_runtime_dir())})
    return 0


def cmd_stop(args) -> int:
    _, cfg, engine = _prepare()
    result = subprocess.run([engine, "stop", cfg["name"]], capture_output=True, text=True)
    _say({"stopped": cfg["name"]} if result.returncode == 0
         else {"error": result.stderr.strip() or "not running"})
    return result.returncode


def cmd_status(args) -> int:
    _, cfg, engine = _prepare()
    record = inspect(engine, cfg["name"])
    state = (record or {}).get("State") or {}
    labels = ((record or {}).get("Config") or {}).get("Labels") or {}
    have, current = labels.get(HASH_LABEL), container_hash()
    _say({"engine": engine, "name": cfg["name"], "exists": record is not None,
          "running": bool(state.get("Running")), "started_at": state.get("StartedAt"),
          "image_hash": have, "current_hash": current,
          "image_up_to_date": have == current if record else None,
          "page": page_url(cfg["port"], cfg.get("page_token", "")),
          "next": None if (record and have == current) else
          "labpipe container start --replace" if record else "labpipe container start"})
    return 0


def cmd_logs(args) -> int:
    _, cfg, engine = _prepare()
    return subprocess.run([engine, "logs", "--tail", str(args.n), cfg["name"]]).returncode


def read_token() -> str:
    """The token from the terminal (not echoed) or from a pipe."""
    if sys.stdin.isatty():
        token = getpass.getpass("Paste the token from `claude setup-token` (not shown): ")
    else:
        token = sys.stdin.read()
    token = token.strip()
    if not token:
        raise ContainerError("no token given")
    return token


def store_claude_token(engine: str, token: str) -> str:
    if engine == "podman":
        result = subprocess.run([engine, "secret", "create", "--replace", SECRET_NAME, "-"],
                                input=token, text=True, capture_output=True)
        if result.returncode != 0:
            raise ContainerError(f"podman secret create failed: {result.stderr.strip()}")
        return f"podman secret {SECRET_NAME}"
    path = claude_env_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as stream:
        stream.write(f"CLAUDE_CODE_OAUTH_TOKEN={token}\n")
    os.chmod(path, 0o600)
    return str(path)


TRUST_SCRIPT = ('f=/home/labpipe/.codex/config.toml; mkdir -p "$(dirname "$f")"; '
                'grep -qsF \'[projects."/labpipe"]\' "$f" || '
                'printf \'\\n[projects."/labpipe"]\\ntrust_level = "trusted"\\n\' >> "$f"')


def cmd_login(args) -> int:
    config, cfg, engine = _prepare()
    if args.what == "claude":
        print("Run `claude setup-token` on this computer first, then paste the token.",
              file=sys.stderr)
        where = store_claude_token(engine, read_token())
        _say({"stored": where, "next": "labpipe container start --replace"})
        return 0
    uid, gid = host_ids()
    if not running(engine, cfg["name"]):
        _say({"error": "container not running: labpipe container start"})
        return 2
    code = subprocess.run([*_exec_prefix(engine, cfg["name"], uid, gid, True), "codex", "login", "--device-auth"]).returncode
    trust = subprocess.run([*_exec_prefix(engine, cfg["name"], uid, gid, False),
                            "sh", "-c", TRUST_SCRIPT], capture_output=True, text=True)
    _say({"codex_login_exit_code": code, "trusted_project": trust.returncode == 0})
    return code


def cmd_shell(args) -> int:
    try:
        config, cfg, engine = _prepare()
        harness = (config.get("agents") or {}).get("harness") or "claude-code"
        program = args.agent or HARNESS_PROGRAM.get(harness)
        if program not in ("claude", "codex"):
            raise ContainerError(f"no agent tool for harness {harness!r}: "
                                 "use `labpipe shell claude|codex`")
    except (ContainerError, SetupError) as exc:
        _say({"error": str(exc)})
        return 2
    if not running(engine, cfg["name"]):
        _say({"error": "container not running: labpipe container start"})
        return 2
    uid, gid = host_ids()
    command = [*_exec_prefix(engine, cfg["name"], uid, gid, True, REPO_MOUNT), program]
    if os.name != "nt":
        os.execvp(command[0], command)  # replaces this process
    return subprocess.run(command).returncode


def cmd_setup(args) -> int:
    """Interactive, in the user's own terminal: pick the engine, write local.yaml, build."""
    engine = detect_engine(None)
    answer = input(f"Use {engine} ({engine_version(engine)}) for the labpipe container? [Y/n] ")
    if answer.strip().lower() in ("n", "no"):
        _say({"cancelled": True, "hint": "set `container: {engine: podman|docker}` in "
                                        "config/local.yaml to choose another"})
        return 1
    safety.set_local_block("container", {"engine": engine})
    config = load_config()
    tag = build(engine, config["container"])
    _say({"engine": engine, "image": tag, "next": [
        "claude setup-token   (on this computer), then: labpipe container login claude",
        "labpipe container start",
        "labpipe container login codex   (only if you use Codex)",
        "labpipe shell   (agent session inside the container)"]})
    return 0


# ---- desktop pop-ups (Linux): xdg-dbus-proxy user service ----------------------------

def proxy_unit_text(proxy: str = "/usr/bin/xdg-dbus-proxy") -> str:
    """The systemd user unit: a filtered copy of the session bus (notifications only)."""
    return (
        "[Unit]\n"
        "Description=labpipe: desktop bus proxy (notifications only) for the container\n\n"
        "[Service]\n"
        f"ExecStartPre=/usr/bin/mkdir -p %t/{BUS_DIR}\n"
        f"ExecStart={proxy} unix:path=%t/bus %t/{BUS_DIR}/bus --filter "
        "--talk=org.freedesktop.Notifications\n"
        "Restart=on-failure\n\n"
        "[Install]\n"
        "WantedBy=default.target\n")


def unit_path() -> Path:
    return Path.home() / ".config" / "systemd" / "user" / UNIT_NAME


def _systemctl(*words: str) -> subprocess.CompletedProcess:
    return subprocess.run(["systemctl", "--user", *words], capture_output=True, text=True)


def _set_popups(value: bool) -> None:
    block = dict(local_container_block() or {})
    block["popups"] = value
    safety.set_local_block("container", block)


def cmd_popups(args) -> int:
    if not sys.platform.startswith("linux"):
        _say({"error": "pop-ups are Linux only; use the notifications page"})
        return 2
    runtime = bus_runtime_dir()
    socket = Path(runtime) / BUS_DIR / "bus" if runtime else None
    path = unit_path()
    if args.state == "status":
        active = _systemctl("is-active", UNIT_NAME).stdout.strip()
        enabled = _systemctl("is-enabled", UNIT_NAME).stdout.strip()
        _say({"unit_file": str(path), "unit_installed": path.exists(), "active": active,
              "enabled": enabled, "socket": str(socket), "socket_exists": bool(
                  socket and socket.exists()),
              "popups_in_local_yaml": bool((local_container_block() or {}).get("popups"))})
        return 0
    if args.state == "on":
        proxy = shutil.which("xdg-dbus-proxy")
        if not proxy:
            _say({"error": "xdg-dbus-proxy is not installed (Fedora: it comes with flatpak, "
                           "`sudo dnf install xdg-dbus-proxy`; Debian/Ubuntu: `sudo apt install "
                           "xdg-dbus-proxy`)"})
            return 2
        if not runtime:
            _say({"error": "XDG_RUNTIME_DIR is not set (not a desktop login session?)"})
            return 2
        text = proxy_unit_text(proxy)
        print(f"Will write {path}:\n\n{text}\nand run:\n"
              "  systemctl --user daemon-reload\n"
              f"  systemctl --user enable --now {UNIT_NAME}\n"
              "then set `popups: true` in config/local.yaml (container block).\n",
              file=sys.stderr)
        if input("Install the pop-up proxy service? [y/N] ").strip().lower() not in ("y", "yes"):
            _say({"cancelled": True})
            return 1
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        for words in (("daemon-reload",), ("enable", "--now", UNIT_NAME)):
            result = _systemctl(*words)
            if result.returncode != 0:
                _say({"error": f"systemctl --user {' '.join(words)} failed",
                      "detail": result.stderr.strip()})
                return 1
        _set_popups(True)
        _say({"popups": True, "unit": str(path), "next": "labpipe container start --replace"})
        return 0
    print(f"Will run: systemctl --user disable --now {UNIT_NAME}, remove {path}, "
          "systemctl --user daemon-reload, and set `popups: false` in config/local.yaml.",
          file=sys.stderr)
    if input("Remove the pop-up proxy service? [y/N] ").strip().lower() not in ("y", "yes"):
        _say({"cancelled": True})
        return 1
    _systemctl("disable", "--now", UNIT_NAME)
    path.unlink(missing_ok=True)
    _systemctl("daemon-reload")
    _set_popups(False)
    _say({"popups": False, "next": "labpipe container start --replace"})
    return 0


def cmd_container(args) -> int:
    handlers = {"build": cmd_build, "start": cmd_start, "stop": cmd_stop, "status": cmd_status,
                "logs": cmd_logs, "login": cmd_login, "setup": cmd_setup,
                "popups": cmd_popups}
    try:
        return handlers[args.action](args)
    except (ContainerError, SetupError) as exc:
        _say({"error": str(exc)})
        return 2


def register(sub) -> None:
    """Add `container …` and `shell` to the CLI parser."""
    group = sub.add_parser("container", help="build, start and use the labpipe container"
                           ).add_subparsers(dest="action", required=True)
    group.add_parser("setup", help="interactive: choose the engine, build the image")
    group.add_parser("build", help="build the image")
    start = group.add_parser("start", help="start the container (builds the image if needed)")
    start.add_argument("--replace", action="store_true",
                       help="replace a running container (new image or new login)")
    group.add_parser("stop")
    group.add_parser("status")
    logs = group.add_parser("logs")
    logs.add_argument("-n", type=int, default=100, help="last N lines (default 100)")
    login = group.add_parser("login", help="store the AI login for the container")
    login.add_argument("what", choices=["claude", "codex"])
    popups = group.add_parser("popups", help="Linux desktop pop-ups through a filtered bus proxy")
    popups.add_argument("state", choices=["on", "off", "status"])
    for leaf in group.choices.values():
        leaf.set_defaults(func=lambda args, config: cmd_container(args), needs_config=False)
    shell = sub.add_parser("shell", help="agent session inside the container")
    shell.add_argument("agent", nargs="?", choices=["claude", "codex"],
                       help="default: the configured agents.harness")
    shell.set_defaults(func=lambda args, config: cmd_shell(args), needs_config=False)
