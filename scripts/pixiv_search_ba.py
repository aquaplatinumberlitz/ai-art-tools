#!/usr/bin/env python3
"""Search Pixiv for Blue Archive fanart — hottest today via pixivpy3 App API.

Uses the same auth pattern as pixiv_app.py (refresh token).
Sorts by popularity, filters to items from today/yesterday.
"""
import json, os, sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from pixivpy3 import AppPixivAPI

TOKEN_FILE = Path(os.path.expanduser(os.environ.get(
    'PIXIV_TOKEN_FILE',
    str(Path(__file__).with_name('.pixiv_token.json')),
)))

try:
    with open(TOKEN_FILE) as f:
        data = json.load(f)
    refresh_token = data.get('refresh_token')
except Exception:
    print(json.dumps({"error": "No token found"}))
    sys.exit(1)

def main():
    try:
        api = AppPixivAPI()
        api.auth(refresh_token=refresh_token)

        count = int(sys.argv[1]) if len(sys.argv) > 1 else 10

        # Search Blue Archive tag, sort by popularity descending
        result = api.search_illust(
            'ブルーアーカイブ',
            search_target='partial_match_for_tags',
            sort='popular_desc',
        )

        illusts = getattr(result, 'illusts', []) or result.get('illusts', [])
        if not illusts:
            print(json.dumps([]))
            sys.exit(0)

        # Filter to hottest in recent 48h
        cutoff = datetime.now(timezone.utc) - timedelta(days=2)

        candidates = []
        for ill in illusts:
            created = getattr(ill, 'create_date', '')
            if created:
                try:
                    # Handle both 'Z' and '+00:00' timezone formats
                    dt = datetime.fromisoformat(created.replace('Z', '+00:00'))
                    if dt < cutoff:
                        continue
                except Exception:
                    pass

            # Get original image URL (full res)
            img_url = (getattr(ill, 'meta_single_page', {}).get('original_image_url', '') or
                       ill.image_urls.get('large') or
                       ill.image_urls.get('medium') or '')

            if img_url and not img_url.startswith('http'):
                img_url = f"https://i.pximg.net{img_url}" if img_url.startswith('/') else f"https://{img_url}"

            tags = [t.name for t in getattr(ill, 'tags', [])] if hasattr(ill, 'tags') else []

            candidates.append({
                'title': getattr(ill, 'title', 'Untitled'),
                'user_name': ill.user.name if hasattr(ill, 'user') and ill.user else 'Unknown',
                'user_id': ill.user.id if hasattr(ill, 'user') and ill.user else '',
                'tags': tags,
                'total_bookmarks': getattr(ill, 'total_bookmarks', 0),
                'total_view': getattr(ill, 'total_view', 0),
                'image_url': img_url,
                'url': f"https://www.pixiv.net/en/artworks/{ill.id}",
                'user_url': f"https://www.pixiv.net/en/users/{ill.user.id}" if hasattr(ill, 'user') and ill.user else '',
            })

        # Sort by total_bookmarks descending (hottest first)
        candidates.sort(key=lambda x: x['total_bookmarks'], reverse=True)

        output = candidates[:count]
        print(json.dumps(output, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e)}, ensure_ascii=False))
        sys.exit(1)

if __name__ == "__main__":
    main()
