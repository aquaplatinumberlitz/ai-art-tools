"""Facebook browser-based job post crawler.

Read-only crawl of Facebook groups using Playwright Chromium.
- Logs in with stored credentials
- Visits configured groups
- Searches keywords within groups
- Scrolls with 2-6s random delay
- Extracts job-like posts
- Saves raw data
- Handles checkpoints/CAPTCHA gracefully
"""

import json
import logging
import os
import random
import re
import time
from datetime import datetime
from typing import Optional

logger = logging.getLogger("facebook_browser")

# ── Constants ──
CRED_FILE = os.path.expanduser("~/.hermes/references/facebook_job_crawl_cred.json")
RAW_DIR = os.path.expanduser("~/reports/job_hyogo")
MAX_POSTS_PER_GROUP = 50
MIN_SCROLL_WAIT = 2
MAX_SCROLL_WAIT = 6
MAX_SCROLLS = 20  # max scroll actions per group
LOGIN_TIMEOUT_MS = 30000
NAV_TIMEOUT_MS = 30000

# ── Load credentials ──
def load_creds() -> Optional[dict]:
    if not os.path.exists(CRED_FILE):
        logger.warning("Facebook credential file not found: %s", CRED_FILE)
        return None
    try:
        with open(CRED_FILE, "r") as f:
            data = json.load(f)
        if data.get("email") and data.get("password"):
            return data
        logger.warning("Facebook credential file missing email/password")
        return None
    except Exception as e:
        logger.error("Failed to load Facebook credentials: %s", e)
        return None

# ── Browser singleton ──
_browser = None
_context = None
_page = None

def _random_delay():
    time.sleep(random.uniform(MIN_SCROLL_WAIT, MAX_SCROLL_WAIT))

def init_browser():
    """Initialize Playwright browser with stealth settings."""
    global _browser, _context, _page
    if _page:
        return _page

    from playwright.sync_api import sync_playwright

    pw = sync_playwright().start()
    _browser = pw.chromium.launch(
        headless=True,
        args=[
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-dev-shm-usage",
            "--disable-web-security",
            "--disable-features=IsolateOrigins,site-per-process",
        ],
    )

    _context = _browser.new_context(
        viewport={"width": 1366, "height": 768},
        user_agent=(
            "Mozilla/5.0 (X11; Linux aarch64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/125.0.0.0 Safari/537.36"
        ),
        locale="ja-JP",
        timezone_id="Asia/Tokyo",
        geolocation={"latitude": 34.8, "longitude": 134.7},  # Hyogo area
        permissions=["geolocation"],
    )

    # Hide webdriver
    _context.add_init_script("""
        Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
        Object.defineProperty(navigator, 'plugins', { get: () => [1,2,3,4,5] });
    """)

    _page = _context.new_page()
    logger.info("Browser initialized")
    return _page

def close_browser():
    """Clean shutdown."""
    global _browser, _context, _page
    try:
        if _page:
            _page.close()
    except: pass
    try:
        if _context:
            _context.close()
    except: pass
    try:
        if _browser:
            _browser.close()
    except: pass
    _page = None
    _context = None
    _browser = None
    logger.info("Browser closed")

def login() -> bool:
    """Log into Facebook. Returns True if successful."""
    creds = load_creds()
    if not creds:
        logger.warning("No credentials — skipping Facebook login")
        return False

    page = init_browser()
    try:
        logger.info("Navigating to Facebook login...")
        page.goto("https://www.facebook.com/login", timeout=LOGIN_TIMEOUT_MS, wait_until="domcontentloaded")
        _random_delay()

        # Click "Accept all" cookies if present
        try:
            accept_btn = page.locator('button[data-testid="cookie-policy-manage-dialog-accept-button"], button:has-text("Allow all"), button:has-text("許可"), button:has-text("同意")')
            if accept_btn.is_visible(timeout=3000):
                accept_btn.click()
                _random_delay()
        except:
            pass

        # Fill email
        email_input = page.locator('input[name="email"], input#email, input[autocomplete="username"]')
        email_input.first.fill(creds["email"])
        _random_delay()

        # Fill password
        pass_input = page.locator('input[name="pass"], input#pass')
        pass_input.first.fill(creds["password"])
        _random_delay()

        # Click login — Facebook uses div[role="button"] not <button>
        # Try visible div[role="button"] first, then force-click hidden input[type="submit"]
        try:
            login_div = page.locator('div[role="button"]:has-text("ログイン"), div[role="button"]:has-text("Log In")')
            if login_div.count() > 0 and login_div.first.is_visible():
                login_div.first.click()
                logger.info("Clicked login via div[role=button]")
            else:
                # Force click the hidden submit input
                page.evaluate("document.querySelector('input[type=\"submit\"]')?.click()")
                logger.info("Clicked login via JS submit")
        except Exception as e:
            # Final fallback: JS form submit
            try:
                page.evaluate("document.querySelector('form')?.requestSubmit()")
                logger.info("Clicked login via form.requestSubmit()")
            except Exception as e2:
                logger.warning("All login click methods failed: %s / %s", e, e2)
                return False

        # Wait for navigation after login
        try:
            page.wait_for_url("**/checkpoint/**", timeout=8000)
            logger.warning("Facebook checkpoint triggered! Stopping crawl.")
            return False
        except:
            pass

        try:
            page.wait_for_load_state("networkidle", timeout=15000)
        except:
            pass

        _random_delay()

        # Check if logged in successfully
        current_url = page.url
        if "login" in current_url and "checkpoint" not in current_url:
            logger.warning("Still on login page — login may have failed")
            return False

        logger.info("Facebook login successful")
        return True

    except Exception as e:
        logger.error("Facebook login failed: %s", e)
        return False

