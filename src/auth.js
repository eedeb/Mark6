/**
 * Signing in, by device authorization flow.
 *
 * The app never sees the account password. It asks the host for a pair of
 * codes, prints the short one, and polls; the person types that code into
 * /host/device in a browser they are already signed into, and the token comes
 * back on the next poll.
 *
 * That shape is not ceremony. A device token authorises this program to run
 * commands on this computer on the agent's say-so, so it has to be issued
 * per-machine and revocable per-machine — which means the account has to know
 * that this machine exists, and a password typed into a terminal would not
 * tell it that.
 */
import { load, save } from './config.js';

const POLL_CEILING_MS = 10 * 60 * 1000;

async function postJSON(url, body) {
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body || {}),
  });
  let data = null;
  try { data = await res.json(); } catch { /* empty or not JSON */ }
  return { status: res.status, data };
}

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function login({ onCode } = {}) {
  const cfg = load();
  const begin = await postJSON(`${cfg.host}/host/device/code`, {});
  if (begin.status !== 200 || !begin.data?.device_code) {
    throw new Error(begin.data?.error || `Could not start pairing (HTTP ${begin.status}).`);
  }

  const { device_code: deviceCode, user_code: userCode,
          verification_uri: uri, interval } = begin.data;
  onCode?.({ userCode, uri });

  const deadline = Date.now() + POLL_CEILING_MS;
  // The server's suggested interval, honoured rather than guessed at. Polling
  // faster does not make a person type faster.
  const waitMs = Math.max(2, Number(interval) || 5) * 1000;

  while (Date.now() < deadline) {
    await sleep(waitMs);
    const poll = await postJSON(`${cfg.host}/host/device/token`, { device_code: deviceCode });
    if (poll.status === 200 && poll.data?.access_token) {
      const next = {
        ...cfg,
        token: poll.data.access_token,
        account: poll.data.account,
        deviceName: poll.data.device_name,
      };
      save(next);
      return next;
    }
    if (poll.data?.error === 'authorization_pending') continue;
    throw new Error(
      poll.data?.error === 'expired_token'
        ? 'That pairing code expired before it was approved. Run `mark6 login` again.'
        : (poll.data?.error || `Pairing failed (HTTP ${poll.status}).`));
  }
  throw new Error('Nobody approved this computer in time. Run `mark6 login` again.');
}

export function logout() {
  const cfg = load();
  save({ ...cfg, token: null, account: null, deviceName: null });
}
