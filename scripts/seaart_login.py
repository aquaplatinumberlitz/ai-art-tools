#!/usr/bin/env python3
"""Manual SeaArt login helper — open browser, let user login, save state."""
import json, os, sys, time
from pathlib import Path
from playwright.sync_api import sync_playwright

STATE_FILE = os.environ.get(
    "SEAART_STATE_FILE",
    str(Path.home() / ".hermes" / "references" / "seaart_state.json")
)

def main():
    state_path = Path(STATE_FILE)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    print(f"[SeaArt Login] State file: {state_path}")
    print("[SeaArt Login] Opening browser...")
    print("[SeaArt Login] Please log in manually in the browser window.")
    print("[SeaArt Login] After login is complete (avatar/profile is visible), press Enter.")

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=False, slow_mo=100)
        context = browser.new_context(viewport={"width": 1440, "height": 900})
        page = context.new_page()
        page.goto("https://seaart.ai/post", wait_until="domcontentloaded", timeout=60000)
        page.wait_for_timeout(3000)

        input("[SeaArt Login] Press Enter after login is complete...")

        # Save state
        context.storage_state(path=str(state_path))
        os.chmod(state_path, 0o600)
        print(f"[SeaArt Login] State saved to: {state_path}")

        # Verify
        page.goto("https://seaart.ai/post", wait_until="domcontentloaded", timeout=30000)
        page.wait_for_timeout(3000)

        is_logged_in = page.evaluate("""
            () => {
                const text = document.body.innerText || '';
                const hasLoginBtn = /Đăng nhập|Login|Sign in|Log in/i.test(text);
                const hasProfileAvatar = !!document.querySelector('[class*="avatar"], [class*="Avatar"], [class*="user"], [class*="User"]');
                return !hasLoginBtn || hasProfileAvatar;
            }
        """)

        if is_logged_in:
            print("[SeaArt Login] ✅ Login state verified")
        else:
            print("[SeaArt Login] ⚠️ Login may not have persisted. Try again.")

        browser.close()

if __name__ == "__main__":
    main()
