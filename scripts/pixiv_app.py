#!/usr/bin/env python3
"""Fetch Pixiv R18 daily/weekly ranking via App API (pixivpy3)."""
import json, sys
from pathlib import Path
from pixivpy3 import AppPixivAPI

TOKEN_FILE = Path(__file__).with_name('.pixiv_token.json')

try:
    with open(TOKEN_FILE) as f:
        data = json.load(f)
    refresh_token = data.get('refresh_token')
except Exception:
    print(json.dumps({"error": "No token found", "results": []}))
    sys.exit(1)

def main():
    try:
        api = AppPixivAPI()
        api.auth(refresh_token=refresh_token)

        mode = sys.argv[1] if len(sys.argv) > 1 else 'day_r18'
        count = int(sys.argv[2]) if len(sys.argv) > 2 else 10

        # Map CLI-friendly names to App API mode names
        mode_map = {
            'daily': 'day',
            'weekly': 'week',
            'day': 'day',
            'week': 'week',
            'day_sfw': 'day',
            'week_sfw': 'week',
            'day_r18': 'day_r18',
            'week_r18': 'week_r18',
            'week_r18g': 'week_r18g',
            'day_male_r18': 'day_male_r18',
            'day_female_r18': 'day_female_r18',
        }
        api_mode = mode_map.get(mode, mode)

        result = api.illust_ranking(mode=api_mode, offset=0)
        illusts = getattr(result, 'illusts', [])

        output = []
        for ill in illusts[:count]:
            img_url = (getattr(ill, 'meta_single_page', {}).get('original_image_url', '') or  # full res
                       ill.image_urls.get('large') or  # ~600x1200
                       ill.image_urls.get('medium') or '')

            # Ensure proper URL format (add https:// if missing)
            if img_url and not img_url.startswith('http'):
                img_url = f"https://i.pximg.net{img_url}" if img_url.startswith('/') else f"https://{img_url}"

            tags = [t.name for t in getattr(ill, 'tags', [])] if hasattr(ill, 'tags') else []

            output.append({
                'rank': len(output) + 1,
                'title': getattr(ill, 'title', ''),
                'illust_id': ill.id,
                'user_name': ill.user.name if hasattr(ill, 'user') and ill.user else '',
                'user_id': ill.user.id if hasattr(ill, 'user') and ill.user else '',
                'tags': tags,
                'rating_count': getattr(ill, 'total_bookmarks', 0),
                'view_count': getattr(ill, 'total_view', 0),
                'image_url': img_url,
                'url': f"https://www.pixiv.net/en/artworks/{ill.id}",
                'user_url': f"https://www.pixiv.net/en/users/{ill.user.id}" if hasattr(ill, 'user') and ill.user else '',
                'x_restrict': getattr(ill, 'x_restrict', 0),
            })

        print(json.dumps(output, ensure_ascii=False, indent=2))
    except Exception as e:
        print(json.dumps({"error": str(e), "results": []}, ensure_ascii=False))
        sys.exit(1)

if __name__ == "__main__":
    main()
