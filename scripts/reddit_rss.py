#!/usr/bin/env python3
"""Fetch Reddit AI art posts via RSS."""
import json, re, sys
from datetime import datetime

import feedparser
import requests

SUB_LIST = [
    ('LocalLLaMA', 5),
    ('StableDiffusion', 5),
]
TOTAL_LIMIT = 10
HEADERS = {'User-Agent': 'ai-art-tools/1.0'}


def entry_date(entry):
    """Return YYYY-MM-DD date from feedparser entry timestamps."""
    parsed = getattr(entry, 'updated_parsed', None) or getattr(entry, 'published_parsed', None)
    if parsed:
        return datetime(*parsed[:6]).strftime('%Y-%m-%d')
    raw = entry.get('updated') or entry.get('published') or ''
    return raw[:10] if raw else ''


def entry_summary(entry):
    """Return plain-text summary capped to the existing output size."""
    summary = entry.get('summary', '')
    if not summary and entry.get('content'):
        summary = entry.content[0].get('value', '')
    return re.sub(r'<[^>]+>', '', summary)[:200]


def fetch():
    """Fetch Reddit RSS feeds and return combined results (max TOTAL_LIMIT)."""
    results = []
    for sub, limit in SUB_LIST:
        if len(results) >= TOTAL_LIMIT:
            break
        url = f'https://old.reddit.com/r/{sub}/.rss'
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
        except requests.RequestException as e:
            print(f"⚠️ Error fetching r/{sub}: {e}", file=sys.stderr)
            continue

        feed = feedparser.parse(r.content)
        if getattr(feed, 'bozo', False):
            print(f"⚠️ Error parsing r/{sub}: {getattr(feed, 'bozo_exception', 'invalid feed')}", file=sys.stderr)
        entries = feed.entries or []
        if not entries:
            continue

        remaining = TOTAL_LIMIT - len(results)
        for entry in entries[:min(limit, remaining)]:
            link = entry.get('link', '').replace('old.reddit.com', 'www.reddit.com')
            results.append({
                'subreddit': sub,
                'title': entry.get('title', '')[:200],
                'author': entry.get('author', ''),
                'url': link,
                'date': entry_date(entry),
                'summary': entry_summary(entry),
            })
    
    return results

if __name__ == '__main__':
    posts = fetch()
    print(json.dumps(posts, ensure_ascii=False, indent=2))
