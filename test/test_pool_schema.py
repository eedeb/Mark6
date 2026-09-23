"""The published tool list, and the corrections folded into it.

Run:  python3 test/test_pool_schema.py

No servers are started. These exercise the shape of what gets published,
which is the only thing the model on the other end ever sees.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(
    os.path.abspath(__file__))), "src"))

from mark6 import config                                    # noqa: E402
from mark6.mcp.pool import Pool, _overlay                    # noqa: E402

FAILURES = []


def check(name, got, want):
    if got == want:
        print(f"  ok   {name}")
    else:
        print(f"  FAIL {name}\n         got {got!r}\n        want {want!r}")
        FAILURES.append(name)


def realhands_tool():
    """The `computer` tool as realhands actually describes it: `action` a bare
    string, with none of its values named anywhere."""
    return {
        "name": "computer",
        "description": "Control the computer.",
        "inputSchema": {
            "type": "object",
            "properties": {
                "action": {"type": "string"},
                "text": {"type": "string"},
                "duration": {"type": "number"},
            },
            "required": ["action"],
        },
    }


def builtin_overlay():
    entry = next(b["entry"] for b in config.BUILTIN_SERVERS
                 if b["entry"]["name"] == "computer")
    return entry["tool_schema"]["computer"]


def test_action_values_are_published():
    _, schema = _overlay(realhands_tool(), builtin_overlay())
    values = schema["properties"]["action"].get("enum")
    check("action carries an enum", isinstance(values, list), True)
    for action in ("key", "mouse_move", "screenshot", "hold_key", "type"):
        check(f"enum has {action}", action in values, True)
    # The three an agent has actually guessed and been given a ValueError for.
    for guess in ("press_key", "move", "click"):
        check(f"enum excludes the guess {guess}", guess in values, False)
    check("type survives alongside the enum",
          schema["properties"]["action"].get("type"), "string")


def test_the_duration_trap_is_stated():
    described, _ = _overlay(realhands_tool(), builtin_overlay())
    check("original description kept",
          described.startswith("Control the computer."), True)
    check("hold_key named in the note", "hold_key" in described, True)


def test_the_tool_is_not_mutated():
    tool = realhands_tool()
    _overlay(tool, builtin_overlay())
    check("server's own tool dict untouched",
          tool["inputSchema"]["properties"]["action"], {"type": "string"})
    check("server's own description untouched",
          tool["description"], "Control the computer.")


def test_a_renamed_parameter_is_left_alone():
    """A future realhands that renames `action` must not have one invented
    for it."""
    tool = realhands_tool()
    tool["inputSchema"]["properties"] = {"operation": {"type": "string"}}
    described, schema = _overlay(tool, builtin_overlay())
    check("no parameter fabricated",
          list(schema["properties"]), ["operation"])
    check("no note about a parameter that is gone",
          described, "Control the computer.")


def test_a_schemaless_tool_is_left_alone():
    tool = {"name": "computer", "description": "d", "inputSchema": {"type": "object"}}
    described, schema = _overlay(tool, builtin_overlay())
    check("schema without properties passes through", schema, {"type": "object"})
    check("description without properties passes through", described, "d")


def test_other_servers_are_published_verbatim():
    described, schema = _overlay(realhands_tool(), None)
    check("no overlay, no enum", "enum" in schema["properties"]["action"], False)
    check("no overlay, no note", described, "Control the computer.")


def test_pool_carries_the_overlay_off_the_entry():
    pool = Pool([
        {"name": "computer", "command": "x", "enabled": True,
         "tool_schema": {"computer": {"properties": {"action": {"enum": ["key"]}}}}},
        {"name": "other", "command": "y", "enabled": True},
        {"name": "off", "command": "z", "enabled": False,
         "tool_schema": {"computer": {}}},
    ])
    check("overlay kept for the server that declared one",
          "computer" in pool.overlays, True)
    check("none invented for a server without one",
          "other" in pool.overlays, False)
    check("a disabled server is not loaded at all",
          "off" in pool.overlays, False)


def test_builtin_entry_still_matches_the_overlay():
    """The overlay keys onto the tool by name; a rename of either end here
    would silently publish nothing."""
    entry = next(b["entry"] for b in config.BUILTIN_SERVERS
                 if b["entry"]["name"] == "computer")
    check("overlay is keyed by a tool name", list(entry["tool_schema"]), ["computer"])
    check("overlay targets action",
          list(entry["tool_schema"]["computer"]["properties"]), ["action"])


for fn in list(globals().values()):
    if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
        print(fn.__name__)
        fn()

print()
if FAILURES:
    print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
    sys.exit(1)
print("all green")
