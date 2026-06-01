#!/usr/bin/env python3
"""Fetch SeaArt posts with Playwright, applying the visible filter sheet when possible."""
import json, os, re, sys, time
import re as re_module
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


def debug_probe_page(page, label):
    """Dump visible controls and filter-related text when debug mode is enabled."""
    if os.environ.get("SEAART_SAVE_DEBUG") != "1":
        return

    save_debug_screenshot(page, label)

    try:
        buttons = page.evaluate(
            """() => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' &&
                        style.display !== 'none' &&
                        box.width > 0 &&
                        box.height > 0;
                };

                return Array.from(document.querySelectorAll('button'))
                    .filter(visible)
                    .slice(0, 200)
                    .map((el) => {
                        const box = el.getBoundingClientRect();
                        return {
                            text: (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 120),
                            aria: el.getAttribute('aria-label') || '',
                            className: String(el.className || '').slice(0, 200),
                            box: {
                                x: Math.round(box.x),
                                y: Math.round(box.y),
                                width: Math.round(box.width),
                                height: Math.round(box.height),
                            },
                        };
                    });
            }"""
        )
        log(f"debug probe {label}: visible buttons={len(buttons)}")
        for button in buttons:
            log(
                "  BUTTON text='{text}' aria='{aria}' class='{className}' box={box}".format(
                    **button
                )
            )
    except Exception as e:
        log(f"debug button probe failed ({label}): {e}")

    try:
        matches = page.evaluate(
            """() => {
                const terms = ['Filter', 'Hot', 'Week', 'Sort'];
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' &&
                        style.display !== 'none' &&
                        box.width > 0 &&
                        box.height > 0;
                };

                return Array.from(document.querySelectorAll('body *'))
                    .filter((el) => visible(el) && terms.some((term) => (el.textContent || '').includes(term)))
                    .slice(0, 200)
                    .map((el) => {
                        const box = el.getBoundingClientRect();
                        return {
                            tag: el.tagName.toLowerCase(),
                            text: (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 160),
                            aria: el.getAttribute('aria-label') || '',
                            className: String(el.className || '').slice(0, 200),
                            box: {
                                x: Math.round(box.x),
                                y: Math.round(box.y),
                                width: Math.round(box.width),
                                height: Math.round(box.height),
                            },
                        };
                    });
            }"""
        )
        log(f"debug probe {label}: filter text elements={len(matches)}")
        for match in matches:
            log(
                "  TEXT tag={tag} text='{text}' aria='{aria}' class='{className}' box={box}".format(
                    **match
                )
            )
    except Exception as e:
        log(f"debug text probe failed ({label}): {e}")


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


def option_labels_for_period(period):
    if period == "day":
        log("period=day not available, falling back to week")
        period = "week"
    if period == "month":
        return period, ["Month", "Months", "Month(s)"]
    if period == "all":
        return period, ["All"]
    return period, [period.title()]


def filter_sheet_visible(page):
    """Best-effort check that a filter/drawer UI is now visible."""
    try:
        has_sort = any(
            page.get_by_text(label, exact=False).first.is_visible()
            for label in ["Hot", "New", "Recommended"]
        )
        has_period = any(
            page.get_by_text(label, exact=False).first.is_visible()
            for label in ["Week", "Month", "All"]
        )
        return has_sort and has_period
    except Exception:
        return False


