/**
 * Where the app keeps its two pieces of state: who it is signed in as, and
 * which local MCP servers it is allowed to lend out.
 *
 * Both live in the user's own config directory, never in the checkout — a
 * device token in a repo is a device token in a backup, a screen share and
 * eventually a paste.
 */
import { homedir, platform } from 'node:os';
import { join } from 'node:path';
import { mkdirSync, readFileSync, writeFileSync, chmodSync, existsSync, renameSync } from 'node:fs';

export function configDir() {
  if (process.env.MARK6_CONFIG_DIR) return process.env.MARK6_CONFIG_DIR;
  if (platform() === 'win32') {
    return join(process.env.APPDATA || join(homedir(), 'AppData', 'Roaming'), 'Mark6');
  }
  // XDG on Linux, and close enough on macOS for a terminal build. When this
  // grows a real bundle it becomes ~/Library/Application Support/Mark 6.
  return join(process.env.XDG_CONFIG_HOME || join(homedir(), '.config'), 'mark6');
}

const DEFAULTS = {
  // Where the hosted side lives. Overridable so a checkout can be pointed at
  // a staging box without editing source.
  host: process.env.MARK6_HOST || 'https://freeclaw.eedeb.dev',
  token: null,
  account: null,
  deviceName: null,
  servers: [],
};

function file() {
  return join(configDir(), 'config.json');
}

export function load() {
  try {
    const raw = JSON.parse(readFileSync(file(), 'utf8'));
    return { ...DEFAULTS, ...raw, servers: raw.servers || [] };
  } catch {
    return { ...DEFAULTS };
  }
}

export function save(cfg) {
  mkdirSync(configDir(), { recursive: true });
  const target = file();
  const tmp = `${target}.tmp`;
  writeFileSync(tmp, JSON.stringify(cfg, null, 2));
  try {
    // 0600 before it is in place, not after. Windows ignores the mode and
    // relies on the ACL of the user's own AppData, which is the equivalent.
    chmodSync(tmp, 0o600);
  } catch { /* not POSIX */ }
  renameSync(tmp, target);
  return target;
}

export function signedIn(cfg) {
  return Boolean(cfg.token && cfg.account);
}

export function configExists() {
  return existsSync(file());
}

export function configPath() {
  return file();
}
