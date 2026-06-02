# Auth & Cleanup Helper Scripts

These scripts are manual maintenance helpers. They are not part of the daily production fetch pipeline.

## `export_browser_state_from_chrome.py`

Connects to a running local Chrome DevTools Protocol session and exports Playwright `storage_state` files for SeaArt and PixAI. It does not read Chrome's cookie database directly, and refuses non-local CDP hosts unless `--allow-non-local-cdp` is passed.

Use after starting Chrome with `--remote-debugging-port=9222` and logging into target sites manually.

```bash
python3 scripts/auth-clean/export_browser_state_from_chrome.py --site seaart
python3 scripts/auth-clean/export_browser_state_from_chrome.py --site pixai
python3 scripts/auth-clean/export_browser_state_from_chrome.py --all
```

By default, files are written to `~/.hermes/references/` on Linux/Mac.

## `verify_auth_states.py`

Checks that required auth state files (SEAART_STATE_FILE, PIXAI_STATE_FILE, PIXIV_TOKEN_FILE) exist, are valid JSON, and have correct permissions.

```bash
python3 scripts/auth-clean/verify_auth_states.py
```

## `cleanup_images.py`

Cleans up report images older than 7 days. Runs automatically as a weekly cron job.

```bash
python3 scripts/auth-clean/cleanup_images.py
```
