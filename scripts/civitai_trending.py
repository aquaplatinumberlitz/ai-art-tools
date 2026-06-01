#!/usr/bin/env python3
"""Fetch CivitAI trending images via REST API.

Usage:
  python3 civitai_trending.py [count]

Output: JSON array of formatted items to stdout.
  Each item: {id, url, username, stats, nsfw, baseModel}

Defaults to 10 items, max 30.
"""
import json, os, sys, time
import requests

API_URL = "https://civitai.com/api/v1/images"
TIMEOUT = 30
MAX_RETRIES = 3
HYDRATE_MODE = os.environ.get("CIVITAI_HYDRATE_STATS", "auto")
MAX_HYDRATE = int(os.environ.get("CIVITAI_MAX_HYDRATE", "20"))


def reaction_score(stats):
    if not isinstance(stats, dict):
        return 0
    return (stats.get("likeCount") or 0) + (stats.get("heartCount") or 0)


def should_hydrate_stats(items, mode):
    if mode == "always":
        return True
    if mode == "never":
        return False
    # auto: hydrate only if ALL selected items have 0 reaction score
    if not items:
        return False
    nonzero = sum(1 for item in items if reaction_score(item.get("stats", {})) > 0)
    return nonzero == 0


def hydrate_stats(image_id):
    try:
        headers = {}
        token = os.environ.get("CIVITAI_API_TOKEN", "")
        if token:
            headers["Authorization"] = f"Bearer {token}"
        r = requests.get(
            API_URL,
            params={"imageId": image_id, "browsingLevel": 31},
            headers=headers,
            timeout=15,
        )
        if r.status_code == 200:
            data = r.json()
            items = data.get("items", [])
            if items and isinstance(items[0], dict):
                return items[0].get("stats", {})
        elif r.status_code == 429:
            retry_after = r.headers.get("Retry-After")
            wait = int(retry_after) if retry_after and retry_after.isdigit() else 5
            print(f"⚠️ CivitAI hydrate 429, waiting {wait}s", file=sys.stderr)
            time.sleep(wait)
            # one retry
            r2 = requests.get(
                API_URL,
                params={"imageId": image_id, "browsingLevel": 31},
                headers=headers,
                timeout=15,
            )
            if r2.status_code == 200:
                data = r2.json()
                items = data.get("items", [])
                if items and isinstance(items[0], dict):
                    return items[0].get("stats", {})
    except Exception as e:
        print(f"⚠️ Hydrate failed for {image_id}: {e}", file=sys.stderr)
    return {}


def merge_hydrated_stats(item, hydrated_stats):
    merged = dict(item)
    if hydrated_stats:
        merged["stats"] = hydrated_stats
    return merged


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
            'cryCount': stats.get('cryCount', 0),
            'laughCount': stats.get('laughCount', 0),
            'likeCount': stats.get('likeCount', 0),
            'heartCount': stats.get('heartCount', 0),
            'commentCount': stats.get('commentCount', 0),
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

    # Conditional hydration
    mode = HYDRATE_MODE
    nonzero_before = sum(1 for item in formatted if reaction_score(item.get("stats", {})) > 0)

    if should_hydrate_stats(formatted, mode):
        max_h = min(MAX_HYDRATE, len(formatted))
        hydrated_count = 0
        for i in range(max_h):
            image_id = formatted[i].get("id")
            if not image_id:
                continue
            hs = hydrate_stats(image_id)
            if hs:
                formatted[i] = merge_hydrated_stats(formatted[i], hs)
                hydrated_count += 1

        nonzero_after = sum(1 for item in formatted if reaction_score(item.get("stats", {})) > 0)
        print(f"[CivitAI] stats_source=hydrate selected={len(formatted)} nonzero_before={nonzero_before} nonzero_after={nonzero_after} hydrated={hydrated_count}", file=sys.stderr)

        if nonzero_after == 0:
            print(f"[CivitAI] API stats unavailable: selected={len(formatted)} nonzero_before={nonzero_before} nonzero_after={nonzero_after} hydrated={hydrated_count}", file=sys.stderr)
    else:
        print(f"[CivitAI] stats_source=list selected={len(formatted)} nonzero={nonzero_before} hydrated=0", file=sys.stderr)

    print(json.dumps(formatted, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
