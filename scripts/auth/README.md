# Auth Helper Scripts

These scripts are manual authentication helpers. They are not part of the daily production fetch pipeline.

## `seaart_login.py`

Opens a headed Playwright Chromium browser for a manual SeaArt login, then writes a Playwright `storage_state` file for the SeaArt scraper. By default, the state file is written to `~/.hermes/references/seaart_state.json`; set `SEAART_STATE_FILE` to override the path.

Run it when you want `scripts/seaart_trending.py` to use a logged-in SeaArt session, or when the saved SeaArt session has expired.

```bash
python3 scripts/auth/seaart_login.py
```

## `export_browser_state_from_chrome.py`

Connects to an already running local Chrome DevTools Protocol session and exports allowlisted Playwright `storage_state` files for supported sites. It does not read Chrome's cookie database directly, and it refuses non-local CDP hosts unless `--allow-non-local-cdp` is passed.

Use it after starting Chrome with `--remote-debugging-port=9222` and logging into the target sites manually.

```bash
python3 scripts/auth/export_browser_state_from_chrome.py --site civitai
python3 scripts/auth/export_browser_state_from_chrome.py --all
```

By default, files are written to `~/.hermes/references/` on Linux/Mac. On Windows, when `HERMES_REFERENCES_DIR` is not set, files are written to `%USERPROFILE%/hermes-auth-export/`.
