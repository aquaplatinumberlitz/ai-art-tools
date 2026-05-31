#!/usr/bin/env python3
"""Fetch Danbooru daily trending popular posts via API (iOS app UA bypasses Cloudflare)."""
import json, sys, requests
from datetime import datetime, timezone

UA = "Danbooru/1.0 (iPhone; iOS 17.0; Scale/3.00)"
BASE = "https://danbooru.donmai.us"

def fetch_popular(scale='day', count=5, date=None):
    if not date:
        date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    url = f"{BASE}/explore/posts/popular.json?date={date}&scale={scale}"
    try:
        r = requests.get(url, headers={"User-Agent": UA}, timeout=15)
    except Exception as e:
        print(f"⚠️ Error: {e}", file=sys.stderr)
        return []
    if r.status_code != 200:
        return []
    try:
        data = r.json()
    except Exception as e:
        print(f"⚠️ Error: {e}", file=sys.stderr)
        return []
    if not data:
        # Fallback to yesterday if today empty (early UTC hours)
        d = datetime.strptime(date, "%Y-%m-%d") - __import__('datetime').timedelta(days=1)
        fallback_url = f"{BASE}/explore/posts/popular.json?date={d.strftime('%Y-%m-%d')}&scale={scale}"
        try:
            r2 = requests.get(fallback_url, headers={"User-Agent": UA}, timeout=15)
        except Exception as e:
            print(f"⚠️ Error: {e}", file=sys.stderr)
            return []
        if r2.status_code == 200:
            try:
                data = r2.json()
            except Exception as e:
                print(f"⚠️ Error: {e}", file=sys.stderr)
                return []
    return data[:count]

def format_post(p):
    img_url = p.get('file_url', '') or p.get('large_file_url', '')  # full original
    # Skip video posts
    if img_url and any(img_url.endswith(ext) for ext in ('.mp4', '.webm', '.gif')):
        return None
    return {
        'id': p.get('id'),
        'score': p.get('score', 0),
        'rating': p.get('rating', '?'),
        'tags': p.get('tag_string', '')[:300],
        'artist': p.get('tag_string_artist', ''),
        'copyright': p.get('tag_string_copyright', ''),
        'character': p.get('tag_string_character', ''),
        'image_url': img_url,
        'large_url': p.get('file_url', ''),
        'preview_url': p.get('preview_file_url', ''),
        'url': f"{BASE}/posts/{p.get('id')}",
    }

def main():
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    scale = sys.argv[2] if len(sys.argv) > 2 else 'day'
    posts = fetch_popular(scale, count * 2)  # Fetch extra to account for video skips
    formatted = []
    for post in posts:
        formatted_post = format_post(post)
        if formatted_post is not None:
            formatted.append(formatted_post)
    print(json.dumps(formatted[:count], ensure_ascii=False, indent=2))

if __name__ == "__main__":
    main()
