"""One local MCP server, spoken to over its stdin and stdout.

The stdio transport is newline-delimited JSON-RPC: one object per line out,
one per line back, matched by id. The fiddly parts, all of which are the
reason this is a module and not a few inline calls:

  * **stdout is the channel, stderr is the diary.** A server that prints a
    banner to stdout has corrupted the protocol; one that prints it to stderr
    is being friendly. So stderr is captured for diagnostics and never
    parsed, and a non-JSON line on stdout is dropped rather than being
    allowed to reject a pending call.
  * **Windows will not find `npx` on its own.** `Popen(['npx', ...],
    shell=False)` uses CreateProcess directly, which — unlike cmd.exe — does
    not try each PATHEXT extension against PATH. `npx` on a real npm install
    is `npx.cmd`, so spawning it by bare name fails with "file not found"
    even though `npx --version` works fine from a prompt. Resolving the
    extension ourselves is what avoids `shell=True`, which would mean pasting
    a user's arguments into a command line instead of passing them as an
    argv array.
  * **A server that dies mid-call** must fail every call waiting on it rather
    than leaving them to time out one at a time.
"""
import json
import os
import subprocess
import sys
import threading
import time

# The app's own root (the directory holding src/, four levels above this
# file). Used as the default working directory for a spawned
# server whose entry gives no cwd of its own, so a relative path a person
# typed in `add` — most usefully the bundled `runtime\python.exe` on Windows
# — resolves the same way whether `run.bat` was double-clicked, launched from
# a shortcut, or invoked with an absolute path from somewhere else entirely.
# Without this, that resolution silently depended on the *caller's* current
# directory, which nothing about running a batch file guarantees.
APP_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__)))))

START_TIMEOUT = 60.0
CALL_TIMEOUT = 45.0

# Hides the console window a Node-based server would otherwise pop on
# Windows. Not defined on other platforms, so this degrades to "nothing
# special" everywhere else.
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)


def resolve_command(cmd):
    """An executable's real path on this platform.

    On Windows a bare `npx` is really `npx.cmd`, and Popen does not consult
    PATHEXT for you (see the module docstring). Doing the search here keeps
    `shell=False`, so arguments reach the child exactly as they were written
    and nothing is ever parsed as a command line."""
    if os.name != "nt":
        return cmd                      # POSIX exec() already searches PATH
    if os.sep in cmd or (os.altsep and os.altsep in cmd):
        return cmd                      # already a path, not a bare name
    if os.path.splitext(cmd)[1]:
        return cmd                      # already has an extension
    exts = [e for e in os.environ.get("PATHEXT", ".COM;.EXE;.BAT;.CMD").split(";") if e]
    for directory in os.environ.get("PATH", "").split(os.pathsep):
        if not directory:
            continue
        for ext in exts:
            candidate = os.path.join(directory, cmd + ext)
            if os.path.isfile(candidate):
                return candidate
    return cmd                          # let Popen report it


class ServerError(Exception):
    pass


class _Pending:
    __slots__ = ("event", "result", "error")

    def __init__(self):
        self.event = threading.Event()
        self.result = None
        self.error = None


