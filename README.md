# AI Art Tools

Daily AI art report build system — fetches trending art from multiple sources and generates an HTML report.

## Quick Install

```bash
bash deploy/install.sh
```

The installer will:
1. Clone/sync the repository
2. Install Python dependencies
3. Detect existing Hermes account files for credentials
4. Generate a `.env` configuration
5. Set up the daily cron job

See [docs/DEPLOYMENT.md](docs/DEPLOYMENT.md) for details.

## Data Sources

| Source | Script | API |
|--------|--------|-----|
| SeaArt | `seaart_trending.py` | Playwright (DOM scrape) |
| Pixiv SFW/R18 | `pixiv_app.py` | pixivpy3 (App API) |
| Danbooru | `danbooru_trending.py` | Danbooru API |
| PixAI | `pixai_trending.py` | PixAI GraphQL |
| CivitAI | `civitai_trending.py` | CivitAI v1 API |
| Blue Archive (Pixiv) | `pixiv_search_ba.py` | Pixiv App API |
| Blue Archive (PixAI) | `pixai_ba.py` | PixAI GraphQL |
| HuggingFace Models | `hf_models_trending.py` | HF API |
| Reddit | `reddit_rss.py` | RSS feed |
| AI News | `ai_news_fetch.py` | RSS feeds |

## Usage

### Full pipeline
```bash
bash scripts/daily_report_pipeline.sh
```

### Build report only (after data fetched)
```bash
python3 scripts/build_report_canonical.py
```

### Single source
```bash
python3 scripts/seaart_trending.py > /tmp/hermes_report/seaart.json
```

## Requirements

- Python packages used by the fetchers/report builder: `requests`, `feedparser`, `pixivpy3`, `playwright`, and `Pillow`.
- System commands used by the report builder: `curl` and `ffmpeg`.
- Playwright browsers must be installed on the server for SeaArt and PixAI. The installer handles this automatically, or you can run it manually:
  ```bash
  python3 -m playwright install chromium
  ```
- `pixiv_downloader.py` must exist at the configured server-local path used by `build_report_canonical.py`.

## Configuration

- `deploy/install.sh` — recommended setup path. It creates `$HERMES_HOME/.env`, reuses account markdown files when available, and installs the cron job.
- `scripts/.pixiv_token.json` — Pixiv OAuth refresh token, with a `refresh_token` field, used by `pixiv_app.py` and `pixiv_search_ba.py`.
- `PIXAI_EMAIL` and `PIXAI_PASSWORD` — PixAI login credentials used when the saved Playwright session expires.
- `HERMES_REPO_DIR` — repository root used by the cron pipeline. Defaults to `/tmp/ai-art-tools`.
- `HERMES_SCRIPT_DIR` — derived from `HERMES_REPO_DIR/scripts` by `scripts/daily_report_pipeline.sh`; production scripts run from the repo checkout, not from `~/.hermes/scripts`.
- `/tmp/hermes_report` — JSON data directory used by `scripts/daily_report_pipeline.sh` and `scripts/build_report_canonical.py`.
- `/home/ubuntu/.hermes/cron` — report output directory used by `scripts/build_report_canonical.py`; it must also contain `pixiv_downloader.py`.
- `/home/ubuntu/.hermes/cron/images` — local image cache directory used by `scripts/build_report_canonical.py`.
- `~/.hermes/scripts/.pixai_state.json` — saved PixAI Playwright storage state generated after login.
- `HERMES_BASE_URL` — public URL prefix for the generated report and cached images.

## Environment Variables

The pipeline and report builder can be configured with environment variables for data directories, output paths, public URL, source timeouts, and optional API credentials. See `.env.example` for the supported variables and defaults.

## SeaArt Notes

SeaArt has no stable public API for community trending posts. The fetcher uses Playwright DOM scraping on `seaart.ai/post`.

- The fetcher attempts to open the visible Filter UI and select Hot/Week when configured.
- After selecting filters, it compares the first feed card URLs/titles before and after the filter action. It reports `quality=hot_week` only when that fingerprint changes.
- If the filter UI cannot be applied or the feed fingerprint does not change, it marks `quality=default_feed` and warns that the default feed was used.
- A candidate pool (default 20) is fetched and locally sorted by likes (descending), with views as tie-breaker.
- The final requested count (e.g. 10) is selected from the top-scoring candidates.
- Stale fallback: if a SeaArt fetch fails, the previous valid JSON is preserved and the report footer marks it as stale.

### SeaArt environment variables

| Variable | Default | Description |
|---|---|---|
| `SEAART_SORT` | `hot` | Sort order: `hot`, `new`, `recommended` |
| `SEAART_PERIOD` | `week` | Time range: `week`, `month`, `all` |
| `SEAART_POOL_SIZE` | `20` | Candidate pool size before local sort |

## SeaArt Login (Optional)

By default, the SeaArt scraper runs anonymously. If you want the scraped feed to more closely match your logged-in browser view:

1. Run the login helper:
   ```bash
   python3 scripts/seaart_login.py
   ```
   This opens a headed browser. Log in manually, then press Enter.

2. Uncomment `SEAART_STATE_FILE` in `.env`:
   ```
   SEAART_STATE_FILE=/home/ubuntu/.hermes/references/seaart_state.json
   ```

3. The scraper will now use your session. To refresh expired sessions, rerun step 1.

The state file is private — never commit it.

## Probe / Dev-only Scripts

These scripts are not part of the production pipeline:
- `scripts/seaart_sdk_probe.py` — investigated unofficial SeaArt Python SDK
- `scripts/seaart_crawl4ai_probe.py` — investigated crawl4ai as alternative
