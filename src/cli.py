#!/usr/bin/env python3
"""Entry point. run.bat calls this with the bundled interpreter; anyone with
their own Python 3.9+ can call it directly the same way:

    python3 src/cli.py login

This file only exists to get `mark6` onto sys.path before importing it — the
package lives beside this script rather than being installed, so both the
bundled embeddable interpreter (which has no pip) and a plain system Python
can run it with nothing to set up first.
"""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from mark6.cli import main                              # noqa: E402

if __name__ == "__main__":
    sys.exit(main())
