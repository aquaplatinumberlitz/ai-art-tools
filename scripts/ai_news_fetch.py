#!/usr/bin/env python3
"""Fetch AI news from RSS feeds and save JSON for downstream report use.

Output: /tmp/hermes_report/ai_news_raw.json
"""
import json, os, sys, re, socket
from datetime import datetime, timezone, timedelta
from email.utils import parsedate_to_datetime
import feedparser
import requests

DATA_DIR = '/tmp/hermes_report'
OUTPUT = os.path.join(DATA_DIR, 'ai_news_raw.json')
os.makedirs(DATA_DIR, exist_ok=True)

UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/120.0.0.0'

FEEDS = [
    # 1. TechCrunch AI (filter by AI-related keywords)
    {
        'name': 'TechCrunch AI',
        'url': 'https://techcrunch.com/category/artificial-intelligence/feed/',
        'filter_keywords': True,
    },
    # 2. VentureBeat (already AI focused)
    {
        'name': 'VentureBeat',
        'url': 'https://feeds.feedburner.com/venturebeat/SZYF',
        'filter_keywords': True,
    },
    # 3. Wired (mixed content, need stricter filter)
    {
        'name': 'Wired',
        'url': 'https://www.wired.com/feed/rss',
        'filter_keywords': True,
    },
    # 4. Import AI (pure AI analysis, keep all)
    {
        'name': 'Import AI',
        'url': 'https://importai.substack.com/feed',
        'filter_keywords': False,
    },
    # 5. MIT Tech Review (mixed)
    {
        'name': 'MIT Tech Review',
        'url': 'https://www.technologyreview.com/feed/',
        'filter_keywords': True,
    },
    # 6. HuggingFace daily papers (model releases + papers)
    {
        'name': 'HuggingFace Papers',
        'url': 'https://huggingface.co/api/daily_papers?limit=10',
        'filter_keywords': False,
    },
]

AI_KEYWORDS = [
    'ai', 'artificial intelligence', 'machine learning', 'deep learning',
    'llm', 'large language model', 'gpt', 'claude', 'gemini', 'llama',
    'mistral', 'deepseek', 'qwen', 'open source', 'open-source',
    'model', 'neural', 'transformer', 'diffusion', 'agent',
    'nvidia', 'gpu', 'chip', 'cerebras', 'amd', 'inference',
    'openai', 'anthropic', 'google deepmind', 'meta ai',
    'hugging face', 'huggingface', 'chatbot', 'arena',
    'robotics', 'self-driving', 'autonomous',
    'copilot', 'coding', 'generative',
]

AI_KEYWORD_PATTERNS = [
    re.compile(r'\bAI\b', re.IGNORECASE) if kw == 'ai'
    else re.compile(r'(?<![A-Za-z0-9])' + re.escape(kw) + r'(?![A-Za-z0-9])', re.IGNORECASE)
    for kw in AI_KEYWORDS
]

socket.setdefaulttimeout(15)


def matches_ai_keywords(text):
    """Return True if text contains an AI keyword as a word/token match."""
    return any(pattern.search(text) for pattern in AI_KEYWORD_PATTERNS)


def parse_item_datetime(item):
    """Best-effort parse of item dates to an aware UTC datetime."""
    for field in ('raw_date', 'published'):
        value = item.get(field, '')
        if not value:
            continue
        try:
            dt = parsedate_to_datetime(value)
        except (TypeError, ValueError, IndexError):
            try:
                dt = datetime.fromisoformat(value.replace('Z', '+00:00'))
            except (TypeError, ValueError):
                continue
        if dt.tzinfo is None:
            dt = dt.replace(tzinfo=timezone.utc)
        return dt.astimezone(timezone.utc)
    return None


def is_recent_item(item, cutoff):
    published_at = parse_item_datetime(item)
    return published_at is None or published_at >= cutoff


