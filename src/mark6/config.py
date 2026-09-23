"""Where the app keeps its two pieces of state: who it is signed in as, and
which local MCP servers it is allowed to lend out.

Both live in the user's own config directory, never beside the source — a
device token in a checkout is a device token in a backup, a screen share and
eventually a paste. Stdlib only: this whole app has no third-party
dependencies, which is also why the bundled interpreter (run.bat, on Windows)
never has to bootstrap pip.
"""
import importlib.util
import json
import os
import platform
import re
import tempfile

NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")

# Stands for "the interpreter this app is itself running on", resolved at
# spawn time by mcp.client.resolve_command. A bundled server is stored with
# this rather than an absolute path so the install folder can be moved.
BUNDLED_PYTHON = "{python}"

# MCP servers that ship inside the app (bin\bootstrap.ps1 installs them into
# the private runtime). Each is added to the list once, and switched OFF like
# anything else: shipping a server is not the same as choosing to lend it.
# Removing one sticks — `seeded` remembers it was offered.
# Every value realhands' `computer` tool accepts, read off its own dispatch
# (realhands/server.py). The first eighteen are the dispatch proper; `stop`,
# `done` and `release` are handled before it and stand the safety overlay
# down, which its description tells the model to call when a task is finished
# -- so leaving them out of an enum would forbid the one action it documents
# by name.
COMPUTER_ACTIONS = [
    "screenshot", "mouse_move", "left_click", "right_click", "middle_click",
    "double_click", "triple_click", "left_click_drag", "left_mouse_down",
    "left_mouse_up", "scroll", "type", "key", "hold_key", "wait",
    "cursor_position", "monitors", "activate_window",
    "stop", "done", "release",
]

BUILTIN_SERVERS = [
    {
        "module": "realhands",
        "entry": {
            "name": "computer",
            "command": BUNDLED_PYTHON,
            # -s: never let another Python 3.12's user site-packages
            # shadow the bundled mcp/pyautogui (see run.bat).
            "args": ["-s", "-m", "realhands.server"],
            "env": {},
            "enabled": False,
            "description": "Computer use: sees your screen, moves your real "
                           "mouse, types on your real keyboard.",
            # Everything below corrects how realhands describes itself. It
            # types `action` as a bare string, names none of its values, and
            # leaves `steps` -- the batch path its own description tells the
            # model to prefer -- as a bare object with no schema at all. So
            # the model guesses, and has guessed `move`, `press_key`, `press`
            # and `keypress`. Each guess is a wasted round trip that comes
            # back as a raw ValueError.
            #
            # Applied by mcp/pool.py, and only where it still fits: a
            # parameter that upstream renames or drops is left alone rather
            # than re-invented.
            "tool_schema": {
                "computer": {
                    # PREPENDED, not appended. The relay truncates a
                    # description to 1024 characters with a bare slice
                    # (FreeBusiness/relay/server.py), and realhands' own text
                    # already overruns that on its own -- so anything added at
                    # the end is sliced off before a model ever sees it. This
                    # is also why it stays short: every character here costs a
                    # character of realhands' own explanation of batching and
                    # screenshot coordinates.
                    #
                    # It carries only what the enum cannot: the names that do
                    # NOT exist but keep getting tried, and the two parameter
                    # rules whose absence produces a ValueError.
                    "note": "Key presses are action `key` with the key name "
                            "in `text` (e.g. \"Return\") -- there is no "
                            "`press`, `keypress` or `move`. `scroll` requires "
                            "`coordinate`. `key` ignores `duration`; only "
                            "`hold_key` holds one.",
                    "properties": {"action": {"enum": COMPUTER_ACTIONS}},
                    # The batch path. realhands' description says "BATCH
                    # WHENEVER YOU CAN", so most actions arrive as steps --
                    # and `steps` is typed `list[dict]` with no inner schema,
                    # which is exactly where `press` was guessed. An enum on
                    # the outer `action` does nothing for a step.
                    "item_properties": {"steps": {"action": {"enum": COMPUTER_ACTIONS}}},
                },
            },
        },
    },
]

DEFAULTS = {
    # Where the hosted side lives. Overridable so a checkout can be pointed at
    # a staging box without editing source.
    "host": os.environ.get("MARK6_HOST", "https://freeclaw.eedeb.dev"),
    "token": None,
    "account": None,
    "device_name": None,
    "servers": [],
    "seeded": [],
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
    cfg["seeded"] = raw.get("seeded") or []
    _seed_builtins(cfg)
    return cfg


# What a bundled server's saved entry is allowed to keep across an update.
# Everything else about it — the command, the arguments, the environment, the
# description — is defined above and re-applied on every load, so changing how
# a shipped server is launched takes effect for people who already have it
# rather than only for new installs. The same reasoning as FreeClaw's
# BUILTIN_SERVERS: an entry saved by an older version must not be able to pin
# a stale command line. Only the choice to switch it on is the person's.
_BUILTIN_OWN_KEYS = ("enabled",)


def _seed_builtins(cfg):
    """Offer each bundled server once, and keep its command line current.

    Not saved here: the next save (any toggle) records it, and until then this
    simply runs again with the same result."""
    for builtin in BUILTIN_SERVERS:
        entry = builtin["entry"]
        name = entry["name"]
        if importlib.util.find_spec(builtin["module"]) is None:
            continue
        existing = next((s for s in cfg["servers"] if s.get("name") == name), None)
        if existing is not None:
            # Re-apply everything but their on/off choice.
            keep = {k: existing[k] for k in _BUILTIN_OWN_KEYS if k in existing}
            existing.update(json.loads(json.dumps(entry)))
            existing.update(keep)
            continue
        if name in cfg["seeded"]:
            continue            # offered before and removed; that decision sticks
        cfg["seeded"].append(name)
        cfg["servers"].append(json.loads(json.dumps(entry)))


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
