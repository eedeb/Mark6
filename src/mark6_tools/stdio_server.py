"""A minimal MCP server over stdio — the other end of mcp/client.py.

Hand-rolled rather than built on the `mcp` package on purpose: that package
is only in the runtime because realhands needs it, and its 1.x -> 2.x move
already broke the import path once (see realhands' own README). The stdio
transport is newline-delimited JSON-RPC, and the subset a tools-only server
needs is four methods. That is less code than a compatibility shim.

Tools register with @server.tool. Calls run on their own threads, so a slow
tool never holds up a ping or a second call; responses are written under a
lock so two of them cannot interleave on stdout.
"""
import inspect
import json
import sys
import threading
import traceback

PROTOCOL_VERSIONS = ("2025-06-18", "2025-03-26", "2024-11-05")


class ToolError(Exception):
    """Raised by a tool to report a failure the model should read, as a
    result with isError rather than a protocol error."""


class StdioServer:
    def __init__(self, name, version):
        self.name = name
        self.version = version
        self.tools = {}
        self._out = sys.stdout
        self._out_lock = threading.Lock()
        # stdout is the protocol channel. Anything a library prints — a
        # progress bar, a warning — would corrupt it, so from here on print()
        # goes to stderr, which the client keeps as a diagnostic log.
        sys.stdout = sys.stderr

    def tool(self, name, description, input_schema=None):
        def register(fn):
            self.tools[name] = {
                "fn": fn,
                "spec": {"name": name, "description": description,
                         "inputSchema": input_schema or {"type": "object",
                                                         "properties": {}}},
            }
            return fn
        return register

    # ── transport ────────────────────────────────────────────

    def _send(self, obj):
        with self._out_lock:
            self._out.write(json.dumps(obj) + "\n")
            self._out.flush()

    def _reply(self, msg_id, result=None, error=None):
        body = {"jsonrpc": "2.0", "id": msg_id}
        if error is not None:
            body["error"] = error
        else:
            body["result"] = result
        self._send(body)

    def serve(self):
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except ValueError:
                continue
            if not isinstance(msg, dict) or "method" not in msg:
                continue                          # a response; we send none
            if "id" not in msg:
                continue                          # a notification
            threading.Thread(target=self._dispatch, args=(msg,), daemon=True).start()

    # ── methods ──────────────────────────────────────────────

    def _dispatch(self, msg):
        method, msg_id = msg["method"], msg["id"]
        params = msg.get("params") or {}
        try:
            if method == "initialize":
                asked = params.get("protocolVersion")
                self._reply(msg_id, {
                    "protocolVersion": asked if asked in PROTOCOL_VERSIONS
                    else PROTOCOL_VERSIONS[0],
                    "capabilities": {"tools": {}},
                    "serverInfo": {"name": self.name, "version": self.version},
                })
            elif method == "ping":
                self._reply(msg_id, {})
            elif method == "tools/list":
                self._reply(msg_id, {"tools": [t["spec"] for t in self.tools.values()]})
            elif method == "tools/call":
                self._reply(msg_id, self._call(params.get("name"),
                                               params.get("arguments") or {}))
            else:
                self._reply(msg_id, error={"code": -32601,
                                           "message": f"Unknown method: {method}"})
        except Exception as e:                            # noqa: BLE001
            traceback.print_exc()
            self._reply(msg_id, error={"code": -32603, "message": str(e)})

    def _call(self, name, arguments):
        tool = self.tools.get(name)
        if tool is None:
            return _text(f"No tool called '{name}'.", error=True)
        try:
            inspect.signature(tool["fn"]).bind(**arguments)
        except TypeError as e:
            return _text(f"Bad arguments for {name}: {e}", error=True)
        try:
            return _text(tool["fn"](**arguments))
        except ToolError as e:
            return _text(str(e), error=True)


def _text(text, error=False):
    return {"content": [{"type": "text", "text": str(text)}], "isError": error}
