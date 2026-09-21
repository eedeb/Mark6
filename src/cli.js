#!/usr/bin/env node
/**
 * Mark 6 — the terminal build.
 *
 * No window yet. This is the daemon half of the desktop app, which is the half
 * that does anything: it signs this computer in, runs the MCP servers you
 * choose, and lends their tools to your hosted agent for as long as it is
 * running. A UI goes on top of exactly this later.
 *
 *   mark6 login                          pair this computer with your account
 *   mark6 add files -- npx -y @modelcontextprotocol/server-filesystem ~/notes
 *   mark6 enable files                   switch it on (everything starts off)
 *   mark6 run                            connect, and stay connected
 */
import { parseArgs } from 'node:util';
import { load, save, signedIn, configPath } from './config.js';
import { login, logout } from './auth.js';
import { Relay } from './relay.js';
import { StdioServer } from './mcp/client.js';

const argv = process.argv.slice(2);
const command = argv[0];

function die(message) {
  console.error(message);
  process.exit(1);
}

function requireAccount(cfg) {
  if (!signedIn(cfg)) die('This computer is not paired yet. Run `mark6 login` first.');
}

// ── commands ─────────────────────────────────────────────────

async function cmdLogin() {
  const cfg = await login({
    onCode: ({ userCode, uri }) => {
      console.log('');
      console.log('  Open this page while signed in to your Mark 6 account:');
      console.log(`      ${uri}`);
      console.log('');
      console.log('  and enter this code:');
      console.log(`      ${userCode}`);
      console.log('');
      console.log('  Waiting for you to approve it...');
    },
  });
  console.log(`\nPaired as "${cfg.deviceName}" on account ${cfg.account}.`);
  console.log(`Config: ${configPath()}`);
  console.log('\nNext: add a server with `mark6 add`, then `mark6 run`.');
}

function cmdLogout() {
  logout();
  console.log('Signed out on this computer.');
  console.log('This does not unpair it — do that from your dashboard, which is '
    + 'what actually stops it being used.');
}

/**
 * `mark6 add <name> -- <command> [args...]`
 *
 * Everything after `--` is the command line, taken as an argv array and never
 * as a string: it is spawned without a shell, so a path with a space in it is
 * one argument and nothing is ever re-parsed.
 */
function cmdAdd() {
  const sep = argv.indexOf('--');
  if (sep === -1 || sep === argv.length - 1) {
    die('Usage: mark6 add <name> -- <command> [args...]\n'
      + 'Example: mark6 add files -- npx -y @modelcontextprotocol/server-filesystem /home/me/notes');
  }
  const name = argv[1];
  if (!name || name === '--') die('Give the server a short name: mark6 add <name> -- <command>');
  if (!/^[A-Za-z0-9_-]{1,32}$/.test(name)) {
    die('A server name can be letters, numbers, dash and underscore, up to 32 characters.');
  }

  const [cmd, ...args] = argv.slice(sep + 1);
  const cfg = load();
  if (cfg.servers.some((s) => s.name === name)) die(`There is already a server called '${name}'.`);

  cfg.servers.push({ name, command: cmd, args, env: {}, enabled: false });
  save(cfg);
  console.log(`Added '${name}': ${cmd} ${args.join(' ')}`);
  console.log(`It is OFF. Switch it on with:  mark6 enable ${name}`);
}

function cmdRemove() {
  const name = argv[1];
  if (!name) die('Usage: mark6 remove <name>');
  const cfg = load();
  const before = cfg.servers.length;
  cfg.servers = cfg.servers.filter((s) => s.name !== name);
  if (cfg.servers.length === before) die(`No server called '${name}'.`);
  save(cfg);
  console.log(`Removed '${name}'.`);
}

function cmdToggle(enabled) {
  const name = argv[1];
  if (!name) die(`Usage: mark6 ${enabled ? 'enable' : 'disable'} <name>`);
  const cfg = load();
  const server = cfg.servers.find((s) => s.name === name);
  if (!server) die(`No server called '${name}'.`);
  server.enabled = enabled;
  save(cfg);
  console.log(`'${name}' is now ${enabled ? 'ON' : 'OFF'}.`);
  if (enabled) console.log('Restart `mark6 run` for your agent to see it.');
}

