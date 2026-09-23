#!/usr/bin/env python3
"""Checks the game-input shim against the pyautogui that is actually installed.

    python3 test/test_gameinput.py

The first version of this test invented a pyautogui with a `keyboardMapping`
attribute on the package. The real library keeps that table on the *platform*
module, so the test passed and the shim raised AttributeError on the first
real keypress. Everything the shim relies on from pyautogui is therefore
asserted against pyautogui's own source here, not against a convenient
imitation — that contract is the whole reason this file exists.

Skips where pyautogui is not installed (the machine this was written on),
since there would be nothing to check against. On a Mark 6 install it is in
the private runtime, so `runtime\\python.exe test\\test_gameinput.py` runs it
for real.
"""
import ast
import importlib.util
import os
import sys
import types

sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "src"))

from mark6 import gameinput as gi                        # noqa: E402

results = []


def check(name, condition, detail=""):
    results.append((name, bool(condition)))
    print(("  PASS  " if condition else "  FAIL  ") + name + (f"  [{detail}]" if detail else ""))


def pyautogui_sources():
    """(__init__.py, platform module) source text for the installed pyautogui."""
    spec = importlib.util.find_spec("pyautogui")
    if spec is None or not spec.origin:
        return None, None
    pkg = os.path.dirname(spec.origin)
    platform_file = {"nt": "_pyautogui_win.py", "posix": "_pyautogui_x11.py"}.get(os.name)
    if sys.platform == "darwin":
        platform_file = "_pyautogui_osx.py"
    path = os.path.join(pkg, platform_file or "")
    if not os.path.isfile(path):
        return open(spec.origin, encoding="utf-8").read(), None
    return (open(spec.origin, encoding="utf-8").read(),
            open(path, encoding="utf-8").read())


def real_mapping(platform_src):
    """pyautogui's own keyboardMapping literal, straight out of its source."""
    for node in ast.walk(ast.parse(platform_src)):
        if (isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and node.func.attr == "update"
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "keyboardMapping"
                and node.args and isinstance(node.args[0], ast.Dict)):
            return ast.literal_eval(node.args[0])
    return {}


def main():
    init_src, platform_src = pyautogui_sources()

    if init_src is None:
        print("pyautogui is not installed here — skipping the source contract "
              "checks. Run this on an install with the bundled runtime.")
        mapping = {}
    else:
        check("pyautogui reaches its key table as platformModule.keyboardMapping",
              "platformModule.keyboardMapping" in init_src)
        check("...and not as an attribute of the package itself",
              "\\npyautogui.keyboardMapping" not in init_src)
        mapping = real_mapping(platform_src) if platform_src else {}

    # The rows VkKeyScan fills in at import time are not in the literal, so
    # they are added here in the shape it produces: low byte the virtual key,
    # high byte the modifiers the character needs (1 shift, 2 ctrl, 4 alt).
    for ch in "abcdefghijklmnopqrstuvwxyz":
        mapping.setdefault(ch, ord(ch.upper()))
    for ch, vk in zip("1234567890", range(0x31, 0x3B)):
        mapping.setdefault(ch, vk)
    mapping.setdefault("!", 0x100 + 0x31)
    mapping.setdefault("@", 0x600 + 0x32)      # reached through AltGr on some layouts
    # The named keys come out of the literal on a machine that has pyautogui;
    # seeded here as well so the behaviour below is checked either way, with
    # the values pyautogui's own table uses.
    for name, vk in (("ctrl", 0x11), ("shift", 0x10), ("alt", 0x12),
                     ("space", 0x20), ("enter", 0x0D), ("esc", 0x1B),
                     ("tab", 0x09), ("up", 0x26)):
        mapping.setdefault(name, vk)

    fake = types.SimpleNamespace(
        PAUSE=0, position=lambda: (0, 0), failSafeCheck=lambda: None,
        platformModule=types.SimpleNamespace(keyboardMapping=mapping))

    check("the shim finds the table", len(gi._keyboard_mapping(fake)) > 40,
          f"{len(gi._keyboard_mapping(fake))} entries")
    check("and degrades to {} rather than raising if it ever moves",
          gi._keyboard_mapping(types.SimpleNamespace()) == {})

    check("'w' maps to VK_W", gi._vks_for(fake, "w") == [0x57], str(gi._vks_for(fake, "w")))
    check("'ctrl+s' maps to ctrl then s", gi._vks_for(fake, "ctrl+s") == [0x11, 0x53])
    check("'!' carries the shift the character needs",
          gi._vks_for(fake, "!") == [0x10, 0x31], str(gi._vks_for(fake, "!")))
    check("an AltGr character carries alt and ctrl too",
          gi._vks_for(fake, "@") == [0x12, 0x11, 0x32], str(gi._vks_for(fake, "@")))
    check("an unknown key returns nothing, so it falls through to pyautogui",
          gi._vks_for(fake, "definitelynotakey") == [])

    # The event stream, with SendInput itself stubbed out.
    sent = []
    gi._send = lambda *i: sent.append(i) or len(i)
    gi._key_input = lambda vk, up=False: (vk, "up" if up else "down")
    gi._mouse_move_input = lambda dx, dy: ("move", dx, dy)
    stock = types.SimpleNamespace(key=lambda t: "STOCK", hold_key=lambda t, d: "STOCK",
                                  move=lambda x, y: "STOCK")
    r = gi.build_replacements(fake, stock)

    sent.clear(); r["key"]("w")
    check("key('w') is one down and one up",
          [e for b in sent for e in b] == [(0x57, "down"), (0x57, "up")])

    sent.clear(); r["key"]("ctrl+s")
    check("a chord releases in reverse order",
          [e for b in sent for e in b] ==
          [(0x11, "down"), (0x53, "down"), (0x53, "up"), (0x11, "up")])

    sent.clear(); r["hold_key"]("w", 0.01)
    check("hold_key holds and then releases",
          [e for b in sent for e in b] == [(0x57, "down"), (0x57, "up")])

    sent.clear()
    try:
        r["hold_key"]("shift+w", "not a number")
    except Exception:                                    # noqa: BLE001
        pass
    evs = [e for b in sent for e in b]
    check("every key is released even when the hold raises",
          evs and all((vk, "up") in evs for vk in (0x10, 0x57)), str(evs))

    sent.clear(); r["move_by"](150, 0)
    check("move_by sends one relative delta and reads no cursor position",
          [e for b in sent for e in b] == [("move", 150, 0)])

    failed = [n for n, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed")
    if failed:
        print("FAILED: " + "; ".join(failed))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
