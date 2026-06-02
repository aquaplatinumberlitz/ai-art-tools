#!/usr/bin/env python3
"""Deprecated wrapper for scripts/auth/export_browser_state_from_chrome.py."""

import runpy
import sys
from pathlib import Path


def main():
    target = Path(__file__).resolve().parent / "auth" / "export_browser_state_from_chrome.py"
    print(
        "warning: scripts/export_browser_state_from_chrome.py is deprecated; "
        "use scripts/auth/export_browser_state_from_chrome.py instead.",
        file=sys.stderr,
    )
    sys.argv[0] = str(target)
    runpy.run_path(str(target), run_name="__main__")


if __name__ == "__main__":
    main()
