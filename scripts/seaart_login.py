#!/usr/bin/env python3
"""Deprecated wrapper for scripts/auth/seaart_login.py."""

import runpy
import sys
from pathlib import Path


def main():
    target = Path(__file__).resolve().parent / "auth" / "seaart_login.py"
    print(
        "warning: scripts/seaart_login.py is deprecated; "
        "use scripts/auth/seaart_login.py instead.",
        file=sys.stderr,
    )
    sys.argv[0] = str(target)
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
