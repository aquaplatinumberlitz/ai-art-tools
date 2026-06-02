# Auth Helper Scripts

These scripts are manual authentication helpers. They are not part of the daily production fetch pipeline.

## `export_browser_state_from_chrome.py`

Connects to an already running local Chrome DevTools Protocol session and exports Playwright `storage_state` files for SeaArt and PixAI. It does not read Chrome's cookie database directly, and it refuses non-local CDP hosts unless `--allow-non-local-cdp` is passed.

Use it after starting Chrome with `--remote-debugging-port=9222` and logging into the target sites manually.

```bash
python3 scripts/auth/export_browser_state_from_chrome.py --site seaart
python3 scripts/auth/export_browser_state_from_chrome.py --site pixai
python3 scripts/auth/export_browser_state_from_chrome.py --all
```

By default, files are written to `~/.hermes/references/` on Linux/Mac.

## `verify_auth_states.py`

Checks that the required auth state files (SEAART_STATE_FILE, PIXAI_STATE_FILE, PIXIV_TOKEN_FILE) exist, are valid JSON, and have correct permissions. Run before the pipeline to ensure credentials are in place.

```bash
python3 scripts/auth/verify_auth_states.py
```
