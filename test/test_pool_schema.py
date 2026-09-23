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
    """The `computer` tool exactly as realhands 0.3.2 publishes it, copied
    from what a paired desktop actually sent.

    The shape matters and a simplified stand-in hides the bug: `action` is not
    a plain string but a FastMCP `anyOf` of string-or-null, and `steps` is an
    array of bare objects with no element schema. An overlay written against a
    tidier imaginary schema passes its tests and changes nothing in
    production."""
    return {
        "name": "computer",
        "description": "Control the real computer.\n\nBATCH WHENEVER YOU CAN.",
        "inputSchema": {
            "type": "object",
            "title": "computerArguments",
            "properties": {
                "action": {"anyOf": [{"type": "string"}, {"type": "null"}],
                           "default": None, "title": "Action"},
                "coordinate": {"anyOf": [{"items": {"type": "integer"},
                                          "type": "array"}, {"type": "null"}],
                               "default": None, "title": "Coordinate"},
                "text": {"anyOf": [{"type": "string"}, {"type": "null"}],
                         "default": None, "title": "Text"},
                "duration": {"anyOf": [{"type": "number"}, {"type": "null"}],
                             "default": None, "title": "Duration"},
                "steps": {"anyOf": [{"items": {"additionalProperties": True,
                                               "type": "object"},
                                     "type": "array"}, {"type": "null"}],
                          "default": None, "title": "Steps"},
                "screenshot": {"default": True, "type": "boolean"},
            },
        },
    }


def builtin_overlay():
    entry = next(b["entry"] for b in config.BUILTIN_SERVERS
                 if b["entry"]["name"] == "computer")
    return entry["tool_schema"]["computer"]


def test_action_values_are_published():
    _, schema = _overlay(realhands_tool(), builtin_overlay())
    action = schema["properties"]["action"]
    # On the string branch, not beside the anyOf, which is where a reader of
    # the schema would not look for it.
    branch = next((b for b in action["anyOf"] if b.get("type") == "string"), None)
    check("enum sits on the string branch", isinstance(branch.get("enum"), list), True)
    check("nothing added beside the anyOf", "enum" in action, False)
    check("null branch survives",
          any(b.get("type") == "null" for b in action["anyOf"]), True)
    values = branch["enum"]
    for act in ("key", "mouse_move", "screenshot", "hold_key", "type"):
        check(f"enum has {act}", act in values, True)
    # Handled before the dispatch, and the only action realhands' own
    # description tells the model to call by name.
    for act in ("stop", "done", "release"):
        check(f"enum has the stand-down action {act}", act in values, True)
    # Every name an agent has actually guessed and been given a ValueError for.
    for guess in ("press_key", "press", "keypress", "move", "click"):
        check(f"enum excludes the guess {guess}", guess in values, False)


def test_batched_steps_are_constrained_too():
    """The failure that started this: `press` inside `steps`, where an enum on
    the outer `action` does nothing."""
    _, schema = _overlay(realhands_tool(), builtin_overlay())
    steps = schema["properties"]["steps"]
    array = next(b for b in steps["anyOf"] if b.get("type") == "array")
    inner = array["items"].get("properties") or {}
    check("steps elements gained an action", "action" in inner, True)
    values = (inner.get("action") or {}).get("enum") or []
    check("step action enum matches the outer one",
          sorted(values), sorted(config.COMPUTER_ACTIONS))
    check("step enum excludes press", "press" in values, False)
    check("elements stay open to other keys",
          array["items"].get("additionalProperties"), True)


def test_the_note_survives_the_relay_truncation():
    """The relay slices a description at 1024 characters. realhands' own text
    already overruns that, so an appended note is cut off before any model
    sees it -- which is what happened the first time this was fixed."""
    tool = realhands_tool()
    tool["description"] = "x" * 4000
    described, _ = _overlay(tool, builtin_overlay())
    sliced = described[:1024]
    check("note is at the front", described.startswith("Key presses"), True)
    check("note survives a 1024 slice", "keypress" in sliced, True)
    check("original description still follows", "x" * 100 in described, True)


def test_the_parameter_traps_are_stated():
    described, _ = _overlay(realhands_tool(), builtin_overlay())
    for phrase in ("keypress", "hold_key", "coordinate", "text"):
        check(f"note mentions {phrase}", phrase in described, True)
    check("realhands' own description kept",
          "BATCH WHENEVER YOU CAN" in described, True)


def test_the_tool_is_not_mutated():
    tool = realhands_tool()
    _overlay(tool, builtin_overlay())
    check("server's own tool dict untouched",
          tool["inputSchema"]["properties"]["action"].get("enum"), None)
    check("server's own steps untouched",
          "properties" in tool["inputSchema"]["properties"]["steps"]["anyOf"][0]["items"],
          False)
    check("server's own description untouched",
          tool["description"].startswith("Control the real computer."), True)


def test_a_renamed_parameter_is_left_alone():
    """A future realhands that renames `action` must not have one invented
    for it."""
    tool = realhands_tool()
    tool["inputSchema"]["properties"] = {"operation": {"type": "string"}}
    described, schema = _overlay(tool, builtin_overlay())
    check("no parameter fabricated",
          list(schema["properties"]), ["operation"])
    check("no note about a parameter that is gone",
          described.startswith("Control the real computer."), True)


def test_a_schemaless_tool_is_left_alone():
    tool = {"name": "computer", "description": "d", "inputSchema": {"type": "object"}}
    described, schema = _overlay(tool, builtin_overlay())
    check("schema without properties passes through", schema, {"type": "object"})
    check("description without properties passes through", described, "d")


def test_other_servers_are_published_verbatim():
    described, schema = _overlay(realhands_tool(), None)
    check("no overlay, no enum",
          "enum" in schema["properties"]["action"]["anyOf"][0], False)
    check("no overlay, no note",
          described.startswith("Control the real computer."), True)


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
    check("overlay also targets steps",
          list(entry["tool_schema"]["computer"]["item_properties"]), ["steps"])


for fn in list(globals().values()):
    if callable(fn) and getattr(fn, "__name__", "").startswith("test_"):
        print(fn.__name__)
        fn()

print()
if FAILURES:
    print(f"{len(FAILURES)} failed: {', '.join(FAILURES)}")
    sys.exit(1)
print("all green")