def click_visible_filter_button(page):
    """Find and click a visible SeaArt filter button."""
    filter_btn = None

    try:
        candidate = page.get_by_role(
            "button", name=re_module.compile(r"Filter|フィルター", re_module.I)
        ).first
        if candidate.is_visible():
            filter_btn = candidate
            log("filter button found via role/name")
    except Exception as e:
        log(f"filter role/name selector did not match: {e}")

    if not filter_btn:
        try:
            candidates = page.get_by_text("Filter", exact=True)
            for i in range(min(candidates.count(), 10)):
                candidate = candidates.nth(i)
                if not candidate.is_visible():
                    continue
                btn = candidate.locator(
                    "xpath=ancestor-or-self::button | ancestor-or-self::*[contains(translate(@class, 'FILTER', 'filter'), 'filter')]"
                ).first
                if btn.is_visible():
                    filter_btn = btn
                    log("filter button found via text exact")
                    break
                filter_btn = candidate
                log("filter button found via direct text exact")
                break
        except Exception as e:
            log(f"filter text selector did not match: {e}")

    if not filter_btn:
        try:
            candidate = page.locator("button:has-text('Filter')").first
            if candidate.is_visible():
                filter_btn = candidate
                log("filter button found via button:has-text")
        except Exception as e:
            log(f"filter button:has-text selector did not match: {e}")

    if not filter_btn:
        try:
            viewport = page.viewport_size or {"width": 1440, "height": 900}
            all_btns = page.query_selector_all("button")
            for btn in all_btns:
                try:
                    box = btn.bounding_box()
                    if not box:
                        continue
                    if box["width"] >= 60 or box["height"] >= 60 or box["y"] >= 150:
                        continue
                    text = clean_text(btn.inner_text() or "")
                    aria = clean_text(btn.get_attribute("aria-label") or "")
                    if text and not re.search(r"filter|フィルター", text, re.I):
                        continue
                    if aria and not re.search(r"filter|フィルター", aria, re.I):
                        continue
                    log(f"  candidate button: text='{text}' aria='{aria}' box={box}")
                    if box["x"] > max(0, viewport["width"] - 110):
                        filter_btn = btn
                        log("filter button found via position: right-aligned small button")
                        break
                except Exception:
                    pass
        except Exception as e:
            log(f"filter positional selector failed: {e}")

    if not filter_btn:
        return False

    try:
        original_url = page.url
        save_debug_screenshot(page, "filter-before-click")
        filter_btn.click()
        page.wait_for_timeout(2000)
        save_debug_screenshot(page, "filter-after-click")
        if "/postDetail/" in page.url or page.url != original_url:
            log("filter candidate navigated away; returning to post feed")
            try:
                page.go_back(wait_until="domcontentloaded", timeout=15000)
                page.wait_for_selector(CARD_SELECTOR, timeout=10000)
            except Exception as e:
                log(f"returning after bad filter candidate failed: {e}")
            return False
        if filter_sheet_visible(page):
            log("filter button clicked and sheet is visible")
            return True
        log("filter button clicked but sheet visibility was not confirmed")
        return True
    except Exception as e:
        log(f"filter button click failed: {e}")
        return False


def click_filter_option(page, label, exact=True):
    try:
        options = page.get_by_text(label, exact=exact)
        for i in range(min(options.count(), 20)):
            option = options.nth(i)
            if not option.is_visible():
                continue
            option.click()
            page.wait_for_timeout(1000)
            return True
    except Exception as e:
        log(f"filter option selector failed label='{label}' error='{e}'")

    try:
        options = page.get_by_text(label, exact=False)
        visible_options = []
        pattern = re_module.compile(rf"(^|\b){re_module.escape(label)}(\b|$)", re_module.I)
        for i in range(min(options.count(), 50)):
            option = options.nth(i)
            if not option.is_visible():
                continue
            text = clean_text(option.inner_text())
            if not pattern.search(text):
                continue
            box = option.bounding_box()
            if not box:
                continue
            area = box["width"] * box["height"]
            visible_options.append((area, option, text, box))

        if visible_options:
            visible_options.sort(key=lambda item: item[0])
            _, option, text, box = visible_options[0]
            log(f"filter option found via text fallback label='{label}' text='{text}' box={box}")
            option.click()
            page.wait_for_timeout(1000)
            return True
    except Exception as e:
        log(f"filter option fallback failed label='{label}' error='{e}'")

    try:
        target = page.evaluate(
            """(label) => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' &&
                        style.display !== 'none' &&
                        box.width > 0 &&
                        box.height > 0;
                };
                const labelLower = label.toLowerCase();
                const containsLabel = (text) => text
                    .replace(/([a-z])([A-Z])/g, '$1 $2')
                    .split(/\\s+/)
                    .some((part) => part.toLowerCase() === labelLower);
                const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                let node;
                while ((node = walker.nextNode())) {
                    const text = node.nodeValue || '';
                    const index = text.toLowerCase().indexOf(labelLower);
                    if (index < 0) continue;
                    const parent = node.parentElement;
                    if (!parent || !visible(parent)) continue;
                    const range = document.createRange();
                    range.setStart(node, index);
                    range.setEnd(node, index + label.length);
                    const box = range.getBoundingClientRect();
                    range.detach();
                    if (box.width > 0 && box.height > 0) {
                        return {
                            text: text.trim().replace(/\\s+/g, ' ').slice(0, 120),
                            x: box.x + box.width / 2,
                            y: box.y + box.height / 2,
                            width: box.width,
                            height: box.height,
                            area: box.width * box.height,
                        };
                    }
                }
                const candidates = Array.from(document.querySelectorAll('body *'))
                    .filter((el) => visible(el) && containsLabel((el.textContent || '').trim()))
                    .map((el) => {
                        const box = el.getBoundingClientRect();
                        return {
                            text: (el.textContent || '').trim().replace(/\\s+/g, ' ').slice(0, 120),
                            x: box.x + box.width / 2,
                            y: box.y + box.height / 2,
                            width: box.width,
                            height: box.height,
                            area: box.width * box.height,
                        };
                    })
                    .filter((item) => item.area > 0 && item.area < 50000)
                    .sort((a, b) => a.area - b.area);
                return candidates[0] || null;
            }""",
            label,
        )
        if target:
            log(
                "filter option found via DOM fallback label='{label}' text='{text}' box={box}".format(
                    label=label,
                    text=target["text"],
                    box={
                        "x": round(target["x"] - target["width"] / 2),
                        "y": round(target["y"] - target["height"] / 2),
                        "width": round(target["width"]),
                        "height": round(target["height"]),
                    },
                )
            )
            page.mouse.click(target["x"], target["y"])
            page.wait_for_timeout(1000)
            return True
    except Exception as e:
        log(f"filter option DOM fallback failed label='{label}' error='{e}'")

    return False


