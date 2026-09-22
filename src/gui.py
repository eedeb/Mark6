#!/usr/bin/env python3
"""The window. A bare `run.bat` opens this with the bundled pythonw.exe;
anyone with their own Python 3.9+ (and tkinter) can run it directly:

    python3 src/gui.py

Like cli.py, this only exists to get `mark6` onto sys.path first. Under
pythonw there is no console to print a crash to, so one is shown in a box.
"""
import os
import sys
import traceback

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

if __name__ == "__main__":
    try:
        from mark6.gui import main                      # noqa: E402
        sys.exit(main())
    except Exception:                                   # noqa: BLE001
        detail = traceback.format_exc()
        try:
            from tkinter import messagebox
            messagebox.showerror("Mark 6", f"Mark 6 hit an error:\n\n{detail}")
        except Exception:                               # noqa: BLE001
            print(detail, file=sys.stderr)
        sys.exit(1)
