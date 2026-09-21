/**
 * The set of local servers this computer is lending out, and the namespacing
 * that keeps two of them from claiming the same tool name.
 *
 * ## The allowlist
 *
 * A server added here is **off** until somebody enables it. That is the whole
 * safety model of this app in one sentence, and it is deliberately not a
 * setting with a sensible default: the agent on the other end reads the open
 * web, and a page it reads can try to talk it into calling things. What stops
 * that being a problem on this machine is that the list of things it can call
 * is short, chosen, and was chosen here rather than there.
 *
 * ## Names
 *
 * The relay publishes one flat tool list, so two servers that both export
 * `read_file` would collide. Every tool is exposed as `<server>_<tool>` and
 * mapped back on the way in. FreeClaw prefixes it again on its side, so the
 * model ends up calling `mcp_desktop_files_read_file` — long, but it says
 * exactly where the call is going, which is worth the characters.
 */
import { StdioServer } from './client.js';

/** Tool names have to survive OpenAI's ^[A-Za-z0-9_-]{1,64}$ after FreeClaw
 *  has added its own `mcp_desktop_` in front, so they are scrubbed here. */
function safeName(part) {
  return String(part || '').replace(/[^0-9A-Za-z_-]/g, '_').replace(/^_+|_+$/g, '');
}

export class Pool {
  constructor(entries = []) {
    this.servers = new Map();
    this.routes = new Map();       // exposed name -> { server, tool }
    for (const entry of entries) {
      if (!entry?.enabled) continue;
      this.servers.set(entry.name, new StdioServer(entry));
    }
  }

  get names() {
    return [...this.servers.keys()];
  }

  /**
   * Start every enabled server and build the published tool list.
   *
   * One server failing to start does not stop the others: a person with four
   * servers and a typo in one should get the other three, and an error they
   * can read about the fourth.
   */
  async start() {
    const tools = [];
    const failures = [];
    this.routes.clear();

    for (const [name, server] of this.servers) {
      try {
        await server.start();
      } catch (err) {
        failures.push({ name, error: err.message });
        continue;
      }
      for (const tool of server.tools) {
        const exposed = `${safeName(name)}_${safeName(tool.name)}`.slice(0, 48);
        if (this.routes.has(exposed)) {
          failures.push({ name, error: `two tools both map to '${exposed}'; rename one` });
          continue;
        }
        this.routes.set(exposed, { server, tool: tool.name });
        tools.push({
          name: exposed,
          // Says where it runs, in the description the model actually reads.
          // Without this the model has no way to tell a tool on somebody's
          // laptop from one on the open internet.
          description: `[runs on your computer, via ${name}] ${tool.description || ''}`.trim(),
          inputSchema: tool.inputSchema || { type: 'object' },
        });
      }
    }
    return { tools, failures };
  }

  /** Run one published tool. Throws with a readable message. */
  async call(exposedName, args, timeoutMs) {
    const route = this.routes.get(exposedName);
    if (!route) {
      throw new Error(`No tool called '${exposedName}' is switched on for this computer.`);
    }
    return route.server.callTool(route.tool, args, timeoutMs);
  }

  stop() {
    for (const [, server] of this.servers) server.stop();
  }
}
