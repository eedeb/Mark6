"""The loop that lends this computer's tools to the hosted agent.

It dials out and stays out. Nothing on the server can open a connection to a
laptop behind a home router, so the shape is: say hello, then hold a poll
open and wait to be given something to do.

    hello   ──▶  here is what I can do
    next    ──▶  (held ~45s)  ◀── a tools/call, or 204 and go round again
    result  ──▶  what it returned

Two things this deliberately does *not* do. It does not retry a failed call
— a tool that failed once on a local machine will fail the same way twice,
and the agent is better told. And it does not treat an unreachable host as
fatal: laptops sleep, wifi drops, and the right response to that is to back
off and try again, not to exit and make somebody notice.
"""
import time

from . import config, httpjson
from .mcp.pool import Pool

BACKOFF_START = 2.0
BACKOFF_MAX = 60.0

# Above the server's own long-poll ceiling (45s), so a slow network is not
# mistaken for the server simply having nothing to say.
POLL_HTTP_TIMEOUT = 60.0

# Below the relay's own RELAY_CALL_SECONDS (50s), so a slow tool is reported
# by this end — which knows which server was slow — rather than being cut off
# by the far end, which does not.
CALL_TIMEOUT = 40.0


class RelayError(Exception):
    """Fatal — the loop should stop, not back off and retry."""


class Relay:
    def __init__(self, log=print, on_connected=None):
        self.cfg = config.load()
        self.log = log
        self.on_connected = on_connected
        self.pool = None
        self.stopping = False
        self.backoff = BACKOFF_START

    def _headers(self):
        return {"Authorization": f"Bearer {self.cfg['token']}"}

    def _url(self, path):
        return f"{self.cfg['host']}/mcp/device/{path}"

    def start(self):
        enabled = [s for s in self.cfg.get("servers", []) if s.get("enabled")]
        self.pool = Pool(self.cfg.get("servers", []))

        if not enabled:
            self.log("No servers are switched on, so your agent will see no tools.")
            self.log("Add one with `run.bat add`, then `run.bat enable <name>`.")

        tools, failures = self.pool.start()
        for f in failures:
            self.log(f"  ! {f['name']}: {f['error']}")
        for t in tools:
            self.log(f"  · {t['name']}")
        self.log(f"{len(tools)} tool{'' if len(tools) == 1 else 's'} "
                 f"ready for {self.cfg.get('account')}.")

        self._hello(tools)
        self._loop()

    def _hello(self, tools):
        status, data = httpjson.post(self._url("hello"), self._headers(),
                                     {"tools": tools}, timeout=30)
        if status == 401:
            raise RelayError(
                "This computer is not paired, or it was unpaired from the "
                "dashboard. Run `run.bat login` again.")
        if status != 200:
            raise RelayError(f"The host refused the tool list (HTTP {status}).")
        self.log("Connected. Leave this running — closing it takes the tools away.\n")
        if self.on_connected:
            self.on_connected(len(tools))

    def _loop(self):
        while not self.stopping:
            try:
                status, call = httpjson.get(self._url("next"), self._headers(),
                                            timeout=POLL_HTTP_TIMEOUT)
            except httpjson.Unreachable as e:
                if self.stopping:
                    return
                self.log(f"Lost the connection ({e}). Retrying in {self.backoff:.0f}s.")
                time.sleep(self.backoff)
                self.backoff = min(self.backoff * 2, BACKOFF_MAX)
                continue

            if self.stopping:
                # Disconnected while the poll was held open. Anything it
                # brought back is for whichever relay replaced this one.
                return
            if status == 401:
                raise RelayError("This computer was unpaired. Run `run.bat login` again.")
            if status == 204 or not call:
                self.backoff = BACKOFF_START
                continue
            if status != 200:
                self.log(f"Unexpected response (HTTP {status}); retrying in "
                         f"{self.backoff:.0f}s.")
                time.sleep(self.backoff)
                self.backoff = min(self.backoff * 2, BACKOFF_MAX)
                continue

            self.backoff = BACKOFF_START
            if call.get("id"):
                self._handle(call)

    def _handle(self, call):
        params = call.get("params") or {}
        name = params.get("name")
        started = time.monotonic()
        self.log(f"→ {name}")
        try:
            result = self.pool.call(name, params.get("arguments"), timeout=CALL_TIMEOUT)
            body = {"id": call["id"], "result": result}
            self.log(f"← {name} ({(time.monotonic() - started) * 1000:.0f}ms)")
        except Exception as e:                            # noqa: BLE001
            body = {"id": call["id"], "error": {"message": str(e)}}
            self.log(f"← {name} failed: {e}")
        try:
            httpjson.post(self._url("result"), self._headers(), body, timeout=30)
        except httpjson.Unreachable as e:
            # The agent will time out and be told so. Nothing better to do
            # from here.
            self.log(f"  (could not deliver the result: {e})")

    def stop(self):
        self.stopping = True
        if self.pool:
            self.pool.stop()