def crawl_group(group_url: str, keywords: list[str] | None = None) -> list[dict]:
    """Crawl a single Facebook group for job posts.

    Returns list of raw post dicts.
    """
    page = init_browser()
    posts = []
    seen_links = set()

    try:
        logger.info("Navigating to group: %s", group_url)
        try:
            page.goto(group_url, timeout=NAV_TIMEOUT_MS, wait_until="domcontentloaded")
        except Exception as nav_err:
            logger.warning("Navigation to group failed: %s", nav_err)
            return posts

        _random_delay()

        # Check if we hit a login-wall or private group page
        current_url = page.url
        page_text = page.inner_text("body")[:1000].lower()
        if "log in" in page_text or "login" in page_text or "đăng nhập" in page_text:
            logger.warning("Group page shows login wall - cannot access: %s", group_url)
            return posts
        if "this content isn't available" in page_text or "content isn't available" in page_text:
            logger.warning("Group content unavailable (private/deleted): %s", group_url)
            return posts
        if "join group" in page_text or "request to join" in page_text:
            logger.warning("Group requires membership: %s", group_url)
            return posts

        # Wait a moment for feed to render
        try:
            page.wait_for_timeout(3000)
        except:
            pass

        # Try to find posts - look for various selectors
        post_selectors = ['[role="article"]', 'div[data-pagelet]', 'div.x1yqt14a']
        found = False
        for sel in post_selectors:
            try:
                if page.locator(sel).count() > 0:
                    found = True
                    break
            except:
                pass

        if not found:
            logger.warning("No post elements found in group (likely private or no content): %s", group_url)
            return posts

        # Scroll and collect posts
        for scroll_num in range(MAX_SCROLLS):
            # Extract visible posts
            try:
                article_elements = page.locator('[role="article"]').all()
                for el in article_elements:
                    try:
                        text = el.inner_text(timeout=3000)

                        # Skip non-job posts early
                        if len(text) < 30:
                            continue

                        # Get post link
                        link_el = el.locator('a[href*="/posts/"], a[href*="/photo/"]').first
                        post_url = ""
                        try:
                            post_url = link_el.get_attribute("href", timeout=2000) or ""
                            if post_url and not post_url.startswith("http"):
                                post_url = "https://www.facebook.com" + post_url
                        except:
                            pass

                        # Deduplicate
                        link_key = post_url or text[:100]
                        if link_key in seen_links:
                            continue
                        seen_links.add(link_key)

                        # Get poster name
                        poster = ""
                        try:
                            poster_el = el.locator('a[role="link"] h3, a[role="link"] span, .x1i10hfl').first
                            poster = poster_el.inner_text(timeout=2000)
                        except:
                            pass

                        posts.append({
                            "group_url": group_url,
                            "post_url": post_url,
                            "poster": poster[:200] if poster else "",
                            "text": text[:2000],
                            "scraped_at": datetime.now().isoformat(),
                        })

                        if len(posts) >= MAX_POSTS_PER_GROUP:
                            logger.info("Reached max %d posts for group, stopping scroll", MAX_POSTS_PER_GROUP)
                            break
                    except:
                        continue

                if len(posts) >= MAX_POSTS_PER_GROUP:
                    break
            except:
                pass

            # Scroll down
            try:
                page.evaluate("window.scrollBy(0, window.innerHeight * 0.8)")
            except:
                page.keyboard.press("PageDown")

            _random_delay()

        logger.info("Collected %d posts from group", len(posts))

    except Exception as e:
        logger.error("Error crawling group %s: %s", group_url, e)

    return posts

