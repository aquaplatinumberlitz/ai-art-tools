#!/usr/bin/env python3
"""Fetch SeaArt trending posts by scraping the rendered DOM with Playwright."""
import json, os, re, sys, time
from pathlib import Path
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright


SEAART_POST_URL = "https://seaart.ai/post"
DEBUG_DIR = Path(os.environ.get("SEAART_DEBUG_DIR", "/tmp/hermes_debug/seaart"))

_LAST_METRICS = {}


def log(msg):
    print(f"SeaArt: {msg}", file=sys.stderr)


SEAART_SORT = os.environ.get("SEAART_SORT", "hot")
SEAART_PERIOD = os.environ.get("SEAART_PERIOD", "week")

VALID_SORTS = {"recommended", "hot", "new"}
VALID_PERIODS = {"day", "week", "month", "all"}

if SEAART_SORT not in VALID_SORTS:
    log(f"unsupported sort '{SEAART_SORT}', falling back to 'hot'")
    SEAART_SORT = "hot"

if SEAART_PERIOD not in VALID_PERIODS:
    log(f"unsupported period '{SEAART_PERIOD}', falling back to 'week'")
    SEAART_PERIOD = "week"

try:
    SEAART_POOL_SIZE = int(os.environ.get("SEAART_POOL_SIZE", "20"))
except (ValueError, TypeError):
    SEAART_POOL_SIZE = 20
    log("invalid SEAART_POOL_SIZE, using default 20")

CARD_SELECTOR = 'a[href*="postDetail"]'
IMG_SELECTOR = "img"
TITLE_SELECTOR = '[class*="title"], [class*="Title"], [class*="name"], [class*="Name"]'
AUTHOR_SELECTOR = '[class*="author"], [class*="Author"], [class*="user"], [class*="User"]'
DETAIL_URL_REGEX = re.compile(r"/postDetail/([^?\s\"'<]+)")


def save_debug_screenshot(page, name):
    """Save a named screenshot when SEAART_SAVE_DEBUG is enabled."""
    if os.environ.get("SEAART_SAVE_DEBUG") != "1":
        return
    try:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(DEBUG_DIR / f"seaart-{name}.png"), full_page=True)
    except Exception as e:
        log(f"debug screenshot failed ({name}): {e}")


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


def split_author_metrics(author_text):
    """Split author name from trailing metric numbers."""
    author_text = str(author_text or "")
    parts = author_text.strip().split()
    if len(parts) < 2:
        return author_text, []

    metrics = []
    i = len(parts) - 1
    while i >= 0 and parts[i].isdigit():
        metrics.append(int(parts[i]))
        i -= 1

    if not metrics:
        return author_text, []

    clean_author = " ".join(parts[:i + 1])
    metrics.reverse()

    return clean_author, metrics


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


def matching_option(page, labels):
    """Find a visible option-like element whose text contains one of the labels."""
    selectors = []
    for label in labels:
        selectors.extend([
            f'text="{label}"',
            f'[class*="option" i]:has-text("{label}")',
            f'[class*="item" i]:has-text("{label}")',
            f'button:has-text("{label}")',
            f'div:has-text("{label}")',
            f'span:has-text("{label}")',
        ])

    for selector in selectors:
        try:
            locator = page.locator(selector)
            for i in range(min(locator.count(), 25)):
                option = locator.nth(i)
                if option.is_visible():
                    return option
        except Exception:
            continue
    return None


def option_labels_for_period(period):
    if period == "day":
        log("period=day not available, falling back to week")
        period = "week"
    if period == "month":
        return period, ["Month", "Months", "Month(s)"]
    if period == "all":
        return period, ["All"]
    return period, [period.title()]


def clickable_filter_button(handle):
    """Return the nearest button handle for a matched filter icon/element."""
    try:
        if handle.evaluate("el => el.tagName && el.tagName.toLowerCase() === 'button'"):
            return handle
        button = handle.evaluate_handle("el => el.closest && el.closest('button')")
        if button:
            element = button.as_element()
            if element:
                return element
    except Exception:
        pass
    return handle


def filter_sheet_visible(page):
    """Best-effort check that a filter/drawer UI is now visible."""
    try:
        sheet = page.locator(
            '[role="dialog"], [class*="drawer" i], [class*="sheet" i], '
            '[class*="popup" i], [class*="modal" i]'
        )
        for i in range(min(sheet.count(), 10)):
            if sheet.nth(i).is_visible():
                return True
    except Exception:
        pass

    try:
        has_sort = any(
            page.get_by_text(label, exact=False).first().is_visible()
            for label in ["Hot", "New", "Recommended"]
        )
        has_period = any(
            page.get_by_text(label, exact=False).first().is_visible()
            for label in ["Week", "Month", "All"]
        )
        return has_sort and has_period
    except Exception:
        return False


