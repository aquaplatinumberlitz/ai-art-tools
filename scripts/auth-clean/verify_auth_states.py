#!/usr/bin/env python3
"""Verify that required auth/token/state files exist and are valid."""

import json
import os
import stat
import sys
from pathlib import Path


AUTH_FILES = [
    ("SEAART_STATE_FILE", Path.home() / ".hermes" / "references" / "seaart_state.json", "SeaArt Playwright state"),
    ("PIXAI_STATE_FILE", Path.home() / ".hermes" / "references" / "pixai_state.json", "PixAI Playwright state"),
    ("PIXIV_TOKEN_FILE", Path.home() / ".hermes" / "references" / "pixiv_token.json", "Pixiv OAuth token"),
]


def check_file(env_var: str, default_path: Path, label: str) -> int:
    """Check a single auth file. Returns 1 if critical issue, 0 otherwise."""
    path_str = os.environ.get(env_var, "")
    path = Path(path_str) if path_str else default_path
    errors = 0

    # Exists?
    exists = path.exists()
    print(f"  {label} ({env_var}): ", end="")

    if not exists:
        print("MISSING")
        return 1

    # Size
    try:
        size = path.stat().st_size
        print(f"found ({size} bytes, ", end="")
    except OSError:
        print(f"found (size unavailable, ", end="")
        errors += 1
        size = -1

    # Permissions
    try:
        mode = path.stat().st_mode
        perm_str = stat.filemode(mode)
        print(f"perms {perm_str}, ", end="")
        if mode & (stat.S_IRWXG | stat.S_IRWXO):
            print("WARNING: world-readable, ", end="")
            errors += 1
    except OSError:
        print("perms unavailable, ", end="")
        errors += 1

    # Valid JSON?
    if env_var in ("SEAART_STATE_FILE", "PIXAI_STATE_FILE") or env_var == "PIXIV_TOKEN_FILE":
        try:
            with open(path, "r", encoding="utf-8") as f:
                data = json.load(f)
            if not isinstance(data, dict):
                print("INVALID (not a JSON object)", end="")
                errors += 1
            elif env_var == "PIXIV_TOKEN_FILE" and "refresh_token" not in data:
                print("INVALID (missing refresh_token)", end="")
                errors += 1
            else:
                print("valid JSON", end="")
        except (json.JSONDecodeError, UnicodeDecodeError, OSError) as e:
            print(f"INVALID JSON ({e})", end="")
            errors += 1
    else:
        print("(no validation)", end="")

    print("")
    return 1 if errors > 0 else 0


def main() -> int:
    print("Auth State File Verification")
    print("=" * 50)
    total_issues = 0
    for env_var, default_path, label in AUTH_FILES:
        total_issues += check_file(env_var, default_path, label)

    print("")
    if total_issues == 0:
        print("✅ All auth files OK")
        return 0
    else:
        print(f"⚠️  {total_issues} file(s) have issues")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
