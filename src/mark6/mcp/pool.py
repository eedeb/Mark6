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
import copy
import re

from .client import StdioServer

_UNSAFE = re.compile(r"[^0-9A-Za-z_-]")


def _safe_name(part):
    """Tool names have to survive OpenAI's ^[A-Za-z0-9_-]{1,64}$ after
    FreeClaw has added its own `mcp_desktop_` in front, so they are scrubbed
    here."""
    return _UNSAFE.sub("_", str(part or "")).strip("_")


def _typed_branch(node, want):
    """The part of `node` that describes a `want` ("string"/"array") value.

    An optional parameter arrives from FastMCP as
    `{"anyOf": [{"type": "string"}, {"type": "null"}]}` rather than a plain
    type, and an `enum` dropped beside that `anyOf` is not where a reader
    looks -- it has to go on the branch it constrains. Returns None when the
    shape is not one we recognise, so the caller can leave it alone."""
    if not isinstance(node, dict):
        return None
    if node.get("type") == want:
        return node
    for branch in node.get("anyOf") or ():
        if isinstance(branch, dict) and branch.get("type") == want:
            return branch
    return None


def _overlay(tool, overlay):
    """(description, inputSchema) for one tool, with a bundled server's
    corrections folded in.

    Some servers describe themselves badly enough that the model cannot call
    them without guessing. The bundled computer-use server names none of the
    values its `action` accepts, and leaves the batch parameter its own
    description tells the model to prefer as an untyped list of dicts -- so an
    agent tries `press`, or `keypress`, and gets a raw ValueError back. A
    correction belongs here because the schema is the only thing on this path
    the model ever reads.

    Only for servers this app ships and has read the source of; a server
    somebody adds themselves is published exactly as it describes itself.

    Every step is conditional on the tool still looking the way the overlay
    expects. A parameter that has been renamed or dropped upstream is left
    alone rather than re-added, so a stale overlay degrades to today's
    behaviour instead of advertising something that no longer exists."""
    description = tool.get("description") or ""
    schema = tool.get("inputSchema") or {"type": "object"}
    if not overlay:
        return description, schema

    properties = schema.get("properties")
    if not isinstance(properties, dict):
        return description, schema

    out = dict(properties)
    changed = False

    # Scalar parameters: the enum goes on the branch that types the value.
    for param, extra in (overlay.get("properties") or {}).items():
        node = properties.get(param)
        if not isinstance(node, dict):
            continue                    # renamed or gone upstream
        node = copy.deepcopy(node)
        (_typed_branch(node, "string") or node).update(copy.deepcopy(extra))
        out[param] = node
        changed = True

    # Array parameters: the same correction, one level down, on whatever each
    # element is. Without this a batched step is unconstrained even though the
    # single-action form next to it is not.
    for param, item_props in (overlay.get("item_properties") or {}).items():
        node = properties.get(param)
        if not isinstance(node, dict):
            continue
        node = copy.deepcopy(node)
        array = _typed_branch(node, "array")
        items = (array or {}).get("items")
        if not isinstance(items, dict):
            continue                    # no element schema to hang this on
        items["properties"] = dict(items.get("properties") or {},
                                   **copy.deepcopy(item_props))
        out[param] = node
        changed = True

    if not changed:
        return description, schema      # nothing matched; leave it untouched

    patched = dict(schema)
    patched["properties"] = out

    # Prepended, never appended: the relay truncates this to 1024 characters
    # with a bare slice, and a server whose own description already fills that
    # budget would swallow a correction added at the end.
    note = overlay.get("note")
    if note:
        description = f"{note}\n\n{description.lstrip()}".strip()
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