def try_click_filter_icon(page, selectors, mobile=False):
    for sel in selectors:
        try:
            for handle in page.query_selector_all(sel)[:8]:
                btn = clickable_filter_button(handle)
                box = btn.bounding_box()
                if not box:
                    continue
                if box["width"] >= 60 or box["height"] >= 60:
                    continue
                if not mobile and box["y"] > 260:
                    continue

                original_url = page.url
                btn.click()
                page.wait_for_timeout(1500)

                if "/postDetail/" in page.url or page.url != original_url:
                    log(f"filter candidate navigated away via selector: {sel}")
                    try:
                        page.go_back(wait_until="domcontentloaded", timeout=15000)
                        page.wait_for_selector(CARD_SELECTOR, timeout=10000)
                    except Exception as e:
                        log(f"returning after bad filter candidate failed: {e}")
                    continue

                if filter_sheet_visible(page):
                    prefix = "filter sheet opened (mobile)" if mobile else "filter sheet opened"
                    log(f"{prefix} via selector: {sel}")
                    return True

                log(f"filter candidate did not expose sheet via selector: {sel}")
        except Exception as e:
            log(f"filter selector failed sel='{sel}' error='{e}'")
    return False


def apply_filters(page):
    """Try to open filter sheet and select sort/period options. Returns dict with status."""
    result = {
        "filter_sheet_opened": False,
        "sort_applied": False,
        "period_applied": False,
        "sort_value": None,
        "period_value": None,
    }

    filter_selectors = [
        'button svg[viewBox*="filter"], button svg[viewBox*="Filter"]',
        'button[aria-label*="filter" i], button[aria-label*="Filter" i]',
        'button:has(svg[viewBox*="filter"]), button:has(svg[viewBox*="Filter"])',
        '[class*="filter" i] button, button[class*="filter" i]',
        'button:has(svg)',
        'section button:last-of-type',
    ]

    filter_clicked = False
    viewport_changed = False

    save_debug_screenshot(page, "filter-before-open")

    try:
        filter_clicked = try_click_filter_icon(page, filter_selectors)
        if filter_clicked:
            result["filter_sheet_opened"] = True
            save_debug_screenshot(page, "filter-sheet-open")
    except Exception as e:
        log(f"filter icon click attempt failed: {e}")

    if not filter_clicked:
        try:
            log("trying mobile viewport for filter access")
            page.set_viewport_size({"width": 430, "height": 932})
            viewport_changed = True
            page.reload(wait_until="domcontentloaded", timeout=30000)
            page.wait_for_selector(CARD_SELECTOR, timeout=15000)
            page.wait_for_timeout(2000)
            save_debug_screenshot(page, "filter-mobile-before-open")

            filter_clicked = try_click_filter_icon(page, filter_selectors, mobile=True)
            if filter_clicked:
                result["filter_sheet_opened"] = True
                save_debug_screenshot(page, "filter-mobile-sheet-open")
        except Exception as e:
            log(f"mobile viewport filter attempt failed: {e}")

    if filter_clicked:
        try:
            sort_target = SEAART_SORT.title()
            option = matching_option(page, [sort_target])
            if option:
                option.click()
                page.wait_for_timeout(1000)
                result["sort_applied"] = True
                result["sort_value"] = SEAART_SORT
                log(f"sort option selected: {SEAART_SORT}")
                save_debug_screenshot(page, "filter-after-sort")
            else:
                log(f"sort option not found in filter sheet: {SEAART_SORT}")
        except Exception as e:
            log(f"sort selection failed: {e}")

        try:
            period_value, period_labels = option_labels_for_period(SEAART_PERIOD)
            option = matching_option(page, period_labels)
            if option:
                option.click()
                page.wait_for_timeout(1000)
                result["period_applied"] = True
                result["period_value"] = period_value
                log(f"period option selected: {period_value}")
                save_debug_screenshot(page, "filter-after-period")
            else:
                log(f"period option not found in filter sheet: {period_value}")
        except Exception as e:
            log(f"period selection failed: {e}")

        try:
            page.wait_for_selector(CARD_SELECTOR, timeout=10000)
            page.wait_for_timeout(1500)
            log("cards available after filter step")
        except Exception as e:
            log(f"cards did not reload after filter step: {e}")
    else:
        log("filter_sheet_opened=false fallback_default_feed=true reason='filter button not found'")

    if viewport_changed:
        try:
            page.set_viewport_size({"width": 1440, "height": 900})
            page.wait_for_timeout(500)
            log("restored desktop viewport after filter attempt")
        except Exception as e:
            log(f"desktop viewport restore failed: {e}")

    return result


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

    author, author_metrics = split_author_metrics(clean_text(raw.get("author") or ""))
    likes = parse_int(raw.get("likes"))
    views = parse_int(raw.get("views"))
    if author_metrics:
        likes = author_metrics[0]
        if len(author_metrics) >= 2:
            views = author_metrics[1]

    return {
        "id": post_id,
        "title": title,
        "author": author,
        "image_url": image_url,
        "url": f"https://seaart.ai/postDetail/{post_id}",
        "likes": likes,
        "views": views,
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


def metric_counts(raw_cards, items, selector_matches=None, candidates=None, filter_status=None):
    filter_status = filter_status or {}
    return {
        "sort": SEAART_SORT,
        "period": SEAART_PERIOD,
        "filter_sheet_opened": filter_status.get("filter_sheet_opened", False),
        "sort_applied": filter_status.get("sort_applied", False),
        "period_applied": filter_status.get("period_applied", False),
        "pool_size": SEAART_POOL_SIZE,
        "candidates": len(items) if candidates is None else candidates,
        "selector_matches": len(raw_cards) if selector_matches is None else selector_matches,
        "items": len(items),
        "images": sum(1 for item in items if item.get("image_url")),
        "titles": sum(1 for item in items if item.get("title")),
        "authors": sum(1 for item in items if item.get("author")),
        "authors_cleaned": sum(1 for raw in raw_cards if split_author_metrics(raw.get("author") or "")[1]),
    }


def log_metrics(metrics):
    print(
        "[SeaArt] sort={sort} period={period} filter_sheet_opened={filter_sheet_opened} "
        "sort_applied={sort_applied} period_applied={period_applied} pool={pool_size} "
        "candidates={candidates} items={items} "
        "images={images} titles={titles} authors={authors} authors_cleaned={authors_cleaned} "
        "runtime={runtime:.1f}s retries={retries}".format(**metrics),
        file=sys.stderr,
    )


def save_debug_artifacts(results, html, screenshot, error, filter_status=None):
    """Save debug artifacts when requested or when extraction fails."""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    if html:
        (DEBUG_DIR / "seaart-post.html").write_text(html, encoding="utf-8")
    if screenshot:
        (DEBUG_DIR / "seaart-post.png").write_bytes(screenshot)
    (DEBUG_DIR / "seaart-metrics.json").write_text(
        json.dumps(
            {**_LAST_METRICS, "filter_status": filter_status or {}, "error": error or ""},
            ensure_ascii=False,
            indent=2,
        ),
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
    pool_size = SEAART_POOL_SIZE
    raw_cards = []
    results = []
    candidates = 0
    error = ""
    selector_matches = 0
    filter_status = {
        "filter_sheet_opened": False,
        "sort_applied": False,
        "period_applied": False,
        "sort_value": None,
        "period_value": None,
    }

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

                filter_status = apply_filters(page)

                try:
                    raw_cards = extract_cards(page, pool_size)
                    selector_matches = page.locator(CARD_SELECTOR).count()
                    results = dedupe_items(raw_cards, pool_size)
                    candidates = len(results)

                    if len(results) < count / 2:
                        for _ in range(2):
                            page.mouse.wheel(0, 900)
                            page.wait_for_timeout(500)
                            raw_cards = extract_cards(page, pool_size)
                            selector_matches = page.locator(CARD_SELECTOR).count()
                            results = dedupe_items(raw_cards, pool_size)
                            candidates = len(results)
                            if len(results) >= min(count, pool_size):
                                break

                    results.sort(key=lambda x: (x.get('likes', 0), x.get('views', 0)), reverse=True)
                    results = results[:count]
                except Exception as e:
                    error = f"DOM evaluation failed: {e}"
                    log(error)
                    raw_cards = []
                    results = []
                    candidates = 0

                if results:
                    log(f"found {len(results)} posts")
                    break

            metrics = {
                **metric_counts(raw_cards, results, selector_matches, candidates, filter_status),
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
                save_debug_artifacts(
                    raw_cards,
                    html,
                    screenshot,
                    error or ("0 usable results" if not results else ""),
                    filter_status,
                )

            return results

        finally:
            browser.close()


if __name__ == '__main__':
    count = int(sys.argv[1]) if len(sys.argv) > 1 else 20
    results = fetch_trending(count)
    print(json.dumps(results, ensure_ascii=False, indent=2))
