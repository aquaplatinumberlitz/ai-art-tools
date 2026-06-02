# Deployment

## Fresh Install

```bash
bash deploy/install.sh
```

The installer clones or updates the repository, creates a Python virtual environment, installs `requirements.txt`, installs Playwright Chromium, creates Hermes directories, generates `$HERMES_HOME/.env`, installs the cron job, and runs syntax checks.

Set these variables when needed:

```bash
HERMES_HOME=/home/ubuntu/.hermes \
HERMES_REPO_DIR=/tmp/ai-art-tools \
bash deploy/install.sh
```

Use `HERMES_DRY_RUN=1` to preview actions. Use `HERMES_INTERACTIVE=1` to prompt for missing required credentials. Use `HERMES_RUN_ONCE=1` to run the pipeline once after installation.

## Auth File Standardization

All private runtime auth/token/state files are standardized under `/home/ubuntu/.hermes/references/`:

| File | Source | Purpose |
|------|--------|---------|
| `pixiv_token.json` | Pixiv OAuth | OAuth refresh token with `{"refresh_token": "..."}` |
| `pixai_state.json` | PixAI Playwright | Saved browser storage state after login |
| `seaart_state.json` | SeaArt Playwright | Saved browser storage state after login |

These files are set to `chmod 600` and their parent directories to `chmod 700` for security. The corresponding `.env` variables (`PIXIV_TOKEN_FILE`, `PIXAI_STATE_FILE`, `SEAART_STATE_FILE`) point to these canonical paths.

## Account File Discovery

The installer reuses the first account markdown file it finds in this order:

1. `$HERMES_ACCOUNTS_FILE`
2. `$HERMES_HOME/accounts.md`
3. `$HERMES_HOME/accounts/accounts.md`
4. `$HERMES_HOME/references/accounts.md`
5. `$HOME/.hermes/accounts.md`
6. `$HOME/.hermes/references/accounts.md`

The parser accepts `KEY=value` lines anywhere in markdown, including fenced `env` blocks.

## Supported Keys

Required:

- `PIXAI_EMAIL`
- `PIXAI_PASSWORD`

Optional:

- `CIVITAI_API_TOKEN`
- `PIXIV_TOKEN_FILE`
- `PIXAI_STATE_FILE`

`PIXIV_TOKEN_FILE` points to an existing Pixiv OAuth token JSON file. `PIXAI_STATE_FILE` points to an existing PixAI Playwright storage state file. The installer writes both paths into `.env` and also copies the files to the legacy default locations when they exist.

## `.env` Generation

The installer writes `$HERMES_HOME/.env`.

- If `.env` does not exist, it copies `.env.example` and fills known values from the account file.
- If `.env` exists, it creates a timestamped backup named `.env.bak.<timestamp>`, keeps existing non-empty credential values, and fills or appends missing values.
- On a fresh `.env`, deployment paths are adjusted to use `$HERMES_HOME/cron` and `$HERMES_HOME/cron/images`.
- On every install, `HERMES_SCRIPT_DIR` is set to `$HERMES_REPO_DIR/scripts` so cron runs the scripts from the repository checkout.
- Secret values are never printed. The installer reports only `found` or `missing` for each supported key.
- The installer applies `chmod 600` to `.env` and discovered account files.

You can edit `$HERMES_HOME/.env` manually after install. Keep one `KEY=value` assignment per line.

## Manual Pipeline Run

```bash
cd "$HERMES_REPO_DIR"
set -a
. "$HERMES_HOME/.env"
set +a
bash scripts/daily_report_pipeline.sh
```

The pipeline also derives `HERMES_SCRIPT_DIR` from `HERMES_REPO_DIR` at startup, validates that all required source scripts exist under `scripts/sources/` and `scripts/report/`, and exits before fetching if any required script is missing.

## Logs

Cron output is appended to:

```bash
$HERMES_HOME/logs/pipeline.log
```

## Cron

The installer writes a marked crontab block:

```text
# BEGIN HERMES AI ART TOOLS
15 22 * * * cd "$HERMES_REPO_DIR" && set -a && . "$HERMES_HOME/.env" && set +a && bash scripts/daily_report_pipeline.sh >> "$HERMES_HOME/logs/pipeline.log" 2>&1
# END HERMES AI ART TOOLS
```

To remove cron, run `crontab -e` and delete that whole block.

## SeaArt Filter Quality

SeaArt runs from `scripts/sources/seaart_trending.py` in the repo checkout. The fetcher records a fingerprint of the first feed card URLs/titles before selecting filters, then waits for the fingerprint to change after Hot/Week selection. If it changes, source status is marked `quality=hot_week`; otherwise the report keeps the JSON but marks `quality=default_feed` with a warning.

## Update Repo

```bash
cd "$HERMES_REPO_DIR"
git pull
```

You can also rerun `bash deploy/install.sh` to update dependencies, refresh `.env` from account files, and reinstall the cron block.

## Security

Never commit `.env`, account markdown files, Pixiv token JSON, PixAI state JSON, or logs. Keep `.env` and account files at `chmod 600`. The installer does not print secret values, but shell history can still capture commands if you paste secrets directly into a terminal.
