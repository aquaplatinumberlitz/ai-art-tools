#!/usr/bin/env python3
"""Fetch HuggingFace trending text-to-image models.

Usage:
  python3 hf_models_trending.py [count]

Output: JSON array of formatted models to stdout.
  Each item: {modelId, downloads, likes, pipeline_tag, lastModified}

Defaults to 8 items, max 20.
"""
import json, sys, time
import requests

API_URL = "https://huggingface.co/api/models"
TIMEOUT = 30
MAX_RETRIES = 3


def fetch_models(limit=8):
    """Fetch trending text-to-image models from HuggingFace with retry."""
    params = {
        "pipeline_tag": "text-to-image",
        "sort": "trendingScore",
        "direction": "-1",
        "limit": limit,
        "full": "true",
    }

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(API_URL, params=params, timeout=TIMEOUT)

            if r.status_code in (429, 500, 502, 503, 504):
                if attempt < MAX_RETRIES - 1:
                    retry_after = r.headers.get("Retry-After")
                    wait = int(retry_after) if retry_after and retry_after.isdigit() else (attempt + 1) * 2
                    print(f"⚠️ HuggingFace {r.status_code}, retrying in {wait}s...", file=sys.stderr)
                    time.sleep(wait)
                    continue
                else:
                    last_error = f"HTTP {r.status_code}"
                    break

            r.raise_for_status()

            try:
                data = r.json()
            except ValueError as e:
                last_error = f"Invalid JSON response: {e}"
                break

            if not isinstance(data, list):
                last_error = "API response is not a list"
                break
            return data

        except requests.RequestException as e:
            last_error = str(e)
            if attempt < MAX_RETRIES - 1:
                wait = (attempt + 1) * 2
                print(f"⚠️ HuggingFace error: {e}, retrying in {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            break

    print(f"ERROR: {last_error or 'Unknown error'}", file=sys.stderr)
    sys.exit(1)


def main():
    try:
        count = int(sys.argv[1]) if len(sys.argv) > 1 else 8
        count = max(1, min(count, 20))
    except ValueError:
        count = 8

    models = fetch_models(count)
    formatted = []
    for m in models[:count]:
        if not isinstance(m, dict):
            continue
        formatted.append({
            'modelId': m.get('modelId', ''),
            'downloads': m.get('downloads', 0),
            'likes': m.get('likes', 0),
            'pipeline_tag': m.get('pipeline_tag', ''),
            'lastModified': m.get('lastModified', None),
        })

    print(json.dumps(formatted, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
