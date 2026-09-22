"""Mark 6 — the terminal build.

No window yet. This is the daemon half of the desktop app, which is the half
that does anything: it signs this computer in, runs the MCP servers you
choose, and lends their tools to your hosted agent for as long as it is
running. A UI goes on top of exactly this later.

    run.bat login                          pair this computer with your account
    run.bat add files -- npx -y @modelcontextprotocol/server-filesystem ~/notes
    run.bat enable files                   switch it on (everything starts off)
    run.bat run                            connect, and stay connected
"""
import re
import signal
import sys

from . import auth, config
from .relay import Relay, RelayError
from .mcp.client import StdioServer

_NAME_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")


def _die(message):
    print(message, file=sys.stderr)
    sys.exit(1)


def _require_account(cfg):
    if not config.signed_in(cfg):
        _die("This computer is not paired yet. Run `run.bat login` first.")


# ── commands ─────────────────────────────────────────────────

def cmd_login(argv):
    def on_code(user_code, uri):
        print()
        print("  Open this page while signed in to your Mark 6 account:")
        print(f"      {uri}")
        print()
        print("  and enter this code:")
        print(f"      {user_code}")
        print()
        print("  Waiting for you to approve it...")

    try:
        cfg = auth.login(on_code=on_code)
    except auth.AuthError as e:
        _die(str(e))
        return
    print(f"\nPaired as \"{cfg['device_name']}\" on account {cfg['account']}.")
    print(f"Config: {config.config_path()}")
    print("\nNext: add a server with `run.bat add`, then `run.bat run`.")


def cmd_logout(argv):
    auth.logout()
    print("Signed out on this computer.")
    print("This does not unpair it — do that from your dashboard, which is "
         "what actually stops it being used.")


def cmd_add(argv):
    """`run.bat add <name> -- <command> [args...]`

    Everything after `--` is the command line, taken as an argv array and
    never as a string: it is spawned without a shell, so a path with a space
    in it is one argument and nothing is ever re-parsed."""
    if "--" not in argv or argv.index("--") == len(argv) - 1:
        _die("Usage: run.bat add <name> -- <command> [args...]\n"
            "Example: run.bat add files -- npx -y "
            "@modelcontextprotocol/server-filesystem C:\\Users\\me\\notes")
        return
    sep = argv.index("--")
    name = argv[0] if argv and argv[0] != "--" else None
    if not name:
        _die("Give the server a short name: run.bat add <name> -- <command>")
        return
    if not _NAME_RE.match(name):
        _die("A server name can be letters, numbers, dash and underscore, "
            "up to 32 characters.")
        return

    rest = argv[sep + 1:]
    if not rest:
        _die("Give a command to run after `--`.")
        return
    cmd, args = rest[0], rest[1:]

    cfg = config.load()
    if any(s["name"] == name for s in cfg["servers"]):
        _die(f"There is already a server called '{name}'.")
        return
    cfg["servers"].append({"name": name, "command": cmd, "args": args,
                           "env": {}, "enabled": False})
    config.save(cfg)
    print(f"Added '{name}': {cmd} {' '.join(args)}")
    print(f"It is OFF. Switch it on with:  run.bat enable {name}")


def cmd_remove(argv):
    if not argv:
        _die("Usage: run.bat remove <name>")
        return
    name = argv[0]
    cfg = config.load()
    before = len(cfg["servers"])
    cfg["servers"] = [s for s in cfg["servers"] if s["name"] != name]
    if len(cfg["servers"]) == before:
        _die(f"No server called '{name}'.")
        return
    config.save(cfg)
    print(f"Removed '{name}'.")


def _cmd_toggle(argv, enabled):
    verb = "enable" if enabled else "disable"
    if not argv:
        _die(f"Usage: run.bat {verb} <name>")
        return
    name = argv[0]
    cfg = config.load()
    server = next((s for s in cfg["servers"] if s["name"] == name), None)
    if server is None:
        _die(f"No server called '{name}'.")
        return
    server["enabled"] = enabled
    config.save(cfg)
    print(f"'{name}' is now {'ON' if enabled else 'OFF'}.")
    if enabled:
        print("Restart `run.bat run` for your agent to see it.")


