"""The set of local servers this computer is lending out, and the namespacing
that keeps two of them from claiming the same tool name.

## The allowlist

A server added here is **off** until somebody enables it. That is the whole
safety model of this app in one sentence, and it is deliberately not a
setting with a sensible default: the agent on the other end reads the open
web, and a page it reads can try to talk it into calling things. What stops
that being a problem on this machine is that the list of things it can call
is short, chosen, and was chosen here rather than there.

## Names

The relay publishes one flat tool list, so two servers that both export
`read_file` would collide. Every tool is exposed as `<server>_<tool>` and
mapped back on the way in. FreeClaw prefixes it again on its side, so the
model ends up calling `mcp_desktop_files_read_file` — long, but it says
exactly where the call is going, which is worth the characters.
"""
import re

from .client import StdioServer

_UNSAFE = re.compile(r"[^0-9A-Za-z_-]")


def _safe_name(part):
    """Tool names have to survive OpenAI's ^[A-Za-z0-9_-]{1,64}$ after
    FreeClaw has added its own `mcp_desktop_` in front, so they are scrubbed
    here."""
    return _UNSAFE.sub("_", str(part or "")).strip("_")


def _overlay(tool, overlay):
    """(description, inputSchema) for one tool, with a bundled server's
    corrections folded in.

    Some servers describe themselves badly enough that the model cannot call
    them without guessing -- the bundled computer-use server types its
    `action` as a bare string and names none of the eighteen values it
    accepts, so an agent tries `move`, or `press_key`, and gets a raw
    ValueError back. A correction belongs in the schema, because the schema is
    the only thing on this path the model ever reads.

    Only for servers this app ships and has read the source of; a server
    somebody adds themselves is published exactly as it describes itself.

    Every step is conditional on the tool still looking the way the overlay
    expects. A parameter that has been renamed or dropped upstream is left
    alone rather than re-added, so a stale overlay degrades to today's
    behaviour instead of advertising a parameter that no longer exists."""
    description = tool.get("description") or ""
    schema = tool.get("inputSchema") or {"type": "object"}
    if not overlay:
        return description, schema

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return description, schema

    patched = None
    for param, extra in (overlay.get("properties") or {}).items():
        if not isinstance(properties.get(param), dict):
            continue            # renamed or gone upstream; say nothing
        if patched is None:
            patched = dict(schema)
            patched["properties"] = dict(properties)
        patched["properties"][param] = dict(patched["properties"][param], **extra)

    if patched is None:
        return description, schema      # nothing matched; leave it untouched

    note = overlay.get("note")
    if note:
        description = f"{description.rstrip()}\n\n{note}".strip()
    return description, patched


class Pool:
    def __init__(self, entries=None):
        self.servers = {}
        self.routes = {}            # exposed name -> (server, real tool name)
        self.overlays = {}          # server name -> {tool name -> overlay}
        for entry in entries or []:
            if not entry.get("enabled"):
                continue
            self.servers[entry["name"]] = StdioServer(
                entry["name"], entry["command"], entry.get("args"),
                entry.get("env"), entry.get("cwd"))
            if entry.get("tool_schema"):
                self.overlays[entry["name"]] = entry["tool_schema"]

    @property
    def names(self):
        return list(self.servers)

    def start(self):
        """Start every enabled server and build the published tool list.

        One server failing to start does not stop the others: a person with
        four servers and a typo in one should get the other three, and an
        error they can read about the fourth."""
        tools = []
        failures = []
        self.routes.clear()

        for name, server in self.servers.items():
            try:
                server.start()
            except Exception as e:                       # noqa: BLE001
                failures.append({"name": name, "error": str(e)})
                continue
            for tool in server.tools:
                exposed = f"{_safe_name(name)}_{_safe_name(tool['name'])}"[:48]
                if exposed in self.routes:
                    failures.append({"name": name,
                                     "error": f"two tools both map to '{exposed}'; rename one"})
                    continue
                self.routes[exposed] = (server, tool["name"])
                described, schema = _overlay(
                    tool, (self.overlays.get(name) or {}).get(tool["name"]))
                tools.append({
                    "name": exposed,
                    # Says where it runs, in the description the model
                    # actually reads. Without this the model has no way to
                    # tell a tool on somebody's laptop from one on the open
                    # internet.
                    "description": (f"[runs on your computer, via {name}] "
                                    f"{described}").strip(),
                    "inputSchema": schema,
                })
        return tools, failures

    def startup_notes(self, limit=6):
        """(server, line) for whatever each running server printed to stderr
        while starting.

        Bounded, because a chatty server would otherwise fill the window's log
        with its own noise before anything useful happened. The interesting
        lines are always the first few — a server announcing what it is, or
        explaining which optional half of itself it could not load."""
        notes = []
        for name, server in self.servers.items():
            if not server.running:
                continue          # its stderr is in the failure it already reported
            for line in server.stderr_lines[:limit]:
                if line.strip():
                    notes.append((name, line.strip()))
        return notes

    def call(self, exposed_name, arguments, timeout=None):
        """Run one published tool. Raises with a readable message."""
        route = self.routes.get(exposed_name)
        if route is None:
            raise KeyError(
                f"No tool called '{exposed_name}' is switched on for this computer.")
        server, real_name = route
        kwargs = {"timeout": timeout} if timeout is not None else {}
        return server.call_tool(real_name, arguments, **kwargs)

    def stop(self):
        for server in self.servers.values():
            server.stop()
