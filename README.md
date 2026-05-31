# AI Art Tools

Daily AI art report build system — fetches trending art from multiple sources and generates an HTML report.

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

See individual scripts for dependencies (pixivpy3, playwright, etc.).

## Configuration

- `references/accounts.md` — API keys, tokens, endpoints
- `.pixiv_token.json` — Pixiv OAuth refresh token (not in repo)