def apply_filters(page, context=None):
    """Try to open SeaArt filter sheet and select sort/period options.
    Returns dict with status fields."""
    result = {
        "filter_sheet_opened": False,
        "sort_applied": False,
        "period_applied": False,
        "sort_value": None,
        "period_value": None,
    }

    save_debug_screenshot(page, "filter-before")
    debug_probe_page(page, "filter-probe-before")

    if click_visible_filter_button(page):
        result["filter_sheet_opened"] = True
        log("filter_sheet_opened=true")
        debug_probe_page(page, "filter-probe-after-open")
    else:
        log("filter_sheet_opened=false fallback_default_feed=true reason='filter button not found'")
        return result

    sort_label = SEAART_SORT.title()
    if click_filter_option(page, sort_label, exact=True):
        result["sort_applied"] = True
        result["sort_value"] = SEAART_SORT
        log(f"sort option selected: {sort_label}")
        save_debug_screenshot(page, "filter-after-sort")
    else:
        log(f"sort option not visible in filter sheet: {sort_label}")

    period_value, period_labels = option_labels_for_period(SEAART_PERIOD)
    for period_label in period_labels:
        if click_filter_option(page, period_label, exact=True):
            result["period_applied"] = True
            result["period_value"] = period_value
            log(f"period option selected: {period_label}")
            save_debug_screenshot(page, "filter-after-period")
            break

    if not result["period_applied"]:
        log(f"period option not visible in filter sheet: {period_value}")

    try:
        page.wait_for_selector(CARD_SELECTOR, timeout=10000)
        page.wait_for_timeout(1500)
        log("cards available after filter step")
    except Exception as e:
        log(f"cards did not reload after filter step: {e}")

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
        "candidates={candidates} items={items} images={images} titles={titles} authors={authors} "
        "likes_nonzero={likes_nonzero} top_likes={top_likes} "
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


def new_mobile_context(browser):
    return browser.new_context(
        user_agent=(
            "Mozilla/5.0 (iPhone; CPU iPhone OS 17_0 like Mac OS X) "
            "AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.0 "
            "Mobile/15E148 Safari/604.1"
        ),
        viewport={"width": 390, "height": 844},
        is_mobile=True,
        has_touch=True,
    )


def collect_results_from_page(page, count, pool_size):
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
    return raw_cards, results[:count], selector_matches, candidates


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
    artifact_page = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            context = browser.new_context(
                user_agent='Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                viewport={'width': 1440, 'height': 900}
            )
            page = context.new_page()
            artifact_page = page
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

                debug_probe_page(page, f"desktop-after-load-{attempt}")
                filter_status = apply_filters(page, context)
                extraction_page = page

                if not filter_status.get("filter_sheet_opened"):
                    log("desktop filter failed; retrying with mobile browser context")
                    try:
                        mobile_context = new_mobile_context(browser)
                        mobile_page = mobile_context.new_page()
                        log("loading trending page in mobile context")
                        mobile_page.goto(SEAART_POST_URL, wait_until='domcontentloaded', timeout=30000)
                        mobile_page.wait_for_selector(CARD_SELECTOR, timeout=15000)
                        mobile_page.wait_for_timeout(1500)
                        debug_probe_page(mobile_page, f"mobile-after-load-{attempt}")
                        mobile_filter_status = apply_filters(mobile_page, mobile_context)
                        if mobile_filter_status.get("filter_sheet_opened"):
                            log("mobile filter opened successfully; extracting from mobile page")
                            filter_status = mobile_filter_status
                            extraction_page = mobile_page
                            artifact_page = mobile_page
                        else:
                            log("mobile filter failed; falling back to default desktop feed")
                            mobile_context.close()
                    except Exception as e:
                        log(f"mobile context filter retry failed: {e}")

                try:
                    raw_cards, results, selector_matches, candidates = collect_results_from_page(
                        extraction_page, count, pool_size
                    )
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
            top3 = sorted([x.get('likes', 0) for x in results], reverse=True)[:3]
            metrics["top_likes"] = top3
            metrics["likes_nonzero"] = sum(1 for x in results if x.get('likes', 0) > 0)
            _LAST_METRICS = metrics
            log_metrics(metrics)

            if os.environ.get("SEAART_SAVE_DEBUG") == "1" or not results:
                try:
                    html = artifact_page.content()
                except Exception:
                    html = ""
                try:
                    screenshot = artifact_page.screenshot(full_page=True)
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
