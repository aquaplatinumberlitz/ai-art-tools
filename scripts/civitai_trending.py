#!/usr/bin/env python3
"""Fetch CivitAI trending images via REST API.

Usage:
  python3 civitai_trending.py [count]

Output: JSON array of formatted items to stdout.
  Each item: {id, url, username, stats, nsfw, baseModel}

Defaults to 10 items, max 30.
"""
import json, sys, time
import requests

API_URL = "https://civitai.com/api/v1/images"
TIMEOUT = 30
MAX_RETRIES = 3


def fetch_images(limit=10):
    """Fetch trending images from CivitAI with retry logic."""
    params = {
        "limit": limit * 5,
        "sort": "Most Reactions",
        "period": "Day",
        "browsingLevel": 31,
    }

    last_error = None
    for attempt in range(MAX_RETRIES):
        try:
            r = requests.get(API_URL, params=params, timeout=TIMEOUT)

            if r.status_code in (429, 500, 502, 503, 504):
                if attempt < MAX_RETRIES - 1:
                    retry_after = r.headers.get("Retry-After")
                    wait = int(retry_after) if retry_after and retry_after.isdigit() else (attempt + 1) * 2
                    print(f"⚠️ CivitAI {r.status_code}, retrying in {wait}s...", file=sys.stderr)
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

            if not isinstance(data, dict):
                last_error = "API response is not an object"
                break

            items = data.get('items', [])
            if not isinstance(items, list):
                last_error = "API response 'items' is not a list"
                break
            return items

        except requests.RequestException as e:
            last_error = str(e)
            if attempt < MAX_RETRIES - 1:
                wait = (attempt + 1) * 2
                print(f"⚠️ CivitAI error: {e}, retrying in {wait}s...", file=sys.stderr)
                time.sleep(wait)
                continue
            break

    print(f"ERROR: {last_error or 'Unknown error'}", file=sys.stderr)
    sys.exit(1)


def format_item(item):
    """Format a raw API item into a clean output dict."""
    if not isinstance(item, dict):
        return None

    stats = item.get('stats', {})
    if not isinstance(stats, dict):
        stats = {}

    url = item.get('url') or ''
    if not isinstance(url, str):
        url = ''

    return {
        'id': item.get('id'),
        'url': url,
        'username': item.get('username', '?'),
        'stats': {
            'likeCount': stats.get('likeCount', 0),
            'heartCount': stats.get('heartCount', 0),
        },
        'nsfw': item.get('nsfw', False),
        'baseModel': item.get('baseModel', ''),
    }


def main():
    try:
        count = int(sys.argv[1]) if len(sys.argv) > 1 else 10
        count = max(1, min(count, 30))
    except ValueError:
        count = 10

    raw_items = fetch_images(count)
    formatted = []
    for item in raw_items:
        if not isinstance(item, dict):
            continue
        url = item.get('url') or ''
        if not isinstance(url, str) or url.endswith('.mp4') or url.endswith('.webm'):
            continue
        fmt = format_item(item)
        if fmt:
            formatted.append(fmt)
            if len(formatted) >= count:
                break

    print(json.dumps(formatted, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
