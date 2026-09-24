#!/usr/bin/env python3
"""Entry point for Mark 6's own MCP server (src/mark6_tools). Mark 6 starts
this itself when the "mark6" server is ticked; nobody needs to run it by hand.

Like cli.py, it only exists to put src/ on sys.path first: the embeddable
Python's ._pth fixes sys.path at startup, so `-m mark6_tools.server` would
not find a package that lives beside the app rather than in the runtime.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mark6_tools.server import main                     # noqa: E402

if __name__ == "__main__":
    main()