class StdioServer:
    def __init__(self, name, command, args=None, env=None, cwd=None):
        self.name = name
        self.command = command
        self.args = list(args or [])
        self.env = dict(env or {})
        self.cwd = cwd
        self.proc = None
        self.tools = []
        self.stderr_lines = []
        self._next_id = 1
        self._pending = {}
        self._lock = threading.Lock()
        self._start_lock = threading.Lock()

    @property
    def running(self):
        return self.proc is not None and self.proc.poll() is None

    def start(self):
        """Start the child and run the MCP handshake. Idempotent — a second
        caller while one is already starting waits for that one instead of
        racing a second child into existence."""
        with self._start_lock:
            if self.running:
                return self.tools
            self._start()
            return self.tools

    def _start(self):
        env = {**os.environ, **self.env}
        try:
            self.proc = subprocess.Popen(
                [resolve_command(self.command), *self.args],
                cwd=self.cwd or APP_ROOT, env=env,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1, encoding="utf-8", errors="replace",
                creationflags=_CREATE_NO_WINDOW,
            )
        except OSError as e:
            raise ServerError(f"'{self.name}' could not start: {e}") from e

        threading.Thread(target=self._read_stdout, daemon=True).start()
        threading.Thread(target=self._read_stderr, daemon=True).start()
        threading.Thread(target=self._watch_exit, daemon=True).start()

        try:
            self.request("initialize", {
                "protocolVersion": "2025-06-18",
                "capabilities": {},
                "clientInfo": {"name": "Mark 6", "version": "0.1.0"},
            }, timeout=START_TIMEOUT)
            self.notify("notifications/initialized")
            result = self.request("tools/list", {}, timeout=START_TIMEOUT)
        except ServerError:
            self.stop()
            raise
        self.tools = [t for t in (result or {}).get("tools", []) if t and t.get("name")]

    def _read_stdout(self):
        try:
            for line in self.proc.stdout:
                self._on_line(line)
        except (OSError, ValueError):
            pass                         # pipe closed under us

    def _read_stderr(self):
        try:
            for line in self.proc.stderr:
                line = line.rstrip("\n")
                self.stderr_lines.append(line)
                # Kept bounded: a chatty server would otherwise grow this
                # forever for the life of the connection.
                if len(self.stderr_lines) > 50:
                    self.stderr_lines.pop(0)
        except (OSError, ValueError):
            pass

    def _watch_exit(self):
        proc = self.proc
        proc.wait()
        code = proc.returncode
        tail = "\n".join(self.stderr_lines[-5:])
        why = f"'{self.name}' exited (code {code})"
        self._fail_all(ServerError(f"{why}:\n{tail}" if tail else why))

    def _on_line(self, line):
        line = line.strip()
        if not line:
            return
        try:
            msg = json.loads(line)
        except ValueError:
            return                       # a banner on stdout: not ours, not fatal
        call_id = msg.get("id")
        if call_id is None:
            return                       # a notification
        with self._lock:
            slot = self._pending.pop(call_id, None)
        if slot is None:
            return
        if "error" in msg:
            slot.error = msg["error"]
        else:
            slot.result = msg.get("result")
        slot.event.set()

    def _fail_all(self, err):
        with self._lock:
            slots = list(self._pending.values())
            self._pending.clear()
        for slot in slots:
            slot.error = {"message": str(err)}
            slot.event.set()

    def _write(self, obj):
        if not self.running or not self.proc.stdin:
            raise ServerError(f"'{self.name}' is not running")
        try:
            self.proc.stdin.write(json.dumps(obj) + "\n")
            self.proc.stdin.flush()
        except (OSError, ValueError) as e:
            raise ServerError(f"'{self.name}' is not accepting input: {e}") from e

    def notify(self, method, params=None):
        try:
            self._write({"jsonrpc": "2.0", "method": method, "params": params or {}})
        except ServerError:
            pass                          # a notification is not worth failing over

    def request(self, method, params, timeout=CALL_TIMEOUT):
        with self._lock:
            call_id = self._next_id
            self._next_id += 1
            slot = _Pending()
            self._pending[call_id] = slot
        try:
            self._write({"jsonrpc": "2.0", "id": call_id, "method": method,
                        "params": params or {}})
        except ServerError:
            with self._lock:
                self._pending.pop(call_id, None)
            raise
        if not slot.event.wait(timeout):
            with self._lock:
                self._pending.pop(call_id, None)
            raise ServerError(
                f"'{self.name}' did not answer {method} within {timeout:.0f}s")
        if slot.error is not None:
            message = slot.error.get("message") if isinstance(slot.error, dict) else slot.error
            raise ServerError(message or json.dumps(slot.error))
        return slot.result

    def call_tool(self, name, arguments, timeout=CALL_TIMEOUT):
        if not self.running:
            self.start()
        return self.request("tools/call", {"name": name, "arguments": arguments or {}},
                            timeout=timeout)

    def stop(self):
        proc, self.proc = self.proc, None
        if not proc:
            return
        self._fail_all(ServerError(f"'{self.name}' was stopped"))
        try:
            proc.terminate()
            try:
                proc.wait(timeout=3)
            except subprocess.TimeoutExpired:
                proc.kill()
        except OSError:
            pass                          # already gone