def cmd_enable(argv):
    _cmd_toggle(argv, True)


def cmd_disable(argv):
    _cmd_toggle(argv, False)


def cmd_list(argv):
    cfg = config.load()
    if config.signed_in(cfg):
        print(f"Paired as \"{cfg['device_name']}\" on account {cfg['account']}.")
    else:
        print("Not paired. Run `run.bat login`.")
    print(f"Config: {config.config_path()}\n")

    if not cfg["servers"]:
        print("No servers yet. Add one:")
        print("  run.bat add files -- npx -y "
             "@modelcontextprotocol/server-filesystem ~/notes")
        return
    for s in cfg["servers"]:
        state = "ON " if s["enabled"] else "off"
        print(f"  [{state}] {s['name']:<16} {s['command']} {' '.join(s.get('args') or [])}")
    off = sum(1 for s in cfg["servers"] if not s["enabled"])
    if off:
        print(f"\n{off} switched off. Your agent cannot see those at all.")


def cmd_test(argv):
    """`run.bat test <name>` — start one server and list its tools, without
    involving the network at all. The first thing to reach for when a server
    is not showing up: it separates "my command line is wrong" from "the
    pairing is wrong", which are otherwise the same symptom."""
    if not argv:
        _die("Usage: run.bat test <name>")
        return
    name = argv[0]
    cfg = config.load()
    entry = next((s for s in cfg["servers"] if s["name"] == name), None)
    if entry is None:
        _die(f"No server called '{name}'.")
        return

    print(f"Starting '{name}': {entry['command']} {' '.join(entry.get('args') or [])}")
    server = StdioServer(entry["name"], entry["command"], entry.get("args"),
                         entry.get("env"))
    try:
        server.start()
        tools = server.tools
        print(f"\nIt works. {len(tools)} tool{'' if len(tools) == 1 else 's'}:")
        for t in tools:
            desc = (t.get("description") or "").split("\n")[0][:70]
            print(f"  {t['name']:<28} {desc}")
        if not entry["enabled"]:
            print(f"\nStill switched off. Turn it on with: run.bat enable {name}")
    except Exception as e:                                # noqa: BLE001
        print(f"\nIt did not start: {e}", file=sys.stderr)
        if server.stderr_lines:
            print("\nWhat it printed:", file=sys.stderr)
            for line in server.stderr_lines[-15:]:
                print(f"  {line}", file=sys.stderr)
        sys.exit(1)
    finally:
        server.stop()


def cmd_run(argv):
    cfg = config.load()
    _require_account(cfg)
    relay = Relay()

    def shutdown(*_a):
        print("\nDisconnecting. Your agent loses these tools until you run "
             "this again.")
        relay.stop()
        sys.exit(0)

    signal.signal(signal.SIGINT, shutdown)
    try:
        signal.signal(signal.SIGTERM, shutdown)
    except (ValueError, AttributeError):
        pass                              # not available on this platform

    try:
        relay.start()
    except RelayError as e:
        relay.stop()
        _die(f"\n{e}")


def _usage():
    print("""Mark 6 — lends this computer's MCP servers to your hosted agent.

  run.bat login                  pair this computer with your Mark 6 account
  run.bat logout                 forget the pairing on this computer
  run.bat list                   what is configured, and what is switched on
  run.bat add <name> -- <cmd>    add a local MCP server (starts switched off)
  run.bat enable <name>          let your agent use it
  run.bat disable <name>         stop letting your agent use it
  run.bat remove <name>          forget it entirely
  run.bat test <name>            start it locally and list its tools
  run.bat run                    connect, and stay connected

Every server starts switched off. Your agent can only ever call the ones you
have turned on, on this computer, while this is running.""")


COMMANDS = {
    "login": cmd_login, "logout": cmd_logout, "list": cmd_list,
    "add": cmd_add, "remove": cmd_remove, "enable": cmd_enable,
    "disable": cmd_disable, "test": cmd_test, "run": cmd_run,
}


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if not argv or argv[0] not in COMMANDS:
        _usage()
        return 1 if argv else 0
    COMMANDS[argv[0]](argv[1:])
    return 0
