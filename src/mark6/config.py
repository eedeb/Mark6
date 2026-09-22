"""Where the app keeps its two pieces of state: who it is signed in as, and
which local MCP servers it is allowed to lend out.

Both live in the user's own config directory, never beside the source — a
device token in a checkout is a device token in a backup, a screen share and
eventually a paste. Stdlib only: this whole app has no third-party
dependencies, which is also why the bundled interpreter (run.bat, on Windows)
never has to bootstrap pip.
"""
import json
import os
import platform
import tempfile

DEFAULTS = {
    # Where the hosted side lives. Overridable so a checkout can be pointed at
    # a staging box without editing source.
    "host": os.environ.get("MARK6_HOST", "https://freeclaw.eedeb.dev"),
    "token": None,
    "account": None,
    "device_name": None,
    "servers": [],
}


def config_dir():
    override = os.environ.get("MARK6_CONFIG_DIR")
    if override:
        return override
    if platform.system() == "Windows":
        base = os.environ.get("APPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Roaming")
        return os.path.join(base, "Mark6")
    # XDG on Linux, and close enough on macOS for a terminal build. When this
    # grows a real bundle it becomes ~/Library/Application Support/Mark 6.
    base = os.environ.get("XDG_CONFIG_HOME") or os.path.join(
        os.path.expanduser("~"), ".config")
    return os.path.join(base, "mark6")


def config_path():
    return os.path.join(config_dir(), "config.json")


def load():
    try:
        with open(config_path(), "r", encoding="utf-8") as f:
            raw = json.load(f)
    except (OSError, ValueError):
        raw = {}
    if not isinstance(raw, dict):
        raw = {}
    cfg = dict(DEFAULTS)
    cfg.update(raw)
    cfg["servers"] = raw.get("servers") or []
    return cfg


def save(cfg):
    directory = config_dir()
    os.makedirs(directory, exist_ok=True)
    target = config_path()
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".config-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(cfg, f, indent=2)
        try:
            # 0600 before it is in place, not after. Windows ignores the mode
            # and relies on the ACL of the user's own AppData, which is the
            # equivalent protection.
            os.chmod(tmp, 0o600)
        except OSError:
            pass
        os.replace(tmp, target)
    except BaseException:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise
    return target


def signed_in(cfg):
    return bool(cfg.get("token") and cfg.get("account"))
