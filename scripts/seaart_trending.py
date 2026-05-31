#!/usr/bin/env python3
"""Fetch SeaArt trending posts by scraping the rendered DOM with Playwright."""
import json, sys, re, time
from playwright.sync_api import sync_playwright


def cover_to_file_url(cover_url):
    """Convert cover URL to full-resolution file URL."""
    m = re.search(
        r'temp-convert-webp/(?:highwebp|png|mp4)/([^/]+)/([^/]+)/(.+?)(?:_low|_high)?\.webp$',
        cover_url
    )
    if m:
        date_part, task_part, hash_part = m.group(1), m.group(2), m.group(3)
        return f'https://image.cdn2.seaart.me/{date_part}/{task_part}/{hash_part}_high.webp'
    return cover_url.replace('_low', '_high')


def fetch_trending(count=15):
    """Fetch trending posts from SeaArt by scraping rendered DOM cards."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1440, 'height': 900}
            )
            page = context.new_page()
            page.goto('https://seaart.ai/post', wait_until='networkidle', timeout=30000)
            page.wait_for_timeout(5000)

            # Scrape post data from the rendered DOM
            results = page.evaluate('''(count) => {
                const posts = [];
                // Find all post card links
                const links = document.querySelectorAll('a[href*="postDetail"]');
                
                for (const link of links) {
                    if (posts.length >= count) break;
                    
                    const href = link.getAttribute('href') || '';
                    const m = href.match(/\\/postDetail\\/([^?]+)/);
                    if (!m) continue;
                    const id = m[1];
                    
                    // Find the img element inside the card
                    const img = link.querySelector('img');
                    const imgSrc = img ? (img.getAttribute('src') || img.getAttribute('data-src') || '') : '';
                    
                    // Title: look for title element
                    let title = '';
                    const titleEl = link.querySelector('[class*="title"], [class*="Title"], [class*="name"], [class*="Name"]');
                    if (titleEl) title = titleEl.textContent.trim();
                    
                    // Also try meta tags or figcaptions
                    if (!title) {
                        const allText = link.querySelectorAll('span, p, div');
                        for (const el of allText) {
                            const t = el.textContent.trim();
                            if (t.length > 3 && t.length < 100) {
                                title = t;
                                break;
                            }
                        }
                    }
                    
                    // Author
                    let author = '';
                    const authorEl = link.querySelector('[class*="author"], [class*="Author"], [class*="user"], [class*="User"]');
                    if (authorEl) author = authorEl.textContent.trim();
                    
                    posts.push({
                        'id': id,
                        'title': title,
                        'author': author,
                        'author_avatar': '',
                        'image_url': imgSrc,
                        'image_width': img ? (parseInt(img.getAttribute('width') || '0') || 0) : 0,
                        'image_height': img ? (parseInt(img.getAttribute('height') || '0') || 0) : 0,
                        'likes': 0,
                        'views': 0,
                        'collections': 0,
                        'tags': [],
                        'sub_channel': '',
                        'created_at': 0,
                        'nsfw_level': 0,
                        'url': 'https://seaart.ai/postDetail/' + id,
                    });
                }
                return posts;
            }''', count)

            # Clean up: convert image URLs to full-res
            for r in results:
                if r['image_url']:
                    r['image_url'] = cover_to_file_url(r['image_url'])

            return results

        finally:
            browser.close()


if __name__ == '__main__':
    results = fetch_trending(20)
    print(json.dumps(results, ensure_ascii=False, indent=2))