def fetch_feed(name, url, filter_keywords=True, max_items=15):
    """Fetch and parse an RSS feed."""
    items = []
    error = ''
    try:
        # Try feedparser first
        feed = feedparser.parse(url)
        
        if feed.entries:
            for entry in feed.entries[:max_items]:
                title = entry.get('title', '') or ''
                link = entry.get('link', '') or ''
                summary = entry.get('summary', '') or ''
                # Try to get published date
                published = ''
                if hasattr(entry, 'published_parsed') and entry.published_parsed:
                    published = datetime(*entry.published_parsed[:6]).strftime('%Y-%m-%d')
                elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                    published = datetime(*entry.updated_parsed[:6]).strftime('%Y-%m-%d')

                # Clean HTML from summary
                clean_summary = re.sub(r'<[^>]+>', '', summary).strip()
                
                text = f"{title} {clean_summary}"
                
                # Filter by AI keywords if needed
                if filter_keywords and not matches_ai_keywords(text):
                    continue
                
                items.append({
                    'source': name,
                    'title': title.strip(),
                    'url': link,
                    'summary': clean_summary[:500],
                    'published': published,
                    'raw_date': entry.get('published', ''),
                })
        
        # If feedparser returned nothing, try requests + manual parse
        if not items:
            r = requests.get(url, headers={'User-Agent': UA}, timeout=15)
            if r.status_code == 200:
                feed2 = feedparser.parse(r.content)
                for entry in feed2.entries[:max_items]:
                    title = entry.get('title', '') or ''
                    link = entry.get('link', '') or ''
                    summary = entry.get('summary', '') or ''
                    clean_summary = re.sub(r'<[^>]+>', '', summary).strip()
                    text = f"{title} {clean_summary}"
                    if filter_keywords and not matches_ai_keywords(text):
                        continue
                    published = ''
                    if hasattr(entry, 'published_parsed') and entry.published_parsed:
                        published = datetime(*entry.published_parsed[:6]).strftime('%Y-%m-%d')
                    elif hasattr(entry, 'updated_parsed') and entry.updated_parsed:
                        published = datetime(*entry.updated_parsed[:6]).strftime('%Y-%m-%d')
                    items.append({
                        'source': name,
                        'title': title.strip(),
                        'url': link,
                        'summary': clean_summary[:500],
                        'published': published,
                    })
            else:
                error = f'HTTP {r.status_code}'

    except Exception as e:
        error = str(e)
        print(f'  ⚠️ {name}: {e}')
    
    return items, error


def fetch_hf_papers():
    """Fetch HuggingFace daily papers."""
    items = []
    error = ''
    try:
        r = requests.get('https://huggingface.co/api/daily_papers?limit=10', 
                        headers={'User-Agent': UA}, timeout=15)
        if r.status_code == 200:
            papers = r.json()
            for p in papers:
                paper = p.get('paper', {})
                title = paper.get('title', '') or ''
                paper_id = paper.get('id', '')
                url = f"https://arxiv.org/abs/{paper_id}" if paper_id else ''
                summary = paper.get('summary', '') or ''
                clean_summary = re.sub(r'<[^>]+>', '', summary).strip()
                
                items.append({
                    'source': 'HuggingFace Papers',
                    'title': title.strip(),
                    'url': url,
                    'summary': clean_summary[:500],
                    'published': '',
                })
        else:
            error = f'HTTP {r.status_code}'
    except Exception as e:
        error = str(e)
        print(f'  ⚠️ HuggingFace Papers: {e}')
    return items, error


def main():
    all_items = []
    sources = {}
    
    print(f'🔍 Fetching AI news... ({datetime.now().strftime("%Y-%m-%d %H:%M")})')
    
    for feed_cfg in FEEDS:
        name = feed_cfg['name']
        print(f'  📡 {name}...', end=' ', flush=True)
        
        if name == 'HuggingFace Papers':
            items, error = fetch_hf_papers()
        else:
            items, error = fetch_feed(
                name=name,
                url=feed_cfg['url'],
                filter_keywords=feed_cfg['filter_keywords'],
            )
        sources[name] = {
            'count': len(items),
            'ok': not error,
            'error': error,
        }
        
        print(f'{len(items)} items')
        for item in items[:3]:
            print(f'      → {item["title"][:100]}')
        all_items.extend(items)

    cutoff = datetime.now(timezone.utc) - timedelta(hours=48)
    recent_items = [item for item in all_items if is_recent_item(item, cutoff)]
    
    # Remove duplicates by URL
    seen_urls = set()
    unique_items = []
    for item in recent_items:
        url = item['url']
        if url and url in seen_urls:
            continue
        if url:
            seen_urls.add(url)
        unique_items.append(item)
    
    print(f'\n📊 Total: {len(all_items)} raw → {len(recent_items)} recent → {len(unique_items)} unique')
    
    # Save JSON
    output = {
        'fetched_at': datetime.now(timezone.utc).isoformat(),
        'total_raw': len(all_items),
        'total_unique': len(unique_items),
        'sources': sources,
        'items': unique_items,
    }
    
    tmp_output = OUTPUT + '.tmp'
    with open(tmp_output, 'w', encoding='utf-8') as f:
        json.dump(output, f, ensure_ascii=False, indent=2)
    os.replace(tmp_output, OUTPUT)
    
    print(f'💾 Saved: {OUTPUT} ({len(json.dumps(output, ensure_ascii=False)):,} bytes)')
    
    # Exit with error if too few items (signal downstream)
    if len(unique_items) < 3:
        print('⚠️ WARNING: Too few items fetched!')
        sys.exit(1)


if __name__ == '__main__':
    main()