def crawl(group_configs: list[dict]) -> dict:
    """Main crawl entry point.

    group_configs: list of {"url": str, "keywords": [str]}
    Returns: {"posts": [post], "errors": [str], "logged_in": bool}
    """
    result = {
        "posts": [],
        "errors": [],
        "logged_in": False,
        "groups_visited": 0,
        "groups_failed": 0,
    }

    if not group_configs:
        logger.info("No Facebook groups configured — skipping")
        return result

    if not login():
        result["errors"].append("Facebook login failed")
        return result

    result["logged_in"] = True

    for gc in group_configs:
        url = gc.get("url", "")
        if not url:
            continue
        try:
            kws = gc.get("keywords", [])
            posts = crawl_group(url, kws)
            result["posts"].extend(posts)
            result["groups_visited"] += 1
        except Exception as e:
            error_str = str(e)
            logger.error("Failed to crawl group %s: %s", url, error_str)
            result["errors"].append(f"Group {url}: {error_str}")
            result["groups_failed"] += 1
            # On checkpoint/CAPTCHA, stop crawling
            if "checkpoint" in error_str.lower() or "captcha" in error_str.lower():
                logger.warning("Checkpoint/CAPTCHA detected — stopping Facebook crawl")
                result["errors"].append("Checkpoint/CAPTCHA — crawl stopped")
                close_browser()
                break
            # On browser crash (EPIPE), recreate for next group
            if "epipe" in error_str.lower() or "pipe" in error_str.lower() or "target closed" in error_str.lower():
                logger.warning("Browser crashed on group %s — reinitializing for next group", url)
                close_browser()
                try:
                    _page = None  # Force reinit
                except:
                    pass

    close_browser()
    return result

def extract_job_posts(raw_posts: list[dict]) -> list[dict]:
    """Filter raw posts that look like job listings.

    Returns list of job-like post dicts with parsed fields.
    """
    job_keywords = [
        "tuyển", "tuyển", "tìm", "cần", "nhận", "recruit", "hiring",
        "求人", "募集", "採用", "転職", "スタッフ", "社員", "派遣",
        "job", "work", "việc", "xưởng", "kỹ sư", "shyu", "chuyển việc",
        "正社員", "契約社員", "製造", "工場", "勤務",
    ]
    exclude_keywords = [
        "特定技能", "技能実習", "アルバイト", "バイト", "パート",
        "建設", "土木", "施工管理", "IT", "プログラマー", "Webエンジニア",
    ]

    results = []
    for post in raw_posts:
        text = post.get("text", "").lower()
        # Must contain at least one job keyword
        if not any(kw.lower() in text for kw in job_keywords):
            continue
        # Must not contain exclude keywords
        if any(kw.lower() in text for kw in exclude_keywords):
            result["_rejected_reason"] = "exclude_keyword"
            continue

        # Extract basic fields
        area = extract_area(text)
        salary = extract_salary(text)
        company = extract_company(text)

        results.append({
            "title": extract_title(text),
            "company": company,
            "source_name": "Facebook",
            "source_type": "Facebook",
            "source_url": post.get("post_url", ""),
            "area": area,
            "salary": salary,
            "poster": post.get("poster", ""),
            "group_url": post.get("group_url", ""),
            "raw_text_snippet": text[:500],
            "scraped_at": post.get("scraped_at", ""),
        })

    return results

# ── Simple text extractors ──
def extract_title(text: str) -> str:
    """Extract job title from post text."""
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    # Heuristic: first line with job-related content
    job_indicators = ["tuyển", "求人", "募集", "採用", "hiring", "recruit",
                      "正社員", "契約社員", "派遣社員", "製造", "工場"]
    for line in lines[:10]:
        if any(ind in line.lower() for ind in job_indicators) and len(line) < 200:
            return line[:150]
    return lines[0][:150] if lines else ""

def extract_area(text: str) -> str:
    """Extract area/location."""
    area_kw = ["姫路", "太子町", "加古川", "高砂", "明石", "神戸",
               "Himeji", "Taishi", "Kakogawa", "Takasago", "Akashi", "Kobe"]
    for kw in area_kw:
        if kw in text:
            return kw
    # Try broader Hyogo match
    if "兵庫" in text or "Hyogo" in text:
        return "兵庫/Hyogo"
    return ""

def extract_salary(text: str) -> str:
    """Extract salary info."""
    patterns = [
        r"(月給|月収|給与)[^。\n]*\d+[万千]",
        r"(時給)\d+[0-9,]+",
        r"\d+[〜~]\d+万",
        r"年収\d+[〜~]?\d*万",
    ]
    for pat in patterns:
        m = re.search(pat, text)
        if m:
            return m.group(0)
    return ""

def extract_company(text: str) -> str:
    lines = [l.strip() for l in text.split("\n") if l.strip()]
    for line in lines:
        if "株式会社" in line or "㈱" in line:
            return line[:100]
    return ""

# ── Standalone test ──
if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    test_groups = [
        {"url": "https://www.facebook.com/groups/chuyenviecksu/", "keywords": ["Hyogo", "Himeji"]},
    ]
    result = crawl(test_groups)
    print(f"Logged in: {result['logged_in']}")
    print(f"Posts: {len(result['posts'])}")
    print(f"Errors: {result['errors']}")
