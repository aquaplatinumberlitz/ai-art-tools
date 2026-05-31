#!/usr/bin/env python3
"""
PixAI Trending Artwork Crawler
- Uses cookies from storage_state directly with REST API (NO browser needed most runs)
- Falls back to Playwright login only when session expires
"""
import json, sys, os, time, requests
from playwright.sync_api import sync_playwright

EMAIL = os.environ.get('PIXAI_EMAIL', '')
PASS = os.environ.get('PIXAI_PASSWORD', '')
STATE_FILE = os.path.expanduser("~/.hermes/scripts/.pixai_state.json")

API_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json',
    'Referer': 'https://pixai.art/',
    'Origin': 'https://pixai.art',
}

def log(msg):
    print(f"PixAI: {msg}", file=sys.stderr)

def load_state():
    """Load saved Playwright storage_state."""
    if not os.path.exists(STATE_FILE):
        return None
    try:
        with open(STATE_FILE) as f:
            return json.load(f)
    except Exception as e:
        print(f"⚠️ Error: {e}", file=sys.stderr)
        return None

def get_cookies(state):
    """Extract cookies dict from storage_state."""
    cookies = {}
    for c in state.get('cookies', []):
        if c.get('domain', '').endswith('pixai.art') and c.get('name'):
            # PixAI uses __Host- cookies which need secure flag
            cookies[c['name']] = c['value']
    return cookies

def get_token_from_state(state):
    """Extract JWT token from storage_state's localStorage snapshot."""
    for origin in state.get('origins', []):
        for item in origin.get('localStorage', []):
            if item.get('name') == 'https://api.pixai.art:token':
                return item.get('value', '')
    return ''

def try_api(token='', cookies=None):
    """Try calling the REST API with available auth. Returns data or None."""
    headers = dict(API_HEADERS)
    if token:
        headers['Authorization'] = f'Bearer {token}'
    try:
        r = requests.get('https://api.pixai.art/v2/artwork/recommend?first=5',
                        headers=headers, cookies=cookies or {}, timeout=10)
        if r.status_code == 401:
            log("saved auth rejected with HTTP 401")
            return None
        if r.status_code == 200:
            data = r.json().get('data', [])
            if data and data[0].get('title'):
                return data
        else:
            log(f"auth probe returned HTTP {r.status_code}")
    except Exception as e:
        log(f"auth probe failed: {e}")
    return None

def login_and_save():
    """Full Playwright login, save storage_state for future reuse."""
    if not EMAIL or not PASS:
        log("missing PIXAI_EMAIL or PIXAI_PASSWORD")
        return None
    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        ctx = browser.new_context(
            user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 Chrome/120.0.0.0 Safari/537.36',
            viewport={'width': 1366, 'height': 768}
        )
        page = ctx.new_page()
        try:
            page.goto('https://pixai.art/en/login', wait_until='domcontentloaded', timeout=30000)
            page.wait_for_selector('input[type="email"], button:has-text("Continue with Email")', timeout=15000)
            
            btn = page.query_selector('button:has-text("Continue with Email")')
            if btn:
                btn.click()
                page.wait_for_selector('input[type="email"]', timeout=15000)
            
            page.fill('input[type="email"]', EMAIL)
            page.fill('input[type="password"]', PASS)
            page.press('input[type="password"]', 'Enter')
            page.wait_for_url(lambda u: '/en' in u and '/login' not in u, timeout=15000)
        except Exception as e:
            log(f"Playwright login failed before session token was available: {e}")
            browser.close()
            return None
        
        token = page.evaluate('localStorage.getItem("https://api.pixai.art:token")')
        if token:
            state = ctx.storage_state()
            os.makedirs(os.path.dirname(STATE_FILE), exist_ok=True)
            with open(STATE_FILE, 'w') as f:
                json.dump(state, f)
            os.chmod(STATE_FILE, 0o600)
            bid = page.evaluate('localStorage.getItem("browser-id")') or ''
            browser.close()
            return {'token': token, 'browser_id': bid, 'state': state}
        log("Playwright login completed without PixAI API token")
        browser.close()
    return None

def get_auth():
    """Get auth: try saved cookies → saved token → full login."""
    state = load_state()
    
    if state:
        cookies = get_cookies(state)
        token = get_token_from_state(state)
        
        # 1) Try saved token first (fastest)
        if token:
            data = try_api(token=token)
            if data:
                bid = ''
                for origin in state.get('origins', []):
                    for item in origin.get('localStorage', []):
                        if item.get('name') == 'browser-id':
                            bid = item.get('value', '')
                            break
                return {'token': token, 'browser_id': bid, 'cookies': cookies}
        
        # 2) Try cookies only (no browser needed)
        if cookies:
            data = try_api(cookies=cookies)
            if data:
                # Cookies work but token expired - we can still use cookies
                # Try to get a fresh token from the API response
                return {'token': '', 'browser_id': '', 'cookies': cookies}
    
    # 3) Full login
    result = login_and_save()
    if result:
        return result
    
    return None

def fetch_trending(auth, limit=15, retry_on_401=True):
    """Fetch trending artworks."""
    headers = dict(API_HEADERS)
    if auth.get('token'):
        headers['Authorization'] = f'Bearer {auth["token"]}'
    
    # Try REST
    try:
        r = requests.get(f'https://api.pixai.art/v2/artwork/recommend?first={limit}',
                        headers=headers, cookies=auth.get('cookies', {}), timeout=15)
        if r.status_code == 401:
            log("artwork API returned HTTP 401")
            if retry_on_401:
                log("retrying once after Playwright re-login")
                if os.path.exists(STATE_FILE):
                    os.remove(STATE_FILE)
                refreshed = login_and_save()
                if refreshed:
                    return fetch_trending(refreshed, limit, retry_on_401=False)
            return []
        if r.status_code == 200:
            data = r.json().get('data', [])
            if data: return data
        else:
            log(f"artwork API returned HTTP {r.status_code}")
    except Exception as e:
        log(f"artwork API failed: {e}")
    
    return []

def format_results(artworks, limit=15):
    """Convert to clean output."""
    results = []
    for art in artworks[:limit]:
        media = art.get('media', {})
        author = art.get('author', {})
        img_url = media.get('thumbnailUrl', '')
        if not img_url and media.get('urls'):
            for u in media['urls']:
                if u.get('variant') in ('PUBLIC', 'THUMBNAIL'):
                    img_url = u['url']
                    break
        if not img_url: continue
        results.append({
            'id': art.get('id', ''),
            'title': (art.get('title') or 'Untitled')[:100],
            'author': author.get('displayName') or author.get('username', 'Unknown'),
            'image_url': img_url,
            'url': f"https://pixai.art/en/artwork/{art.get('id')}",
            'likes': art.get('likedCount', 0),
            'created_at': (art.get('createdAt') or '')[:10],
        })
    return results

def main():
    limit = int(sys.argv[1]) if len(sys.argv) > 1 else 15
    auth = get_auth()
    if not auth:
        print(json.dumps({"error": "Auth failed"}, ensure_ascii=False))
        sys.exit(1)
    artworks = fetch_trending(auth, limit)
    if not artworks:
        # Session dead - re-login
        if os.path.exists(STATE_FILE):
            os.remove(STATE_FILE)
        auth = login_and_save()
        if auth:
            artworks = fetch_trending(auth, limit)
    if not artworks:
        print(json.dumps({"error": "No artworks found"}, ensure_ascii=False))
        sys.exit(1)
    results = format_results(artworks, limit)
    print(json.dumps(results, ensure_ascii=False, indent=2))

if __name__ == '__main__':
    main()
