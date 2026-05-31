#!/usr/bin/env python3
"""
PixAI Blue Archive Fanart Crawler
- Uses cookies from storage_state directly with GraphQL API (NO browser needed)
- Falls back to Playwright login only when session expires
"""
import json, sys, os, time, requests
from playwright.sync_api import sync_playwright

EMAIL = os.environ.get('PIXAI_EMAIL', '')
PASS = os.environ.get('PIXAI_PASSWORD', '')
STATE_FILE = os.path.expanduser("~/.hermes/scripts/.pixai_state.json")
BA_TACK_ID = "1941984459972170287"

API_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Referer': 'https://pixai.art/',
    'Origin': 'https://pixai.art',
}

def load_state():
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Error: {e}", file=sys.stderr)
        return None

def get_cookies(state):
    cookies = {}
    for c in state.get('cookies', []):
        if c.get('domain', '').endswith('pixai.art') and c.get('name'):
            cookies[c['name']] = c['value']
    return cookies

def get_token_from_state(state):
    for origin in state.get('origins', []):
        for item in origin.get('localStorage', []):
            if item.get('name') == 'https://api.pixai.art:token':
                return item.get('value', '')
    return ''

def fetch_ba_via_api(token='', cookies=None, limit=5):
    """Try fetching Blue Archive artworks via GraphQL with available auth."""
    headers = {
        'User-Agent': 'Mozilla/5.0 Chrome/120.0.0.0 Safari/537.36',
        'Content-Type': 'application/json',
        'Accept': 'application/json',
        'Origin': 'https://pixai.art',
    }
    if token:
        headers['Authorization'] = f'Bearer {token}'
    
    query = 'query($first: Int, $tackId: String) { artworks(first: $first, tackId: $tackId) { edges { node { id title media { urls { url variant } } author { id displayName username } createdAt likedCount } } } }'
    
    try:
        r = requests.post('https://api.pixai.art/graphql', headers=headers,
                         cookies=cookies or {}, json={
            'query': query,
            'variables': {'first': limit, 'tackId': BA_TACK_ID}
        }, timeout=15)
        if r.status_code == 200:
            data = r.json()
            if 'errors' not in data:
                edges = data.get('data', {}).get('artworks', {}).get('edges', [])
                if edges:
                    return [e['node'] for e in edges[:limit]]
    except Exception as e:
        print(f"⚠️ Error: {e}", file=sys.stderr)
    return None

def login_and_save():
    """Full Playwright login, save storage_state."""
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1366, 'height': 768}
        )
        page = ctx.new_page()
        page.goto('https://pixai.art/en/login', wait_until='domcontentloaded', timeout=30000)
        page.wait_for_timeout(8000)
        
        btn = page.query_selector('button:has-text("Continue with Email")')
        if btn: btn.click()
        page.wait_for_timeout(3000)
        
        page.fill('input[type="email"]', EMAIL)
        page.fill('input[type="password"]', PASS)
        page.wait_for_timeout(500)
        page.press('input[type="password"]', 'Enter')
        page.wait_for_timeout(8000)
        
        try:
            page.wait_for_url(lambda u: '/en' in u and '/login' not in u, timeout=15000)
        except Exception as e:
            print(f"⚠️ Error: {e}", file=sys.stderr)
        
        token = page.evaluate('localStorage.getItem("https://api.pixai.art:token")')
        if token:
            state = ctx.storage_state()
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
            os.chmod(STATE_FILE, 0o600)
            bid = page.evaluate('localStorage.getItem("browser-id")') or ''
            browser.close()
            return {'token': token, 'browser_id': bid, 'cookies': get_cookies(state)}
        browser.close()
    return None

def get_auth():
    """Try saved cookies/token first, fall back to login."""
    state = load_state()
    
    if state:
        cookies = get_cookies(state)
        token = get_token_from_state(state)
        
        # 1) Try token
        if token:
            data = fetch_ba_via_api(token=token)
            if data:
                return {'token': token, 'browser_id': '', 'cookies': cookies}
        
        # 2) Try cookies
        if cookies:
            data = fetch_ba_via_api(cookies=cookies)
            if data:
                return {'token': '', 'browser_id': '', 'cookies': cookies}
    
    # 3) Full login
    return login_and_save()

def format_results(artworks, limit=5):
    results = []
    for node in artworks[:limit]:
        media = node.get('media', {})
        author = node.get('author', {})
        
        img_url = ''
        if media.get('urls'):
            for u in media['urls']:
                if u.get('variant') == 'THUMBNAIL':
                    img_url = u['url']
                    break
            if not img_url:
                for u in media['urls']:
                    if u.get('variant') == 'PUBLIC':
                        img_url = u['url']
                        break
        
        if not img_url: continue
        results.append({
            'id': node.get('id', ''),
            'title': (node.get('title') or 'Untitled')[:100],
            'author': author.get('displayName') or author.get('username') or 'Unknown',
            'image_url': img_url,
            'url': f"https://pixai.art/en/artwork/{node.get('id')}",
            'likes': node.get('likedCount', 0),
            'created_at': (node.get('createdAt') or '')[:10],
        })
    return results

if __name__ == '__main__':
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    auth = get_auth()
    if not auth:
        print(json.dumps({"error": "Auth failed"}, ensure_ascii=False))
        sys.exit(1)
    artworks = fetch_ba_via_api(token=auth.get('token', ''), cookies=auth.get('cookies', {}), limit=limit)
    if not artworks:
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
        auth = login_and_save()
        if auth:
            from functools import partial
            artworks = fetch_ba_via_api(token=auth.get('token', ''), cookies=auth.get('cookies', {}), limit=limit)
    if not artworks:
        print(json.dumps({"error": "No artworks found"}, ensure_ascii=False))
        sys.exit(1)
    results = format_results(artworks, limit)
    print(json.dumps(results, ensure_ascii=False, indent=2))
