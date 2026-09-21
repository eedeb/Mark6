#!/usr/bin/env node
/**
 * A minimal stdio MCP server, for testing the app without downloading one.
 *
 * Three tools, chosen to exercise the parts that break: one that works, one
 * that fails on purpose, and one that takes longer than anybody wants so the
 * timeout path can be seen doing its job.
 *
 * It also prints a line to stderr on startup, because a real server usually
 * does and the client has to not care.
 */
import { createInterface } from 'node:readline';

process.stderr.write('echo-server: starting up\n');

const TOOLS = [
  {
    name: 'echo',
    description: 'Repeat a message back.',
    inputSchema: {
      type: 'object',
      properties: { message: { type: 'string', description: 'What to repeat' } },
      required: ['message'],
    },
  },
  {
    name: 'whoami',
    description: 'Report the machine this is running on.',
    inputSchema: { type: 'object', properties: {} },
  },
  {
    name: 'explode',
    description: 'Always fails. For testing how an error reaches the agent.',
    inputSchema: { type: 'object', properties: {} },
  },
  {
    name: 'slowly',
    description: 'Sleeps for `seconds` and then answers.',
    inputSchema: {
      type: 'object',
      properties: { seconds: { type: 'number' } },
      required: ['seconds'],
    },
  },
];

const send = (obj) => process.stdout.write(`${JSON.stringify(obj)}\n`);
const ok = (id, result) => send({ jsonrpc: '2.0', id, result });
const text = (id, s) => ok(id, { content: [{ type: 'text', text: s }] });

async function handle(msg) {
  const { id, method, params } = msg;
  if (id === undefined || id === null) return;           // a notification

  if (method === 'initialize') {
    return ok(id, {
      protocolVersion: '2025-06-18',
      capabilities: { tools: {} },
      serverInfo: { name: 'echo-server', version: '1.0.0' },
    });
  }
  if (method === 'tools/list') return ok(id, { tools: TOOLS });
  if (method === 'tools/call') {
    const name = params?.name;
    const args = params?.arguments || {};
    if (name === 'echo') return text(id, `echo: ${args.message ?? ''}`);
    if (name === 'whoami') {
      const os = await import('node:os');
      return text(id, `${os.hostname()} (${os.platform()}/${os.arch()}), pid ${process.pid}`);
    }
    if (name === 'explode') {
      return send({ jsonrpc: '2.0', id, error: { code: -32000, message: 'exploded, as requested' } });
    }
    if (name === 'slowly') {
      const seconds = Math.min(Number(args.seconds) || 1, 120);
      await new Promise((r) => setTimeout(r, seconds * 1000));
      return text(id, `slept ${seconds}s`);
    }
    return send({ jsonrpc: '2.0', id, error: { code: -32602, message: `no such tool: ${name}` } });
  }
  send({ jsonrpc: '2.0', id, error: { code: -32601, message: `no such method: ${method}` } });
}

createInterface({ input: process.stdin }).on('line', (line) => {
  if (!line.trim()) return;
  try {
    handle(JSON.parse(line));
  } catch (err) {
    process.stderr.write(`echo-server: bad line: ${err.message}\n`);
  }
});
