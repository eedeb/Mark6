/**
 * The loop that lends this computer's tools to the hosted agent.
 *
 * It dials out and stays out. Nothing on the server can open a connection to
 * a laptop behind a home router, so the shape is: say hello, then hold a poll
 * open and wait to be given something to do.
 *
 *     hello   ──▶  here is what I can do
 *     next    ──▶  (held ~45s)  ◀── a tools/call, or 204 and go round again
 *     result  ──▶  what it returned
 *
 * Two things this deliberately does *not* do. It does not retry a failed call
 * — a tool that failed once on a local machine will fail the same way twice,
 * and the agent is better told. And it does not treat an unreachable host as
 * fatal: laptops sleep, wifi drops, and the right response to that is to back
 * off and try again, not to exit and make somebody notice.
 */
import { load } from './config.js';
import { Pool } from './mcp/pool.js';

const BACKOFF_START_MS = 2_000;
const BACKOFF_MAX_MS = 60_000;

// Below the relay's own RELAY_CALL_SECONDS (50s), so a slow tool is reported
// by this end — which knows which server was slow — rather than being cut off
// by the far end, which does not.
const CALL_TIMEOUT_MS = 40_000;

export class Relay {
  constructor({ log = console.log } = {}) {
    this.cfg = load();
    this.log = log;
    this.pool = null;
    this.stopping = false;
    this.backoff = BACKOFF_START_MS;
  }

  headers(extra = {}) {
    return {
      Authorization: `Bearer ${this.cfg.token}`,
      'Content-Type': 'application/json',
      ...extra,
    };
  }

  url(path) {
    return `${this.cfg.host}/mcp/device/${path}`;
  }

  async start() {
    const enabled = (this.cfg.servers || []).filter((s) => s.enabled);
    this.pool = new Pool(this.cfg.servers || []);

    if (!enabled.length) {
      this.log('No servers are switched on, so your agent will see no tools.');
      this.log('Add one with `mark6 add`, then `mark6 enable <name>`.');
    }

    const { tools, failures } = await this.pool.start();
    for (const f of failures) this.log(`  ! ${f.name}: ${f.error}`);
    for (const t of tools) this.log(`  · ${t.name}`);
    this.log(`${tools.length} tool${tools.length === 1 ? '' : 's'} ready for ${this.cfg.account}.`);

    await this.hello(tools);
    await this.loop();
  }

  async hello(tools) {
    const res = await fetch(this.url('hello'), {
      method: 'POST',
      headers: this.headers(),
      body: JSON.stringify({ tools }),
    });
    if (res.status === 401) {
      throw new Error(
        'This computer is not paired, or it was unpaired from the dashboard. '
        + 'Run `mark6 login` again.');
    }
    if (!res.ok) throw new Error(`The host refused the tool list (HTTP ${res.status}).`);
    this.log('Connected. Leave this running — closing it takes the tools away.\n');
  }

  async loop() {
    while (!this.stopping) {
      let call = null;
      try {
        const res = await fetch(this.url('next'), { headers: this.headers() });
        if (res.status === 401) {
          throw new Error('This computer was unpaired. Run `mark6 login` again.');
        }
        if (res.status === 204) { this.backoff = BACKOFF_START_MS; continue; }
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        call = await res.json();
        this.backoff = BACKOFF_START_MS;
      } catch (err) {
        if (this.stopping) return;
        // An unpairing is permanent and worth stopping for; anything else is
        // weather.
        if (/unpaired/i.test(err.message)) throw err;
        this.log(`Lost the connection (${err.message}). Retrying in ${Math.round(this.backoff / 1000)}s.`);
        await new Promise((r) => setTimeout(r, this.backoff));
        this.backoff = Math.min(this.backoff * 2, BACKOFF_MAX_MS);
        continue;
      }
      if (call?.id) await this.handle(call);
    }
  }

  async handle(call) {
    const name = call.params?.name;
    const started = Date.now();
    this.log(`→ ${name}`);
    let body;
    try {
      const result = await this.pool.call(name, call.params?.arguments, CALL_TIMEOUT_MS);
      body = { id: call.id, result };
      this.log(`← ${name} (${Date.now() - started}ms)`);
    } catch (err) {
      body = { id: call.id, error: { message: err.message } };
      this.log(`← ${name} failed: ${err.message}`);
    }
    try {
      await fetch(this.url('result'), {
        method: 'POST', headers: this.headers(), body: JSON.stringify(body),
      });
    } catch (err) {
      // The agent will time out and be told so. Nothing better to do from here.
      this.log(`  (could not deliver the result: ${err.message})`);
    }
  }

  stop() {
    this.stopping = true;
    this.pool?.stop();
  }
}