function cmdList() {
  const cfg = load();
  console.log(signedIn(cfg)
    ? `Paired as "${cfg.deviceName}" on account ${cfg.account}.`
    : 'Not paired. Run `mark6 login`.');
  console.log(`Config: ${configPath()}\n`);
  if (!cfg.servers.length) {
    console.log('No servers yet. Add one:');
    console.log('  mark6 add files -- npx -y @modelcontextprotocol/server-filesystem ~/notes');
    return;
  }
  for (const s of cfg.servers) {
    console.log(`  [${s.enabled ? 'ON ' : 'off'}] ${s.name.padEnd(16)} ${s.command} ${(s.args || []).join(' ')}`);
  }
  const off = cfg.servers.filter((s) => !s.enabled).length;
  if (off) console.log(`\n${off} switched off. Your agent cannot see those at all.`);
}

/**
 * `mark6 test <name>` — start one server and list its tools, without involving
 * the network at all. The first thing to reach for when a server is not
 * showing up: it separates "my command line is wrong" from "the pairing is
 * wrong", which are otherwise the same symptom.
 */
async function cmdTest() {
  const name = argv[1];
  if (!name) die('Usage: mark6 test <name>');
  const cfg = load();
  const entry = cfg.servers.find((s) => s.name === name);
  if (!entry) die(`No server called '${name}'.`);

  console.log(`Starting '${name}': ${entry.command} ${(entry.args || []).join(' ')}`);
  const server = new StdioServer(entry);
  try {
    const tools = await server.start();
    console.log(`\nIt works. ${tools.length} tool${tools.length === 1 ? '' : 's'}:`);
    for (const t of tools) {
      console.log(`  ${t.name.padEnd(28)} ${(t.description || '').split('\n')[0].slice(0, 70)}`);
    }
    if (!entry.enabled) console.log(`\nStill switched off. Turn it on with: mark6 enable ${name}`);
  } catch (err) {
    console.error(`\nIt did not start: ${err.message}`);
    if (server.stderr.length) {
      console.error('\nWhat it printed:');
      for (const line of server.stderr.slice(-15)) console.error(`  ${line}`);
    }
    process.exitCode = 1;
  } finally {
    server.stop();
  }
}

async function cmdRun() {
  const cfg = load();
  requireAccount(cfg);
  const relay = new Relay();
  const shutdown = () => {
    console.log('\nDisconnecting. Your agent loses these tools until you run this again.');
    relay.stop();
    process.exit(0);
  };
  process.on('SIGINT', shutdown);
  process.on('SIGTERM', shutdown);

  try {
    await relay.start();
  } catch (err) {
    relay.stop();
    die(`\n${err.message}`);
  }
}

function usage() {
  console.log(`Mark 6 — lends this computer's MCP servers to your hosted agent.

  mark6 login                  pair this computer with your Mark 6 account
  mark6 logout                 forget the pairing on this computer
  mark6 list                   what is configured, and what is switched on
  mark6 add <name> -- <cmd>    add a local MCP server (starts switched off)
  mark6 enable <name>          let your agent use it
  mark6 disable <name>         stop letting your agent use it
  mark6 remove <name>          forget it entirely
  mark6 test <name>            start it locally and list its tools
  mark6 run                    connect, and stay connected

Every server starts switched off. Your agent can only ever call the ones you
have turned on, on this computer, while this is running.`);
}

const commands = {
  login: cmdLogin,
  logout: cmdLogout,
  list: cmdList,
  add: cmdAdd,
  remove: cmdRemove,
  enable: () => cmdToggle(true),
  disable: () => cmdToggle(false),
  test: cmdTest,
  run: cmdRun,
};

const handler = commands[command];
if (!handler) {
  usage();
  process.exit(command ? 1 : 0);
}
await handler();
