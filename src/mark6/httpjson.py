"""One tiny wrapper around urllib so the rest of the app never touches it
directly. Stdlib only, on purpose — see config.py.

Every call returns `(status, data)` rather than raising on a non-2xx status.
The device flow and the relay both treat "the server answered 401" and "the
server answered 200" as two ordinary outcomes to branch on, not as a
try/except split across every call site.
"""
import json
import socket
import urllib.error
import urllib.request


class Unreachable(Exception):
    """The host could not be reached at all — DNS, TCP, TLS. Distinct from an
    HTTP error status, which is a real answer and handled as one."""


# freeclaw.eedeb.dev sits behind Cloudflare, and Cloudflare's bot protection
# refuses urllib's default User-Agent ("Python-urllib/3.x") outright — error
# 1010, before the request ever reaches nginx. Not specific to this app: any
# stock urllib client hits the same wall. A real browser-shaped string is
# enough; nothing here is actually pretending to be a browser otherwise.
_USER_AGENT = "Mark6/0.1 (+https://freeclaw.eedeb.dev)"


def _do(method, url, headers, body, timeout):
    data = json.dumps(body).encode("utf-8") if body is not None else None
    req = urllib.request.Request(url, data=data, method=method, headers={
        "User-Agent": _USER_AGENT,
        **headers,
        **({"Content-Type": "application/json"} if data is not None else {}),
    })
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read()
            return resp.status, (json.loads(raw) if raw else None)
    except urllib.error.HTTPError as e:
        raw = e.read()
        try:
            parsed = json.loads(raw) if raw else None
        except ValueError:
            parsed = None
        return e.code, parsed
    except (urllib.error.URLError, socket.timeout, TimeoutError) as e:
        raise Unreachable(str(getattr(e, "reason", e) or e)) from e


def post(url, headers, body=None, timeout=30):
    return _do("POST", url, headers, body, timeout)


def get(url, headers, timeout=30):
    return _do("GET", url, headers, None, timeout)
