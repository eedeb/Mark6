/**
 * One local MCP server, spoken to over its stdin and stdout.
 *
 * The stdio transport is newline-delimited JSON-RPC: one object per line out,
 * one per line back, matched by id. The fiddly parts, all of which are the
 * reason this is a file and not ten lines inline:
 *
 *   * **stdout is the channel, stderr is the diary.** A server that prints a
 *     banner to stdout has corrupted the protocol; one that prints it to
 *     stderr is being friendly. So stderr is captured for diagnostics and
 *     never parsed, and a non-JSON line on stdout is dropped rather than
 *     being allowed to reject a pending call.
 *   * **Windows.** `npx` is `npx.cmd`, and spawning it by bare name fails with
 *     ENOENT. Resolving the extension ourselves is what avoids `shell: true`,
 *     which would mean pasting a user's arguments into a command line.
 *   * **A server that dies mid-call** must fail the calls waiting on it rather
 *     than leaving them to time out one at a time.
 */
import { spawn } from 'node:child_process';
import { createInterface } from 'node:readline';
import { delimiter, join } from 'node:path';
import { existsSync } from 'node:fs';

const PROTOCOL_VERSION = '2025-06-18';
const CLIENT_INFO = { name: 'Mark 6', version: '0.1.0' };

const START_TIMEOUT_MS = 60_000;
const CALL_TIMEOUT_MS = 45_000;

/**
 * An executable's real path on this platform.
 *
 * On Windows a bare `npx` is really `npx.cmd`, and `spawn` does not look at
 * PATHEXT for you. Doing the search here keeps `shell: false`, so arguments
 * reach the child exactly as they were written and nothing is ever parsed as
 * a command line.
 */
function resolveCommand(cmd) {
  if (process.platform !== 'win32') return cmd;
  if (cmd.includes('/') || cmd.includes('\\')) return cmd;
  const exts = (process.env.PATHEXT || '.COM;.EXE;.BAT;.CMD').split(';').filter(Boolean);
  for (const dir of (process.env.PATH || '').split(delimiter)) {
    if (!dir) continue;
    for (const ext of exts) {
      const candidate = join(dir, cmd + ext);
      if (existsSync(candidate)) return candidate;
    }
  }
  return cmd;               // let spawn report it
}

export class StdioServer {
  constructor({ name, command, args = [], env = {}, cwd }) {
    this.name = name;
    this.command = command;
    this.args = args;
    this.env = env;
    this.cwd = cwd;
    this.child = null;
    this.tools = [];
    this.nextId = 1;
    this.pending = new Map();
    this.stderr = [];
    this.starting = null;
  }

  get running() {
    return Boolean(this.child) && this.child.exitCode === null && !this.child.killed;
  }

  async start() {
    if (this.running) return;
    // Concurrent callers share one startup rather than racing two children.
    if (this.starting) return this.starting;
    this.starting = this._start().finally(() => { this.starting = null; });
    return this.starting;
  }

  async _start() {
    const child = spawn(resolveCommand(this.command), this.args, {
      cwd: this.cwd || undefined,
      env: { ...process.env, ...this.env },
      stdio: ['pipe', 'pipe', 'pipe'],
      windowsHide: true,
      shell: false,
    });
    this.child = child;

    createInterface({ input: child.stdout }).on('line', (line) => this._onLine(line));
    createInterface({ input: child.stderr }).on('line', (line) => {
      // Kept bounded: a chatty server would otherwise grow this forever.
      this.stderr.push(line);
      if (this.stderr.length > 50) this.stderr.shift();
    });

    child.on('exit', (code, signal) => {
      const why = `'${this.name}' exited (${signal || `code ${code}`})`;
      const tail = this.stderr.slice(-5).join('\n');
      this._failAll(new Error(tail ? `${why}:\n${tail}` : why));
    });
    child.on('error', (err) => this._failAll(
      new Error(`'${this.name}' could not start: ${err.message}`)));

    await this.request('initialize', {
      protocolVersion: PROTOCOL_VERSION,
      capabilities: {},
      clientInfo: CLIENT_INFO,
    }, START_TIMEOUT_MS);
    this.notify('notifications/initialized');

    const result = await this.request('tools/list', {}, START_TIMEOUT_MS);
    this.tools = (result?.tools || []).filter((t) => t && t.name);
    return this.tools;
  }

  _onLine(line) {
    const text = line.trim();
    if (!text) return;
    let msg;
    try {
      msg = JSON.parse(text);
    } catch {
      return;               // a banner on stdout: not ours, not fatal
    }
    if (msg.id === undefined || msg.id === null) return;   // a notification
    const slot = this.pending.get(msg.id);
    if (!slot) return;
    this.pending.delete(msg.id);
    clearTimeout(slot.timer);
    if (msg.error) {
      slot.reject(new Error(msg.error.message || JSON.stringify(msg.error)));
    } else {
      slot.resolve(msg.result);
    }
  }

  _failAll(err) {
    for (const [, slot] of this.pending) {
      clearTimeout(slot.timer);
      slot.reject(err);
    }
    this.pending.clear();
  }

  _write(obj) {
    if (!this.child?.stdin?.writable) throw new Error(`'${this.name}' is not running`);
    this.child.stdin.write(`${JSON.stringify(obj)}\n`);
  }

  notify(method, params) {
    try {
      this._write({ jsonrpc: '2.0', method, params: params || {} });
    } catch { /* a notification is not worth failing over */ }
  }

  request(method, params, timeoutMs = CALL_TIMEOUT_MS) {
    const id = this.nextId++;
    return new Promise((resolve, reject) => {
      const timer = setTimeout(() => {
        this.pending.delete(id);
        reject(new Error(`'${this.name}' did not answer ${method} within ${Math.round(timeoutMs / 1000)}s`));
      }, timeoutMs);
      this.pending.set(id, { resolve, reject, timer });
      try {
        this._write({ jsonrpc: '2.0', id, method, params: params || {} });
      } catch (err) {
        clearTimeout(timer);
        this.pending.delete(id);
        reject(err);
      }
    });
  }

  async callTool(name, args, timeoutMs) {
    if (!this.running) await this.start();
    return this.request('tools/call', { name, arguments: args || {} }, timeoutMs);
  }

  stop() {
    if (!this.child) return;
    this._failAll(new Error(`'${this.name}' was stopped`));
    try { this.child.kill(); } catch { /* already gone */ }
    this.child = null;
  }
}
