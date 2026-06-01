#!/usr/bin/env python3
"""Fetch SeaArt trending posts by scraping the rendered DOM with Playwright."""
import json, os, re, sys, time
from pathlib import Path
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright


SEAART_POST_URL = "https://seaart.ai/post"
DEBUG_DIR = Path(os.environ.get("SEAART_DEBUG_DIR", "/tmp/hermes_debug/seaart"))

CARD_SELECTOR = 'a[href*="postDetail"]'
IMG_SELECTOR = "img"
TITLE_SELECTOR = '[class*="title"], [class*="Title"], [class*="name"], [class*="Name"]'
AUTHOR_SELECTOR = '[class*="author"], [class*="Author"], [class*="user"], [class*="User"]'
DETAIL_URL_REGEX = re.compile(r"/postDetail/([^?\s\"'<]+)")

_LAST_METRICS = {}


def log(msg):
    print(f"SeaArt: {msg}", file=sys.stderr)


def clean_text(value):
    return re.sub(r"\s+", " ", str(value or "")).strip()


def derive_title(text):
    text = clean_text(text)
    if not text:
        return ""
    for part in re.split(r"(?<=[.!?])\s+|\n+", text):
        part = clean_text(part)
        if 3 < len(part) < 100:
            return part
    return text[:99]


def first_src_from_srcset(srcset):
    if not srcset:
        return ""
    first = srcset.split(",", 1)[0].strip()
    return first.split(" ", 1)[0].strip()


def parse_int(value):
    if value is None:
        return 0
    text = str(value).replace(",", "").strip().lower()
    multiplier = 1
    if text.endswith("k"):
        multiplier = 1_000
        text = text[:-1]
    elif text.endswith("m"):
        multiplier = 1_000_000
        text = text[:-1]
    try:
        return int(float(text) * multiplier)
    except ValueError:
        return 0


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


def extract_cards(page, limit):
    """Extract raw post card data from the rendered page."""
    return page.evaluate(
        """({limit, cardSelector, imgSelector, titleSelector, authorSelector}) => {
            const cards = [];
            const links = document.querySelectorAll(cardSelector);

            for (const link of links) {
                if (cards.length >= limit) break;

                const img = link.querySelector(imgSelector);
                const titleEl = link.querySelector(titleSelector);
                const authorEl = link.querySelector(authorSelector);
                const textParts = [];
                for (const el of link.querySelectorAll('span, p, div')) {
                    const text = (el.textContent || '').trim();
                    if (text) textParts.push(text);
                }

                cards.push({
                    href: link.getAttribute('href') || '',
                    title: titleEl ? (titleEl.textContent || '').trim() : '',
                    author: authorEl ? (authorEl.textContent || '').trim() : '',
                    image_src: img ? (img.getAttribute('src') || '') : '',
                    image_data_src: img ? (img.getAttribute('data-src') || '') : '',
                    image_srcset: img ? (img.getAttribute('srcset') || '') : '',
                    image_alt: img ? (img.getAttribute('alt') || '') : '',
                    image_width: img ? (parseInt(img.getAttribute('width') || '0') || 0) : 0,
                    image_height: img ? (parseInt(img.getAttribute('height') || '0') || 0) : 0,
                    text: textParts.join(' '),
                });
            }

            return cards;
        }""",
        {
            "limit": limit,
            "cardSelector": CARD_SELECTOR,
            "imgSelector": IMG_SELECTOR,
            "titleSelector": TITLE_SELECTOR,
            "authorSelector": AUTHOR_SELECTOR,
        },
    )


def normalize_item(raw):
    """Normalize a raw card into the core SeaArt item fields."""
    href = raw.get("href") or raw.get("url") or ""
    match = DETAIL_URL_REGEX.search(href)
    if not match:
        return None

    post_id = match.group(1)
    image_url = (
        raw.get("image_url")
        or raw.get("image_src")
        or raw.get("image_data_src")
        or first_src_from_srcset(raw.get("image_srcset") or "")
        or ""
    )
    if image_url:
        image_url = urljoin(SEAART_POST_URL, image_url)

    title = clean_text(raw.get("title") or raw.get("image_alt") or "")
    if not title:
        title = derive_title(raw.get("text") or "")

    return {
        "id": post_id,
        "title": title,
        "author": clean_text(raw.get("author") or ""),
        "image_url": image_url,
        "url": f"https://seaart.ai/postDetail/{post_id}",
        "likes": parse_int(raw.get("likes")),
        "views": parse_int(raw.get("views")),
        "collections": parse_int(raw.get("collections")),
        "created_at": raw.get("created_at") or 0,
    }


