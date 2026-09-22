"""Signing in, by device authorization flow.

The app never sees the account password. It asks the host for a pair of
codes, prints the short one, and polls; the person types that code into
/host/device in a browser they are already signed into, and the token comes
back on the next poll.

That shape is not ceremony. A device token authorises this program to run
commands on this computer on the agent's say-so, so it has to be issued
per-machine and revocable per-machine — which means the account has to know
that this machine exists, and a password typed into a terminal would not
tell it that.
"""
import time

from . import config, httpjson

POLL_CEILING_SECONDS = 10 * 60


class AuthError(Exception):
    pass


def login(on_code=None):
    cfg = config.load()
    status, data = httpjson.post(f"{cfg['host']}/host/device/code", {}, {})
    if status != 200 or not data or not data.get("device_code"):
        raise AuthError((data or {}).get("error") or f"Could not start pairing (HTTP {status}).")

    device_code = data["device_code"]
    user_code = data["user_code"]
    uri = data["verification_uri"]
    # The server's suggested interval, honoured rather than guessed at. Polling
    # faster does not make a person type faster.
    interval = max(2, int(data.get("interval") or 5))

    if on_code:
        on_code(user_code, uri)

    deadline = time.monotonic() + POLL_CEILING_SECONDS
    while time.monotonic() < deadline:
        time.sleep(interval)
        status, data = httpjson.post(f"{cfg['host']}/host/device/token", {},
                                     {"device_code": device_code})
        if status == 200 and data and data.get("access_token"):
            cfg.update({
                "token": data["access_token"],
                "account": data.get("account"),
                "device_name": data.get("device_name"),
            })
            config.save(cfg)
            return cfg
        error = (data or {}).get("error")
        if error == "authorization_pending":
            continue
        if error == "expired_token":
            raise AuthError("That pairing code expired before it was approved. "
                            "Run `run.bat login` again.")
        raise AuthError(error or f"Pairing failed (HTTP {status}).")
    raise AuthError("Nobody approved this computer in time. Run `run.bat login` again.")


def logout():
    cfg = config.load()
    cfg.update({"token": None, "account": None, "device_name": None})
    config.save(cfg)
