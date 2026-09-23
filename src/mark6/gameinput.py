#!/usr/bin/env python3
"""realhands, with input a game can actually see.

The bundled computer-use server drives the desktop through pyautogui, and on
Windows pyautogui injects input in two ways that ordinary applications accept
and games ignore:

  * **The mouse is teleported.** `pyautogui.moveTo` is
    `ctypes.windll.user32.SetCursorPos(x, y)` and nothing else. That moves the
    pointer without generating any motion event, so there is no *delta* to
    read. A game's camera does not read where the cursor is — it grabs the
    cursor, hides it, and reads relative movement frame by frame (Minecraft
    via GLFW's raw mouse motion). There is nothing to read, so the view never
    turns, and the game re-centres the cursor every frame anyway.

  * **Every key carries scan code 0.** pyautogui calls
    `keybd_event(vkCode, 0, flags, 0)` — the second argument is the scan code.
    Desktop applications key off the virtual-key code and are fine. Games
    generally read the scan code: GLFW pulls it out of the window message and
    looks the key up by it, `keycodes[0]` is unknown, and the key is dropped.
    That is why the agent can type into Notepad perfectly and do nothing at
    all in Minecraft.

Both are fixed the same way — `SendInput`, which is the documented modern API
and the one that reaches the raw input stream: mouse movement as a relative
delta with MOUSEEVENTF_MOVE, and keys with KEYEVENTF_SCANCODE and a real scan
code from MapVirtualKey.

This is a **shim, not a fork**. It replaces a handful of functions on
`realhands.input` and then runs `realhands.server` completely unchanged, for
the same reason FreeClaw shims shadow-web rather than patching it: the server
keeps updating from PyPI, and nothing here has to be kept in step with the
rest of it. If the replacement ever stops applying cleanly it says so and runs
the stock server instead of failing to start.

## The absolute-coordinate compromise

`move(x, y)` is handed real screen pixels, because that is what the `computer`
tool's schema speaks. A relative delta is computed from where the pointer is
now, so on the desktop the pointer still lands exactly where it was asked to
and the observable behaviour is unchanged — it simply gets there by a real
movement event instead of a teleport.

In a game with the cursor grabbed and hidden, "where the pointer is now" is
not meaningful, so the delta turns the camera by roughly the distance asked
for rather than aiming at a point. Crude, but it is the most that can be done
without adding an action to somebody else's tool schema. Aim by nudging.

## The brakes are preserved deliberately

pyautogui's fail-safe — slam the pointer into a screen corner and the action
raises — lives *inside* pyautogui's own calls, so bypassing pyautogui would
silently remove a brake the README promises. Every replacement below calls
`pyautogui.failSafeCheck()` first and honours `pyautogui.PAUSE` after, so the
corner abort and the pacing behave exactly as before. The panic hotkey
(ctrl+alt+q) is handled by `keyboard` and is unaffected either way.

Set MARK6_NO_GAMEINPUT=1 to skip the replacement and run the stock server.
"""
import ctypes
import os
import sys
import time
from ctypes import wintypes

# ── SendInput ────────────────────────────────────────────────

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
MOUSEEVENTF_MOVE = 0x0001
KEYEVENTF_EXTENDEDKEY = 0x0001
KEYEVENTF_KEYUP = 0x0002
KEYEVENTF_SCANCODE = 0x0008
MAPVK_VK_TO_VSC_EX = 0x04

# Keys that live on the extended half of the keyboard. Without
# KEYEVENTF_EXTENDEDKEY these arrive as their numpad twins — right control
# reads as left, and the arrow keys as 4/8/6/2.
_EXTENDED_VKS = {
    0x21, 0x22, 0x23, 0x24,        # PageUp PageDown End Home
    0x25, 0x26, 0x27, 0x28,        # Left Up Right Down
    0x2D, 0x2E,                    # Insert Delete
    0x5B, 0x5C, 0x5D,              # LWin RWin Apps
    0xA3,                          # RControl
    0xA5,                          # RMenu (right alt)
    0x6F,                          # Divide
    0x0D,                          # numpad Enter is handled by the EX lookup
}


class _MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", wintypes.LONG), ("dy", wintypes.LONG),
                ("mouseData", wintypes.DWORD), ("dwFlags", wintypes.DWORD),
                ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", wintypes.WORD), ("wScan", wintypes.WORD),
                ("dwFlags", wintypes.DWORD), ("time", wintypes.DWORD),
                ("dwExtraInfo", ctypes.POINTER(ctypes.c_ulong))]