def output_item(item, raw=None):
    raw = raw or {}
    return {
        "id": item["id"],
        "title": item["title"],
        "author": item["author"],
        "author_avatar": "",
        "image_url": cover_to_file_url(item["image_url"]) if item["image_url"] else "",
        "image_width": raw.get("image_width") or 0,
        "image_height": raw.get("image_height") or 0,
        "likes": item["likes"],
        "views": item["views"],
        "collections": item["collections"],
        "tags": [],
        "sub_channel": "",
        "created_at": item["created_at"],
        "nsfw_level": 0,
        "url": item["url"],
    }


def dedupe_items(raw_cards, limit):
    seen = set()
    items = []
    for raw in raw_cards:
        item = normalize_item(raw)
        if not item or item["id"] in seen:
            continue
        seen.add(item["id"])
        items.append(output_item(item, raw))
        if len(items) >= limit:
            break
    return items


def metric_counts(raw_cards, items, selector_matches=None):
    return {
        "selector_matches": len(raw_cards) if selector_matches is None else selector_matches,
        "items": len(items),
        "images": sum(1 for item in items if item.get("image_url")),
        "titles": sum(1 for item in items if item.get("title")),
        "authors": sum(1 for item in items if item.get("author")),
    }


def log_metrics(metrics):
    print(
        "[SeaArt] selector_matches={selector_matches} items={items} "
        "images={images} titles={titles} authors={authors} "
        "runtime={runtime:.1f}s retries={retries}".format(**metrics),
        file=sys.stderr,
    )


def save_debug_artifacts(results, html, screenshot, error):
    """Save debug artifacts when requested or when extraction fails."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    if html:
        (DEBUG_DIR / "seaart-post.html").write_text(html, encoding="utf-8")
    if screenshot:
        (DEBUG_DIR / "seaart-post.png").write_bytes(screenshot)
    (DEBUG_DIR / "seaart-metrics.json").write_text(
        json.dumps({**_LAST_METRICS, "error": error or ""}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    (DEBUG_DIR / "seaart-raw-cards.json").write_text(
        json.dumps(results, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if error:
        (DEBUG_DIR / "seaart-error.txt").write_text(str(error), encoding="utf-8")


def fetch_trending(count=15):
    """Fetch trending posts from SeaArt by scraping rendered DOM cards."""
    global _LAST_METRICS
    started = time.perf_counter()
    retries = 0
    raw_cards = []
    results = []
    error = ""
    selector_matches = 0

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1440, 'height': 900}
            )
            page = context.new_page()
            for attempt in range(3):
                retries = attempt
                if attempt == 0:
                    log("loading trending page")
                    page.goto(SEAART_POST_URL, wait_until='domcontentloaded', timeout=30000)
                else:
                    log(f"no results, retrying page load ({attempt}/2)")
                    page.reload(wait_until='domcontentloaded', timeout=30000)

                try:
                    page.wait_for_selector(CARD_SELECTOR, timeout=15000)
                except Exception as e:
                    error = f"post cards did not appear: {e}"
                    log(error)

                try:
                    raw_cards = extract_cards(page, count)
                    selector_matches = page.locator(CARD_SELECTOR).count()
                    results = dedupe_items(raw_cards, count)

                    if len(results) < count / 2:
                        for _ in range(2):
                            page.mouse.wheel(0, 900)
                            page.wait_for_timeout(500)
                            raw_cards = extract_cards(page, count)
                            selector_matches = page.locator(CARD_SELECTOR).count()
                            results = dedupe_items(raw_cards, count)
                            if len(results) >= count:
                                break
                except Exception as e:
                    error = f"DOM evaluation failed: {e}"
                    log(error)
                    raw_cards = []
                    results = []

                if results:
                    log(f"found {len(results)} posts")
                    break

            metrics = {
                **metric_counts(raw_cards, results, selector_matches),
                "runtime": round(time.perf_counter() - started, 3),
                "retries": retries,
            }
            _LAST_METRICS = metrics
            log_metrics(metrics)

            if os.environ.get("SEAART_SAVE_DEBUG") == "1" or not results:
                try:
                    html = page.content()
                except Exception:
                    html = ""
                try:
                    screenshot = page.screenshot(full_page=True)
                except Exception:
                    screenshot = b""
                save_debug_artifacts(raw_cards, html, screenshot, error or ("0 usable results" if not results else ""))

            return results

        finally:
            browser.close()


if __name__ == '__main__':
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    results = fetch_trending(count)
    print(json.dumps(results, ensure_ascii=False, indent=2))
