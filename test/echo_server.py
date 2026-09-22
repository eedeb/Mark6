#!/usr/bin/env python3
"""A minimal stdio MCP server, for testing the app without downloading one.

Four tools, chosen to exercise the parts that break: one that works, one that
reports the machine it is running on, one that fails on purpose, and one that
takes longer than anybody wants so the timeout path can be seen doing its job.

It also prints a line to stderr on startup, because a real server usually
does and the client has to not care.
"""
import json
import os
import platform
import sys
import time

print("echo-server: starting up", file=sys.stderr, flush=True)

TOOLS = [
    {
        "name": "echo",
        "description": "Repeat a message back.",
        "inputSchema": {
            "type": "object",
            "properties": {"message": {"type": "string", "description": "What to repeat"}},
            "required": ["message"],
        },
    },
    {
        "name": "whoami",
        "description": "Report the machine this is running on.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "explode",
        "description": "Always fails. For testing how an error reaches the agent.",
        "inputSchema": {"type": "object", "properties": {}},
    },
    {
        "name": "slowly",
        "description": "Sleeps for `seconds` and then answers.",
        "inputSchema": {
            "type": "object",
            "properties": {"seconds": {"type": "number"}},
            "required": ["seconds"],
        },
    },
]


def send(obj):
    sys.stdout.write(json.dumps(obj) + "\n")
    sys.stdout.flush()


def ok(msg_id, result):
    send({"jsonrpc": "2.0", "id": msg_id, "result": result})


def text(msg_id, s):
    ok(msg_id, {"content": [{"type": "text", "text": s}]})


def handle(msg):
    msg_id = msg.get("id")
    method = msg.get("method")
    params = msg.get("params") or {}
    if msg_id is None:
        return                          # a notification

    if method == "initialize":
        return ok(msg_id, {
            "protocolVersion": "2025-06-18",
            "capabilities": {"tools": {}},
            "serverInfo": {"name": "echo-server", "version": "1.0.0"},
        })
    if method == "tools/list":
        return ok(msg_id, {"tools": TOOLS})
    if method == "tools/call":
        name = params.get("name")
        args = params.get("arguments") or {}
        if name == "echo":
            return text(msg_id, f"echo: {args.get('message', '')}")
        if name == "whoami":
            return text(msg_id, f"{platform.node()} ({platform.system().lower()}/"
                                f"{platform.machine()}), pid {os.getpid()}")
        if name == "explode":
            return send({"jsonrpc": "2.0", "id": msg_id,
                        "error": {"code": -32000, "message": "exploded, as requested"}})
        if name == "slowly":
            seconds = min(float(args.get("seconds") or 1), 120)
            time.sleep(seconds)
            return text(msg_id, f"slept {seconds}s")
        return send({"jsonrpc": "2.0", "id": msg_id,
                    "error": {"code": -32602, "message": f"no such tool: {name}"}})
    send({"jsonrpc": "2.0", "id": msg_id,
         "error": {"code": -32601, "message": f"no such method: {method}"}})


for raw_line in sys.stdin:
    line = raw_line.strip()
    if not line:
        continue
    try:
        handle(json.loads(line))
    except ValueError as e:
        print(f"echo-server: bad line: {e}", file=sys.stderr, flush=True)
