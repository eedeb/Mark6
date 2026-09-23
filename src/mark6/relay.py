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
        self.tools = []
        self.stopping = False
        self.backoff = BACKOFF_START

    def _headers(self, polling=False):
        headers = {"Authorization": f"Bearer {self.cfg['token']}"}
        if polling:
            # Tells the host it may answer this poll with 409 to ask for the
            # tool list again. Opt-in, because an app that did not understand
            # 409 would treat it as a transport error and back off forever.
            headers["X-Mark6-Rehello"] = "1"
        return headers

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
        # What each server said on the way up. A stdio server's stderr is its
        # only way to tell anyone anything — the protocol owns stdout — and it
        # was being captured for crash reports and otherwise thrown away. That
        # left questions like "is the game-input shim actually loaded?"
        # unanswerable from here, when the server had said so in as many words.
        for name, note in self.pool.startup_notes():
            self.log(f"  [{name}] {note}")
        for t in tools:
            self.log(f"  · {t['name']}")
        self.log(f"{len(tools)} tool{'' if len(tools) == 1 else 's'} "
                 f"ready for {self.cfg.get('account')}.")

        self.tools = tools
        self._hello(announce=True)
        self._loop()

    def _hello(self, announce=False):
        """Publish what this computer can do.

        Sent again whenever the host says it has nothing for us — see _loop.
        The host keeps the list in memory, so anything that restarts it loses
        the list while this app is none the wiser: the poll below still
        succeeds, the token is still good, and the only symptom is an agent
        that quietly sees no tools. Re-sending is what closes that gap from
        this end."""
        status, data = httpjson.post(self._url("hello"), self._headers(),
                                     {"tools": self.tools}, timeout=30)
        if status == 401:
            raise RelayError(
                "This computer is not paired, or it was unpaired from the "
                "dashboard. Run `run.bat login` again.")
        if status != 200:
            raise RelayError(f"The host refused the tool list (HTTP {status}).")
        if announce:
            self.log("Connected. Leave this running — closing it takes the tools away.\n")
            if self.on_connected:
                self.on_connected(len(self.tools))

    def _loop(self):
        while not self.stopping:
            try:
                status, call = httpjson.get(self._url("next"),
                                            self._headers(polling=True),
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
            if status == 409:
                # The host has no tool list for this computer — it restarted,
                # or never had one. It cannot ask for it out of band, because
                # nothing on that side can open a connection to a machine
                # behind a router; this poll is the only channel there is, so
                # the answer to "I have nothing for you" is to send it again.
                self.log("The host lost this computer's tool list — sending it again.")
                self._hello()
                self.backoff = BACKOFF_START
                continue
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