class _HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", wintypes.DWORD), ("wParamL", wintypes.WORD),
                ("wParamH", wintypes.WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", _MOUSEINPUT), ("ki", _KEYBDINPUT), ("hi", _HARDWAREINPUT)]


class _INPUT(ctypes.Structure):
    _fields_ = [("type", wintypes.DWORD), ("u", _INPUTUNION)]


_user32 = None


def _u32():
    """user32, bound once with argtypes and its own last-error slot.

    `use_last_error=True` matters: without it `get_last_error()` reads a value
    some unrelated call may have overwritten, so a failure would be reported
    with a meaningless code. argtypes matter on 64-bit, where letting ctypes
    guess the third argument truncates the struct size and SendInput rejects
    every event."""
    global _user32
    if _user32 is None:
        _user32 = ctypes.WinDLL("user32", use_last_error=True)
        _user32.SendInput.argtypes = (wintypes.UINT, ctypes.c_void_p, ctypes.c_int)
        _user32.SendInput.restype = wintypes.UINT
        _user32.MapVirtualKeyW.argtypes = (wintypes.UINT, wintypes.UINT)
        _user32.MapVirtualKeyW.restype = wintypes.UINT
    return _user32


def _send(*inputs):
    """Hand a batch to SendInput. Returns how many it accepted.

    Sent as one array rather than one call each: SendInput documents that a
    batch cannot be interleaved with input from the real keyboard or mouse,
    which is what keeps a chord from being split by a keystroke of the
    person's own."""
    n = len(inputs)
    array = (_INPUT * n)(*inputs)
    sent = _u32().SendInput(n, ctypes.byref(array), ctypes.sizeof(_INPUT))
    if sent != n:
        raise OSError(f"SendInput sent {sent} of {n} events "
                      f"(error {ctypes.get_last_error()})")
    return sent


def _mouse_move_input(dx, dy):
    return _INPUT(type=INPUT_MOUSE,
                  u=_INPUTUNION(mi=_MOUSEINPUT(int(dx), int(dy), 0,
                                               MOUSEEVENTF_MOVE, 0, None)))


def _key_input(vk, up=False):
    """One key event carrying a real scan code — the part games read."""
    scan = _u32().MapVirtualKeyW(vk, MAPVK_VK_TO_VSC_EX)
    flags = KEYEVENTF_SCANCODE | (KEYEVENTF_KEYUP if up else 0)
    # MAPVK_VK_TO_VSC_EX returns the 0xE0 prefix in the high byte for the
    # extended keys; the flag is what actually carries that over SendInput.
    if (scan >> 8) == 0xE0 or vk in _EXTENDED_VKS:
        flags |= KEYEVENTF_EXTENDEDKEY
    return _INPUT(type=INPUT_KEYBOARD,
                  u=_INPUTUNION(ki=_KEYBDINPUT(0, scan & 0xFF, flags, 0, None)))


# ── the replacements ─────────────────────────────────────────

def build_replacements(pyautogui, stock):
    """The functions that go onto realhands.input, closed over pyautogui.

    Takes the module rather than importing it so this stays testable off
    Windows, and takes `stock` so anything not worth re-implementing (clicks,
    scrolling, typing — all of which already reach games, because pyautogui
    sends those through mouse_event/SendInput rather than SetCursorPos) can
    fall through untouched."""

    def _guard():
        # The corner abort, which would otherwise be lost the moment pyautogui
        # stops being on the path. Raises FailSafeException, exactly as before.
        pyautogui.failSafeCheck()

    def _settle():
        if pyautogui.PAUSE:
            time.sleep(pyautogui.PAUSE)

    def move(x, y):
        """Land the pointer on (x, y) by real movement events.

        Broken into steps rather than one jump: a game applies each delta to
        its camera as it arrives, and a single 900-pixel delta is a snap
        rather than a turn. Each step aims at where it *should* be by now and
        sends the difference from where the pointer actually is, so pointer
        acceleration cannot accumulate drift. On the desktop the end point is
        identical to the old teleport."""
        _guard()
        start_x, start_y = pyautogui.position()
        total_dx, total_dy = x - start_x, y - start_y
        if total_dx or total_dy:
            distance = max(abs(total_dx), abs(total_dy))
            steps = max(1, min(_MOVE_STEPS, distance // 8 or 1))
            for i in range(1, steps + 1):
                want_x = start_x + round(total_dx * i / steps)
                want_y = start_y + round(total_dy * i / steps)
                at_x, at_y = pyautogui.position()
                if (want_x - at_x) or (want_y - at_y):
                    _send(_mouse_move_input(want_x - at_x, want_y - at_y))
                time.sleep(_MOVE_STEP_SLEEP)
            at_x, at_y = pyautogui.position()          # rounding drift
            if (x - at_x) or (y - at_y):
                _send(_mouse_move_input(x - at_x, y - at_y))
        _settle()

    def move_by(dx, dy):
        """Turn the view by a relative amount. What mouselook actually wants —
        no cursor position is consulted, so a grabbed cursor does not matter."""
        _guard()
        _send(_mouse_move_input(dx, dy))
        _settle()

    def key(text):
        """Press a key or chord with real scan codes."""
        _guard()
        vks = _vks_for(pyautogui, text)
        if not vks:
            return stock.key(text)                  # unmappable: let pyautogui try
        _send(*[_key_input(v) for v in vks],
              *[_key_input(v, up=True) for v in reversed(vks)])
        _settle()

    def hold_key(text, duration):
        """Hold a key or chord down — the one a game needs for walking."""
        _guard()
        vks = _vks_for(pyautogui, text)
        if not vks:
            return stock.hold_key(text, duration)
        _send(*[_key_input(v) for v in vks])
        try:
            time.sleep(max(0.0, float(duration)))
        finally:
            # In a finally because a key left down is the worst thing this
            # module can do to somebody's computer.
            _send(*[_key_input(v, up=True) for v in reversed(vks)])
        _settle()

    return {"move": move, "move_by": move_by, "key": key, "hold_key": hold_key}


# How a long move is broken up, so a game sees a turn rather than a snap.
_MOVE_STEPS = 12
_MOVE_STEP_SLEEP = 0.004

_VK_SHIFT = 0x10


def _vks_for(pyautogui, text):
    """Virtual-key codes for 'ctrl+s' / 'Return' / 'w', or [] if any part is
    unknown. realhands has already translated xdotool spellings into
    pyautogui's vocabulary by the time this runs."""
    try:
        from realhands.input import _translate_combo
        names = _translate_combo(text)
    except Exception:                                   # noqa: BLE001
        names = [p.strip().lower() for p in str(text).split("+") if p.strip()]
    vks = []
    for name in names:
        code = pyautogui.keyboardMapping.get(name)
        if code is None:
            return []
        # pyautogui packs "this character needs shift" into the high byte, the
        # same divmod its own _keyDown does. Dropping it would turn '!' into
        # '1' — so the shift is sent as a key of its own instead.
        mods, vk = divmod(code, 0x100)
        if mods & 0x1 and _VK_SHIFT not in vks:
            vks.append(_VK_SHIFT)
        vks.append(vk)
    return vks


# ── applying it, and running the server ──────────────────────

def apply():
    """Replace the input functions on realhands. Returns what it changed."""
    import types

    import pyautogui
    from realhands import input as rh_input

    stock = types.SimpleNamespace(key=rh_input.key, hold_key=rh_input.hold_key,
                                  move=rh_input.move)
    replacements = build_replacements(pyautogui, stock)
    for name, fn in replacements.items():
        setattr(rh_input, name, fn)
    return sorted(replacements)


def main():
    if os.name != "nt":
        print("[mark6] gameinput is Windows-only; running realhands unchanged.",
              file=sys.stderr)
    elif os.environ.get("MARK6_NO_GAMEINPUT"):
        print("[mark6] MARK6_NO_GAMEINPUT set; running realhands unchanged.",
              file=sys.stderr)
    else:
        try:
            changed = apply()
            print(f"[mark6] game input active: replaced {', '.join(changed)} "
                  f"on realhands.input (SendInput + scan codes)", file=sys.stderr)
        except Exception as e:                          # noqa: BLE001
            # Never a reason not to start. A computer-use server that drives
            # the desktop but not games is most of what it was for.
            print(f"[mark6] game input could not be applied ({type(e).__name__}: {e}); "
                  "running realhands unchanged.", file=sys.stderr)

    from realhands.server import main as server_main
    server_main()


if __name__ == "__main__":
    main()
