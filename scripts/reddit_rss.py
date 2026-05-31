#!/usr/bin/env python3
"""Fetch Reddit AI art posts via RSS (Windows Chrome UA bypasses Cloudflare)."""
import json, sys, subprocess

SUB_LIST = [
    ('LocalLLaMA', 5),
    ('StableDiffusion', 5),
]
TOTAL_LIMIT = 10

def fetch():
    """Fetch Reddit RSS feeds and return combined results (max TOTAL_LIMIT)."""
    results = []
    for sub, limit in SUB_LIST:
        if len(results) >= TOTAL_LIMIT:
            break
        cmd = [
            'curl', '-sL', '--max-time', '10',
            '-H', 'User-Agent: Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
            f'https://old.reddit.com/r/{sub}/.rss'
        ]
        try:
            out = subprocess.run(cmd, capture_output=True, timeout=15, check=False)
        except subprocess.TimeoutExpired as e:
            print(f"⚠️ Error fetching r/{sub}: {e}", file=sys.stderr)
            continue
        except OSError as e:
            print(f"⚠️ Error running curl for r/{sub}: {e}", file=sys.stderr)
            continue
        
        if out.returncode != 0 or not out.stdout.strip():
            if out.stderr:
                print(f"⚠️ Error fetching r/{sub}: {out.stderr.decode(errors='replace').strip()}", file=sys.stderr)
            continue
        
        import xml.etree.ElementTree as ET
        try:
            root = ET.fromstring(out.stdout)
            ns = {'': 'http://www.w3.org/2005/Atom'}
            entries = root.findall('.//entry', ns)
            remaining = TOTAL_LIMIT - len(results)
            for e in entries[:min(limit, remaining)]:
                title_el = e.find('title', ns)
                link_el = e.find('link', ns)
                updated_el = e.find('updated', ns)
                author_el = e.find('author', ns)
                content_el = e.find('content', ns)
                
                title = title_el.text[:200] if title_el is not None and title_el.text else ''
                link = link_el.get('href', '') if link_el is not None else ''
                updated = updated_el.text[:10] if updated_el is not None and updated_el.text else ''
                author = author_el.find('name', ns).text if author_el is not None and author_el.find('name', ns) is not None else ''
                
                # Strip old.reddit.com -> reddit.com
                clean_link = link.replace('old.reddit.com', 'www.reddit.com')
                
                # Get summary from content
                summary = ''
                if content_el is not None and content_el.text:
                    import re
                    summary = re.sub(r'<[^>]+>', '', content_el.text)[:200]
                
                results.append({
                    'subreddit': sub,
                    'title': title,
                    'author': author,
                    'url': clean_link,
                    'date': updated,
                    'summary': summary,
                })
        except Exception as e:
            print(f"⚠️ Error parsing r/{sub}: {e}", file=sys.stderr)
            continue
    
    return results

if __name__ == '__main__':
    posts = fetch()
    print(json.dumps(posts, ensure_ascii=False, indent=2))
