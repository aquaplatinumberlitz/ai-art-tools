#!/usr/bin/env python3
"""Fetch SeaArt posts with Playwright, applying the visible filter sheet when possible."""
import json, os, re, sys, time
import re as re_module
from pathlib import Path
from urllib.parse import urljoin
from playwright.sync_api import sync_playwright


SEAART_POST_URL = "https://www.seaart.ai/post?sort=hot&period=week"
DEBUG_DIR = Path(os.environ.get("SEAART_DEBUG_DIR", "/tmp/hermes_debug/seaart"))

_LAST_METRICS = {}
_LAST_EXTRACTION_METRICS = {}
_LAST_EXTRACTION_CANDIDATES = []


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
    metric_re = r"(\d+(?:[.,]\d+)?[KMBkmb]?)"

    parts = author_text.strip().split()
    if len(parts) < 2:
        # Single token: try compact regex for "Oosti1.5K517" pattern
        match = re.match(
            rf"^(?P<author>.*?)(?P<first>{metric_re})\s*(?P<second>{metric_re})$",
            author_text.strip(),
        )
        if match and match.group("author").strip():
            return match.group("author").strip(), [
                parse_int(match.group("first")),
                parse_int(match.group("second")),
            ]
        return author_text, []

    metrics = []
    i = len(parts) - 1
    while i >= 0 and parts[i].isdigit():
        metrics.append(int(parts[i]))
        i -= 1

    if not metrics or re.search(r"[KMBkmb]", author_text):
        # Fallback: use regex for compact K/M values that .isdigit() can't parse
        # Examples: "Oosti1.5K517" (no space), "Oosti 1.5K 517" (space)
        match = re.match(
            rf"^(?P<author>.*?)(?P<first>{metric_re})\s*(?P<second>{metric_re})$",
            author_text,
        )
        if match:
            clean_author = match.group("author").strip()
            if clean_author:
                return clean_author, [
                    parse_int(match.group("first")),
                    parse_int(match.group("second")),
                ]
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
    global _LAST_EXTRACTION_METRICS, _LAST_EXTRACTION_CANDIDATES
    payload = page.evaluate(
        """({limit, cardSelector, imgSelector, titleSelector, authorSelector}) => {
            const cards = [];
            const diagnostics = [];
            const metrics = {
                global_post_links: 0,
                toolbar_boundary_y: 0,
                accepted_main_feed: 0,
                excluded_above_toolbar: 0,
                excluded_upper_section: 0,
                excluded_too_small: 0,
                excluded_inside_filter_box: 0,
            };

            const visible = (el) => {
                const style = window.getComputedStyle(el);
                const box = el.getBoundingClientRect();
                return style.visibility !== 'hidden' &&
                    style.display !== 'none' &&
                    box.width > 0 &&
                    box.height > 0;
            };

            const normalizeText = (el) => (el?.innerText || el?.textContent || '')
                .trim()
                .replace(/\\s+/g, ' ');

            const isUpperSection = (link) => {
                const box = link.getBoundingClientRect();
                const absTop = box.top + window.scrollY;
                if (absTop < 400) return true;

                const upperPattern = /creative\\s+featured|my\\s+community\\s+milestones?|milestones?|achievement|achieving|followers|thành\\s*tựu|cộng\\s*đồng|nổi bật|sáng tạo|community/i;
                let el = link.parentElement;
                let depth = 0;
                while (el && el !== document.body && depth < 5) {
                    const box = el.getBoundingClientRect();
                    if (box.height > 2000 || box.width === window.innerWidth) {
                        el = el.parentElement;
                        depth++;
                        continue;
                    }
                    const text = normalizeText(el);
                    if (upperPattern.test(text)) {
                        return true;
                    }
                    el = el.parentElement;
                    depth++;
                }
                return false;
            };

            const insideFilterBox = (link) => {
                let el = link.parentElement;
                while (el && el !== document.body) {
                    const box = el.getBoundingClientRect();
                    const className = String(el.className || '');
                    const text = normalizeText(el);
                    if (/(^|\\s)(my-)?filter-form-box(\\s|$)|right-filter-box|select-filter-box|filter-box|popover|drawer/i.test(className) &&
                        /filter|sort\\s*by|time\\s*range|apply|confirm/i.test(text) &&
                        box.height < 1200) {
                        return true;
                    }
                    el = el.parentElement;
                }
                return false;
            };

            const hasUpperSectionParentText = (link) => {
                const upperPattern = /creative\\s+featured|my\\s+community\\s+milestones?|campaign\\s+featured|achievement|milestone/i;
                const toolbarPattern = /Trending|Seedance|GPT image|Featured Topics|Short Film|Pro Tips|Viral Clips|\\bHot\\b|\\bFilter\\b/i;
                let el = link.parentElement;
                let depth = 0;
                while (el && el !== document.body && depth < 8) {
                    const box = el.getBoundingClientRect();
                    const text = normalizeText(el);
                    if (box.height < 2200 && upperPattern.test(text) && !toolbarPattern.test(text)) {
                        return true;
                    }
                    el = el.parentElement;
                    depth++;
                }
                return false;
            };

            const findFeedBoundaryY = () => {
                const toolbarTerms = [
                    'Trending',
                    'Seedance',
                    'GPT image',
                    'Featured Topics',
                    'Short Film',
                    'Pro Tips',
                    'Viral Clips',
                    'Hot',
                    'Filter',
                ];
                const popoverSelector = '.el-popover, .el-popper, .hy-filter-popover, [role="tooltip"]';
                const isToolbarCandidate = (el) => {
                    if (!el || !visible(el)) return false;
                    if (el.closest(popoverSelector)) return false;
                    if (el.closest('footer')) return false;
                    if (el.closest('a[href*="postDetail"]')) return false;
                    const box = el.getBoundingClientRect();
                    if (box.height < 20 || box.height > 180 || box.width < 80) return false;
                    const text = normalizeText(el);
                    const hits = toolbarTerms.filter(term => text.toLowerCase().includes(term.toLowerCase())).length;
                    return hits >= 2 || (/\\b(Hot|Filter)\\b/i.test(text) && hits >= 1);
                };

                const classCandidates = Array.from(document.querySelectorAll('.my-filter-form-box .filter-form-box, .filter-form-box'))
                    .filter(isToolbarCandidate)
                    .sort((a, b) => {
                        const ab = a.getBoundingClientRect();
                        const bb = b.getBoundingClientRect();
                        return (ab.top + window.scrollY) - (bb.top + window.scrollY);
                    });
                if (classCandidates.length) {
                    const tb = classCandidates[0].getBoundingClientRect();
                    return Math.round(tb.bottom + window.scrollY + 10);
                }

                const termNodes = Array.from(document.querySelectorAll('button, [role="button"], a, span, div'))
                    .filter((el) => {
                        if (!visible(el)) return false;
                        if (el.closest(popoverSelector)) return false;
                        if (el.closest('footer')) return false;
                        if (el.closest('a[href*="postDetail"]')) return false;
                        const text = normalizeText(el);
                        const box = el.getBoundingClientRect();
                        return toolbarTerms.some(term => text.toLowerCase().includes(term.toLowerCase())) &&
                            text.length <= 180 &&
                            box.height > 0 &&
                            box.height <= 90 &&
                            box.width >= 20;
                    });
                const containers = [];
                for (const node of termNodes) {
                    let el = node;
                    let depth = 0;
                    while (el && el !== document.body && depth < 6) {
                        if (isToolbarCandidate(el)) {
                            containers.push(el);
                            break;
                        }
                        el = el.parentElement;
                        depth++;
                    }
                }
                const unique = Array.from(new Set(containers))
                    .sort((a, b) => {
                        const ab = a.getBoundingClientRect();
                        const bb = b.getBoundingClientRect();
                        const ay = ab.top + window.scrollY;
                        const by = bb.top + window.scrollY;
                        return ay - by || (ab.width * ab.height) - (bb.width * bb.height);
                    });
                if (unique.length) {
                    const tb = unique[0].getBoundingClientRect();
                    return Math.round(tb.bottom + window.scrollY + 10);
                }

                return 0;
            };

            const links = Array.from(document.querySelectorAll(cardSelector));
            metrics.global_post_links = links.length;
            const yBands = {above_200: 0, "200_500": 0, "500_1000": 0, "1000_1500": 0, above_1500: 0};
            for (const link of links) {
                const top = link.getBoundingClientRect().top + window.scrollY;
                if (top < 200) yBands.above_200++;
                else if (top < 500) yBands["200_500"]++;
                else if (top < 1000) yBands["500_1000"]++;
                else if (top < 1500) yBands["1000_1500"]++;
                else yBands.above_1500++;
            }
            metrics.toolbar_boundary_y = findFeedBoundaryY();

            for (const link of links) {
                const rect = link.getBoundingClientRect();
                const absTop = rect.top + window.scrollY;
                let included = false;
                let excludeReason = "";

                if (!metrics.toolbar_boundary_y || absTop <= metrics.toolbar_boundary_y + 50) {
                    metrics.excluded_above_toolbar += 1;
                    excludeReason = "above_toolbar";
                } else if (hasUpperSectionParentText(link)) {
                    metrics.excluded_upper_section += 1;
                    excludeReason = "upper_section_parent_text";
                } else if (isUpperSection(link)) {
                    metrics.excluded_upper_section += 1;
                    excludeReason = "upper_section";
                } else if (rect.width < 80 || rect.height < 80) {
                    metrics.excluded_too_small += 1;
                    excludeReason = "too_small";
                } else if (insideFilterBox(link)) {
                    metrics.excluded_inside_filter_box += 1;
                    excludeReason = "inside_filter_box";
                } else {
                    included = true;
                }

                const img = link.querySelector(imgSelector);
                const titleEl = link.querySelector(titleSelector);
                const authorEl = link.querySelector(authorSelector);
                const authorNameEl = link.querySelector('.author-info-box .head-name, .head-name.line-one');
                const metricEls = [...link.querySelectorAll('.info-btns-box .like-action-num')];
                const textParts = [];
                for (const el of link.querySelectorAll('span, p, div')) {
                    const text = (el.textContent || '').trim();
                    if (text) textParts.push(text);
                }

                const card = {
                    href: link.getAttribute('href') || '',
                    title: titleEl ? (titleEl.textContent || '').trim() : '',
                    author: authorEl ? (authorEl.textContent || '').trim() : '',
                    author_name: authorNameEl ? normalizeText(authorNameEl) : '',
                    likes_str: metricEls[0] ? normalizeText(metricEls[0]) : '',
                    comments_str: metricEls[1] ? normalizeText(metricEls[1]) : '',
                    image_src: img ? (img.getAttribute('src') || '') : '',
                    image_data_src: img ? (img.getAttribute('data-src') || '') : '',
                    image_srcset: img ? (img.getAttribute('srcset') || '') : '',
                    image_alt: img ? (img.getAttribute('alt') || '') : '',
                    image_width: img ? (parseInt(img.getAttribute('width') || '0') || 0) : 0,
                    image_height: img ? (parseInt(img.getAttribute('height') || '0') || 0) : 0,
                    text: textParts.join(' '),
                };
                diagnostics.push({
                    title: card.title || card.image_alt || '',
                    author: card.author || '',
                    href: card.href,
                    absTop: Math.round(absTop),
                    boundaryY: Math.round(metrics.toolbar_boundary_y || 0),
                    included,
                    excludeReason,
                    bbox: {
                        x: Math.round(rect.x),
                        y: Math.round(rect.y),
                        w: Math.round(rect.width),
                        h: Math.round(rect.height),
                    },
                });
                if (!included || cards.length >= limit) continue;

                cards.push(card);
            }

            metrics.accepted_main_feed = cards.length;
            if (metrics.global_post_links === 0 || cards.length === 0) {
                metrics.debug_y_bands = JSON.stringify(yBands);
                metrics.debug_first_link_top = links.length > 0 ?
                    Math.round(links[0].getBoundingClientRect().top + window.scrollY) : 0;
            }
            return {cards, metrics, diagnostics};
        }""",
        {
            "limit": limit,
            "cardSelector": CARD_SELECTOR,
            "imgSelector": IMG_SELECTOR,
            "titleSelector": TITLE_SELECTOR,
            "authorSelector": AUTHOR_SELECTOR,
        },
    )
    if isinstance(payload, dict):
        _LAST_EXTRACTION_METRICS = payload.get("metrics") or {}
        _LAST_EXTRACTION_CANDIDATES = payload.get("diagnostics") or []
        return payload.get("cards") or []
    _LAST_EXTRACTION_METRICS = {}
    _LAST_EXTRACTION_CANDIDATES = []
    return payload or []


def option_labels_for_period(period):
    if period == "day":
        log("period=day not available, falling back to week")
        period = "week"
    if period == "month":
        return period, ["Month", "Months", "Month(s)"]
    if period == "all":
        return period, ["All"]
    return period, [period.title()]


def scroll_to_main_feed_toolbar(page) -> bool:
    """Scroll page to bring the real main feed toolbar to the viewport top."""
    box = page.evaluate("""
        () => {
            const toolbarTerms = [
                'Trending',
                'Seedance',
                'GPT image',
                'Featured Topics',
                'Short Film',
                'Pro Tips',
                'Viral Clips',
                'Hot',
                'Filter',
            ];
            const upperPattern = /creative\\s+featured|my\\s+community\\s+milestones?|campaign\\s+featured|achievement|milestone/i;
            const popoverSelector = '.el-popover, .el-popper, .hy-filter-popover, [role="tooltip"]';
            const visible = (el) => {
                const s = window.getComputedStyle(el);
                const b = el.getBoundingClientRect();
                return s.visibility !== 'hidden' &&
                    s.display !== 'none' &&
                    b.width > 0 &&
                    b.height > 0;
            };
            const normalizeText = (el) => (el?.innerText || el?.textContent || '')
                .trim()
                .replace(/\\s+/g, ' ');
            const insideUpperSection = (el) => {
                let cur = el;
                let depth = 0;
                while (cur && cur !== document.body && depth < 8) {
                    const box = cur.getBoundingClientRect();
                    const text = normalizeText(cur);
                    const hasToolbarText = toolbarTerms.some(term => text.toLowerCase().includes(term.toLowerCase()));
                    if (upperPattern.test(text) && !hasToolbarText && box.height < 2200) return true;
                    cur = cur.parentElement;
                    depth++;
                }
                return false;
            };
            const validToolbar = (el) => {
                if (!el || !visible(el)) return false;
                if (el.closest(popoverSelector)) return false;
                if (el.closest('footer')) return false;
                if (el.closest('a[href*="postDetail"]')) return false;
                if (insideUpperSection(el)) return false;
                const box = el.getBoundingClientRect();
                if (box.height < 20 || box.height > 180 || box.width < 120) return false;
                const text = normalizeText(el);
                const hits = toolbarTerms.filter(term => text.toLowerCase().includes(term.toLowerCase())).length;
                return hits >= 2 || (/\\b(Filter|Hot)\\b/i.test(text) && hits >= 1);
            };

            const classCandidates = Array.from(document.querySelectorAll('.my-filter-form-box .filter-form-box, .filter-form-box'))
                .filter(validToolbar);
            if (classCandidates.length) {
                const el = classCandidates
                    .sort((a, b) => (a.getBoundingClientRect().top + window.scrollY) - (b.getBoundingClientRect().top + window.scrollY))[0];
                const b = el.getBoundingClientRect();
                return {top: Math.max(0, Math.round(b.top + window.scrollY))};
            }

            const termNodes = Array.from(document.querySelectorAll('button, [role="button"], a, span, div'))
                .filter((el) => {
                    if (!visible(el)) return false;
                    if (el.closest(popoverSelector)) return false;
                    if (el.closest('footer')) return false;
                    if (el.closest('a[href*="postDetail"]')) return false;
                    if (insideUpperSection(el)) return false;
                    const box = el.getBoundingClientRect();
                    const text = normalizeText(el);
                    return toolbarTerms.some(term => text.toLowerCase().includes(term.toLowerCase())) &&
                        text.length <= 180 &&
                        box.height > 0 &&
                        box.height <= 90 &&
                        box.width >= 20;
                });
            const containers = [];
            for (const node of termNodes) {
                let cur = node;
                let depth = 0;
                while (cur && cur !== document.body && depth < 6) {
                    if (validToolbar(cur)) {
                        containers.push(cur);
                        break;
                    }
                    cur = cur.parentElement;
                    depth++;
                }
            }
            const unique = Array.from(new Set(containers));
            if (unique.length) {
                const el = unique
                    .sort((a, b) => {
                        const ab = a.getBoundingClientRect();
                        const bb = b.getBoundingClientRect();
                        const ay = ab.top + window.scrollY;
                        const by = bb.top + window.scrollY;
                        return ay - by || (ab.height * ab.width) - (bb.height * bb.width);
                    })[0];
                const b = el.getBoundingClientRect();
                return {top: Math.max(0, Math.round(b.top + window.scrollY))};
            }
            return null;
        }
    """)
    if not box:
        log("main feed toolbar not found; refusing to scrape upper sections")
        return False

    page.evaluate("top => window.scrollTo(0, Math.max(0, top))", box["top"])
    page.wait_for_timeout(1000)
    return True


def feed_fingerprint(page):
    """Collect a fingerprint for cards scoped below the real main-feed toolbar."""
    try:
        fingerprint = page.evaluate("""
            () => {
                const visible = (el) => {
                    const style = window.getComputedStyle(el);
                    const box = el.getBoundingClientRect();
                    return style.visibility !== 'hidden' &&
                        style.display !== 'none' &&
                        box.width > 0 &&
                        box.height > 0;
                };
                const normalizeText = (el) => (el?.innerText || el?.textContent || '').trim().replace(/\\s+/g, ' ');
                
                const findFeedBoundaryY = () => {
                    const toolbarTerms = [
                        'Trending',
                        'Seedance',
                        'GPT image',
                        'Featured Topics',
                        'Short Film',
                        'Pro Tips',
                        'Viral Clips',
                        'Hot',
                        'Filter',
                    ];
                    const popoverSelector = '.el-popover, .el-popper, .hy-filter-popover, [role="tooltip"]';
                    const isToolbarCandidate = (el) => {
                        if (!el || !visible(el)) return false;
                        if (el.closest(popoverSelector)) return false;
                        if (el.closest('footer')) return false;
                        if (el.closest('a[href*="postDetail"]')) return false;
                        const box = el.getBoundingClientRect();
                        if (box.height < 20 || box.height > 180 || box.width < 80) return false;
                        const text = normalizeText(el);
                        const hits = toolbarTerms.filter(term => text.toLowerCase().includes(term.toLowerCase())).length;
                        return hits >= 2 || (/\\b(Hot|Filter)\\b/i.test(text) && hits >= 1);
                    };

                    const classCandidates = Array.from(document.querySelectorAll('.my-filter-form-box .filter-form-box, .filter-form-box'))
                        .filter(isToolbarCandidate)
                        .sort((a, b) => {
                            const ab = a.getBoundingClientRect();
                            const bb = b.getBoundingClientRect();
                            return (ab.top + window.scrollY) - (bb.top + window.scrollY);
                        });
                    if (classCandidates.length) {
                        const tb = classCandidates[0].getBoundingClientRect();
                        return Math.round(tb.bottom + window.scrollY + 10);
                    }

                    const termNodes = Array.from(document.querySelectorAll('button, [role="button"], a, span, div'))
                        .filter((el) => {
                            if (!visible(el)) return false;
                            if (el.closest(popoverSelector)) return false;
                            if (el.closest('footer')) return false;
                            if (el.closest('a[href*="postDetail"]')) return false;
                            const text = normalizeText(el);
                            const box = el.getBoundingClientRect();
                            return toolbarTerms.some(term => text.toLowerCase().includes(term.toLowerCase())) &&
                                text.length <= 180 &&
                                box.height > 0 &&
                                box.height <= 90 &&
                                box.width >= 20;
                        });
                    const containers = [];
                    for (const node of termNodes) {
                        let el = node;
                        let depth = 0;
                        while (el && el !== document.body && depth < 6) {
                            if (isToolbarCandidate(el)) {
                                containers.push(el);
                                break;
                            }
                            el = el.parentElement;
                            depth++;
                        }
                    }
                    const unique = Array.from(new Set(containers))
                        .sort((a, b) => {
                            const ab = a.getBoundingClientRect();
                            const bb = b.getBoundingClientRect();
                            const ay = ab.top + window.scrollY;
                            const by = bb.top + window.scrollY;
                            return ay - by || (ab.width * ab.height) - (bb.width * bb.height);
                        });
                    if (unique.length) {
                        const tb = unique[0].getBoundingClientRect();
                        return Math.round(tb.bottom + window.scrollY + 10);
                    }
                    return 0;
                };

                const insideUpperSection = (link) => {
                    const upperPattern = /creative\\s+featured|my\\s+community\\s+milestones?|campaign\\s+featured|achievement|milestone/i;
                    const toolbarPattern = /Trending|Seedance|GPT image|Featured Topics|Short Film|Pro Tips|Viral Clips|\\bHot\\b|\\bFilter\\b/i;
                    let el = link.parentElement;
                    let depth = 0;
                    while (el && el !== document.body && depth < 8) {
                        const box = el.getBoundingClientRect();
                        const text = normalizeText(el);
                        if (box.height < 2200 && upperPattern.test(text) && !toolbarPattern.test(text)) {
                            return true;
                        }
                        el = el.parentElement;
                        depth++;
                    }
                    return false;
                };
                
                const boundaryY = findFeedBoundaryY();
                if (!boundaryY) return [];
                const links = [...document.querySelectorAll('a[href*="postDetail"]')].filter(visible);
                const candidates = links.filter(a => {
                    const box = a.getBoundingClientRect();
                    const top = box.top + window.scrollY;
                    return box.width >= 50 &&
                        box.height >= 50 &&
                        top > boundaryY + 50 &&
                        !insideUpperSection(a);
                });
                return candidates.slice(0, 5).map(a => ({
                    href: a.href,
                    title: (a.querySelector('img')?.alt || '').trim().replace(/\\s+/g, ' ')
                }));
            }"""
        )
        return fingerprint if isinstance(fingerprint, list) else []
    except Exception as e:
        log(f"fingerprint capture failed: {e}")
        return []


def fingerprint_json(fingerprint):
    return json.dumps(fingerprint or [], sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def fingerprint_ids(fingerprint):
    ids = []
    for item in fingerprint or []:
        href = item.get("href", "") if isinstance(item, dict) else ""
        match = DETAIL_URL_REGEX.search(href)
        ids.append(match.group(1) if match else href[-36:])
    return ids


def wait_for_cards(page, min_count=1, attempts=10, delay_ms=1000, scroll=False):
    """Wait for visible post links without applying strict feed extraction filters."""
    last_count = 0
    for attempt in range(attempts):
        try:
            last_count = page.evaluate(
                """() => {
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden' &&
                            style.display !== 'none' &&
                            box.width > 0 &&
                            box.height > 0;
                    };
                    return [...document.querySelectorAll('a[href*="postDetail"]')]
                        .filter(visible)
                        .length;
                }"""
            )
        except Exception as e:
            log(f"visible card poll failed: {e}")
            last_count = 0
        if last_count >= min_count:
            return last_count
        if scroll and attempt in (3, 6):
            try:
                page.mouse.wheel(0, 700)
            except Exception:
                pass
        page.wait_for_timeout(delay_ms)
    return last_count


def wait_for_fingerprint_change(page, before_fp, timeout_ms):
    """Wait until the scoped main-feed fingerprint differs from before_fp."""
    deadline = time.perf_counter() + timeout_ms / 1000
    last_fp = before_fp
    while time.perf_counter() < deadline:
        page.wait_for_timeout(500)
        current = feed_fingerprint(page)
        last_fp = fingerprint_json(current)
        if last_fp != before_fp:
            return True
    raise TimeoutError(f"scoped feed fingerprint unchanged: {last_fp}")


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


def click_toolbar_sort_dropdown(page, target="Hot"):
    """Find and click the toolbar sort dropdown, then select the target sort."""

    target_event = {
        "Recommended": "dropdown-item-recommend",
        "Hot": "dropdown-item-hot",
        "New": "dropdown-item-new",
        "Follow": "dropdown-item-follows",
    }.get(target, "dropdown-item-hot")

    # Strategy 0: SeaArt's desktop toolbar uses Element UI dropdown markup inside
    # right-filter-box. Click that control first, then its own dropdown item.
    try:
        dropdown = page.locator(".right-filter-box .my-select-box .el-dropdown-link").first
        if dropdown.is_visible(timeout=1500):
            box = dropdown.bounding_box()
            current_text = clean_text(dropdown.inner_text())
            dropdown.click()
            page.wait_for_timeout(1000)
            log(f"toolbar sort: opened right-filter-box dropdown text='{current_text}' box={box}")

            item = page.locator(
                f".right-filter-box [data-event='{target_event}'], "
                f".el-dropdown-menu [data-event='{target_event}']"
            ).first
            if item.is_visible(timeout=3000):
                item_box = item.bounding_box()
                item.click()
                page.wait_for_timeout(2000)
                log(f"toolbar sort: clicked '{target}' menu item box={item_box}")
                return True

            if target.lower() in current_text.lower():
                log(f"toolbar sort: target '{target}' is already selected in right-filter-box")
                return True
            log(f"toolbar sort: '{target}' menu item not visible after opening right-filter-box dropdown")
    except Exception as e:
        log(f"toolbar sort strategy 0 failed: {e}")

    def click_target_option(opened_label, opened_box):
        try:
            target_options = page.get_by_text(target, exact=True)
            visible_options = []
            for i in range(min(target_options.count(), 20)):
                option = target_options.nth(i)
                try:
                    if not option.is_visible():
                        continue
                    box = option.bounding_box()
                    if not box:
                        continue
                    text = clean_text(option.inner_text())
                    if text.lower() != target.lower():
                        continue
                    if box["height"] >= 70 or box["width"] < 10:
                        continue
                    # The selected menu item may be in a popover below the toolbar, or the
                    # toolbar itself when the current sort is already the target.
                    if box["y"] < 50 or box["y"] > 600:
                        continue
                    if opened_box and abs(box["x"] - opened_box["x"]) < 3 and abs(box["y"] - opened_box["y"]) < 3:
                        continue
                    visible_options.append((box["y"], box["x"], option, box))
                except Exception:
                    continue
            if not visible_options and opened_label.lower() == target.lower():
                log(f"toolbar sort: target '{target}' is already selected")
                return True
            if visible_options:
                visible_options.sort(key=lambda item: (item[0], item[1]))
                _, _, option, box = visible_options[0]
                option.click()
                page.wait_for_timeout(2000)
                log(f"toolbar sort: clicked target '{target}' at y={box['y']}")
                return True
        except Exception as e:
            log(f"toolbar sort target selector failed: {e}")

        try:
            target_node = page.evaluate(
                """({target, openedBox}) => {
                    const visible = (el) => {
                        const style = window.getComputedStyle(el);
                        const box = el.getBoundingClientRect();
                        return style.visibility !== 'hidden' &&
                            style.display !== 'none' &&
                            box.width > 0 &&
                            box.height > 0;
                    };
                    const targetLower = target.toLowerCase();
                    const openedX = openedBox ? openedBox.x : -9999;
                    const openedY = openedBox ? openedBox.y : -9999;
                    return [...document.querySelectorAll('div, span, button, [role="button"], li')]
                        .filter((el) => {
                            if (!visible(el)) return false;
                            if (el.closest('a[href*="postDetail"]')) return false;
                            if (el.closest('[class*="filter" i]') && /filter|time\\s*range|apply|confirm/i.test(el.closest('[class*="filter" i]').innerText || '')) return false;
                            const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
                            const box = el.getBoundingClientRect();
                            if (text.toLowerCase() !== targetLower) return false;
                            if (box.height >= 70 || box.width < 10) return false;
                            if (box.top + window.scrollY < 50 || box.top + window.scrollY > 600) return false;
                            if (Math.abs((box.x + window.scrollX) - openedX) < 3 &&
                                Math.abs((box.y + window.scrollY) - openedY) < 3) return false;
                            return true;
                        })
                        .map((el) => {
                            const box = el.getBoundingClientRect();
                            return {
                                x: box.x + box.width / 2,
                                y: box.y + box.height / 2,
                                top: box.top + window.scrollY,
                                text: (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' '),
                            };
                        })
                        .sort((a, b) => a.top - b.top)[0] || null;
                }""",
                {"target": target, "openedBox": opened_box},
            )
            if target_node:
                page.mouse.click(target_node["x"], target_node["y"])
                page.wait_for_timeout(2000)
                log(f"toolbar sort: clicked target '{target_node['text']}' via DOM fallback at y={target_node['top']}")
                return True
        except Exception as e:
            log(f"toolbar sort target DOM fallback failed: {e}")

        return False

    # Strategy 1: exact visible sort labels in the toolbar zone.
    try:
        for label in ["Recommended", "Hot", "New", "Follow"]:
            try:
                elements = page.get_by_text(label, exact=True)
                for i in range(min(elements.count(), 20)):
                    el = elements.nth(i)
                    try:
                        if not el.is_visible():
                            continue
                        box = el.bounding_box()
                        if not box:
                            continue
                        text = clean_text(el.inner_text())
                        if text.lower() != label.lower():
                            continue
                        if box["y"] < 50 or box["y"] >= 250 or box["height"] >= 50:
                            continue
                        el.click()
                        page.wait_for_timeout(2000)
                        log(f"toolbar sort: clicked '{label}' at y={box['y']}")
                        return click_target_option(label, box)
                    except Exception:
                        continue
            except Exception:
                continue
    except Exception as e:
        log(f"toolbar sort strategy 1 failed: {e}")

    # Strategy 2: DOM scan for compact sort controls near the top of the page.
    try:
        candidates = page.evaluate(
            """() => {
                const visible = (el) => {
                    const s = window.getComputedStyle(el);
                    const b = el.getBoundingClientRect();
                    return s.visibility !== 'hidden' && s.display !== 'none' && b.width > 0 && b.height > 0;
                };
                return [...document.querySelectorAll('div, span, button, [role="button"]')]
                    .filter(el => {
                        if (!visible(el)) return false;
                        if (el.closest('a[href*="postDetail"]')) return false;
                        if (el.closest('[class*="filter" i]') && /filter|time\\s*range|apply|confirm/i.test(el.closest('[class*="filter" i]').innerText || '')) return false;
                        const text = (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' ');
                        const box = el.getBoundingClientRect();
                        if (box.top + window.scrollY > 250 || box.top + window.scrollY < 50) return false;
                        if (box.height >= 50 || box.width < 20 || box.width > 240) return false;
                        return /^(Recommended|Hot|New|Follow)$/i.test(text);
                    })
                    .map(el => {
                        const box = el.getBoundingClientRect();
                        return {
                            text: (el.innerText || el.textContent || '').trim().replace(/\\s+/g, ' '),
                            tag: el.tagName,
                            x: box.x + window.scrollX,
                            y: box.top + window.scrollY,
                            clickX: box.x + box.width / 2,
                            clickY: box.y + box.height / 2,
                            className: String(el.className || '')
                        };
                    })
                    .sort((a, b) => a.y - b.y || a.x - b.x);
            }"""
        )
        if candidates:
            top = candidates[0]
            log(f"toolbar sort candidate found: text='{top['text']}' tag={top['tag']} y={top['y']}")
            page.mouse.click(top["clickX"], top["clickY"])
            page.wait_for_timeout(2000)
            return click_target_option(top["text"], {"x": top["x"], "y": top["y"]})
    except Exception as e:
        log(f"toolbar sort strategy 2 failed: {e}")

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


def click_filter_apply_button(page):
    """Click an explicit filter apply/confirm action when SeaArt exposes one."""
    apply_labels = [
        "Apply",
        "Confirm",
        "Done",
        "OK",
        "Save",
        "適用",
        "確認",
        "完了",
    ]
    for label in apply_labels:
        try:
            option = page.get_by_role(
                "button", name=re_module.compile(rf"^{re_module.escape(label)}$", re_module.I)
            ).first
            if option.is_visible(timeout=500):
                option.click()
                page.wait_for_timeout(1000)
                log(f"filter sheet action clicked: {label}")
                return "button"
        except Exception:
            pass
        try:
            option = page.get_by_text(label, exact=True).first
            if option.is_visible(timeout=500):
                option.click()
                page.wait_for_timeout(1000)
                log(f"filter sheet action clicked via text: {label}")
                return "text"
        except Exception:
            pass

    return ""


def dismiss_filter_sheet(page):
    """Close transient filter UI without clicking page chrome or promotions."""
    try:
        page.keyboard.press("Escape")
        page.wait_for_timeout(500)
        log("filter sheet close attempted via Escape")
    except Exception as e:
        log(f"filter sheet Escape failed: {e}")

    return "dismiss"


def apply_filters(page, context=None):
    """Apply the toolbar sort first, then optionally refine with the filter sheet."""
    result = {
        "filter_sheet_opened": False,
        "toolbar_sort_found": False,
        "toolbar_hot_clicked": False,
        "toolbar_sort_content_changed": False,
        "week_clicked": False,
        "week_content_changed": False,
        "content_changed": False,
        "quality": "default_feed",
        "warning": "",
        "before_fingerprint": [],
        "after_fingerprint": [],
    }

    visible_before = wait_for_cards(page, min_count=5)
    if visible_before < 5:
        log(f"default feed baseline has only {visible_before} visible cards before fingerprint")
    before_fingerprint = feed_fingerprint(page)
    before_fp = fingerprint_json(before_fingerprint)
    result["before_fingerprint"] = before_fingerprint
    log(f"before_fingerprint_ids={fingerprint_ids(before_fingerprint)}")
    save_debug_screenshot(page, "01-initial-page")
    debug_probe_page(page, "01-initial-page-probe")

    if SEAART_SORT == "hot":
        toolbar_sort_found = click_toolbar_sort_dropdown(page, "Hot")
        result["toolbar_sort_found"] = toolbar_sort_found
        if toolbar_sort_found:
            result["toolbar_hot_clicked"] = True
            try:
                wait_for_fingerprint_change(page, before_fp, 15000)
                result["toolbar_sort_content_changed"] = True
                result["content_changed"] = True
                result["quality"] = "hot_only"
                log("toolbar: Hot changed content")
            except Exception as e:
                log(f"toolbar: Hot did not change content: {e}")
        else:
            log("toolbar: sort dropdown not found")
    else:
        log(f"toolbar: unsupported toolbar sort target '{SEAART_SORT}', skipping")

    save_debug_screenshot(page, "02-after-toolbar-hot")

    if click_visible_filter_button(page):
        result["filter_sheet_opened"] = True
        log("filter_sheet_opened=true")
        save_debug_screenshot(page, "03-filter-sheet-open")
        debug_probe_page(page, "03-filter-sheet-open-probe")

        period_value, period_labels = option_labels_for_period(SEAART_PERIOD)
        if period_value == "week":
            period_labels = ["Week"]
        for period_label in period_labels:
            try:
                if click_filter_option(page, period_label, exact=True):
                    result["week_clicked"] = True
                    log(f"filter: {period_label} clicked")
                    break
            except Exception:
                pass

        if not result["week_clicked"]:
            log(f"filter: period option not visible in filter sheet: {period_value}")

        applied = click_filter_apply_button(page)
        if not applied:
            dismiss_filter_sheet(page)

        after_fp = feed_fingerprint(page)
        after_fp_json = fingerprint_json(after_fp)
        if after_fp_json != before_fp:
            if result["week_clicked"]:
                result["week_content_changed"] = True
                result["content_changed"] = True
                result["quality"] = "hot_week"
                log("filter: Week changed content")
            elif result["content_changed"]:
                log("filter: fingerprint remains changed after sheet dismissal")
        else:
            log("filter: Week did not change content")
    else:
        log("filter: could not open filter sheet")

    after_fingerprint = feed_fingerprint(page)
    result["after_fingerprint"] = after_fingerprint
    log(f"after_fingerprint_ids={fingerprint_ids(after_fingerprint)}")
    log(
        "toolbar_sort_found={toolbar_sort_found} toolbar_hot_clicked={toolbar_hot_clicked} "
        "toolbar_sort_content_changed={toolbar_sort_content_changed}".format(**result)
    )
    log(
        "filter_sheet_opened={filter_sheet_opened} week_clicked={week_clicked} "
        "week_content_changed={week_content_changed}".format(**result)
    )
    if not result["content_changed"]:
        result["warning"] = "filter did not change content; using default feed"
    log(f"content_changed={str(result['content_changed']).lower()} quality={result['quality']}")

    save_debug_screenshot(page, "04-before-extraction")
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

    author_name = clean_text(raw.get("author_name") or "")
    author, author_metrics = (
        (author_name, [])
        if author_name
        else split_author_metrics(clean_text(raw.get("author") or ""))
    )
    likes = parse_int(raw.get("likes"))
    views = parse_int(raw.get("views"))
    likes_str = raw.get("likes_str") or ""
    if likes_str:
        likes = parse_int(likes_str)
    comments_str = raw.get("comments_str") or ""
    comments = parse_int(comments_str) if comments_str else 0
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
        "comments": comments,
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
        "comments": item["comments"],
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
    extraction_metrics = _LAST_EXTRACTION_METRICS or {}
    return {
        "sort": SEAART_SORT,
        "period": SEAART_PERIOD,
        "filter_sheet_opened": filter_status.get("filter_sheet_opened", False),
        "toolbar_sort_found": filter_status.get("toolbar_sort_found", False),
        "toolbar_hot_clicked": filter_status.get("toolbar_hot_clicked", False),
        "toolbar_sort_content_changed": filter_status.get("toolbar_sort_content_changed", False),
        "week_clicked": filter_status.get("week_clicked", False),
        "week_content_changed": filter_status.get("week_content_changed", False),
        "sort_applied": filter_status.get("sort_applied", False),
        "period_applied": filter_status.get("period_applied", False),
        "content_changed": filter_status.get("content_changed", False),
        "quality": filter_status.get("quality", "default_feed"),
        "warning": filter_status.get("warning", ""),
        "pool_size": SEAART_POOL_SIZE,
        "global_post_links": extraction_metrics.get("global_post_links", selector_matches or len(raw_cards)),
        "toolbar_boundary_y": extraction_metrics.get("toolbar_boundary_y", 0),
        "accepted_main_feed": extraction_metrics.get("accepted_main_feed", len(raw_cards)),
        "excluded_above_toolbar": extraction_metrics.get("excluded_above_toolbar", 0),
        "excluded_upper_section": extraction_metrics.get("excluded_upper_section", 0),
        "excluded_too_small": extraction_metrics.get("excluded_too_small", 0),
        "excluded_inside_filter_box": extraction_metrics.get("excluded_inside_filter_box", 0),
        "debug_y_bands": extraction_metrics.get("debug_y_bands", ""),
        "debug_first_link_top": extraction_metrics.get("debug_first_link_top", 0),
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
        "[SeaArt] toolbar_boundary_y={toolbar_boundary_y} global_post_links={global_post_links} "
        "accepted_main_feed={accepted_main_feed} excluded_above_toolbar={excluded_above_toolbar} "
        "excluded_upper_section={excluded_upper_section} excluded_too_small={excluded_too_small} "
        "excluded_inside_filter_box={excluded_inside_filter_box} debug_y_bands={debug_y_bands} "
        "debug_first_link_top={debug_first_link_top}".format(**metrics),
        file=sys.stderr,
    )
    print(
        "[SeaArt] sort={sort} period={period} filter_sheet_opened={filter_sheet_opened} "
        "toolbar_sort_found={toolbar_sort_found} toolbar_hot_clicked={toolbar_hot_clicked} "
        "toolbar_sort_content_changed={toolbar_sort_content_changed} week_clicked={week_clicked} "
        "week_content_changed={week_content_changed} sort_applied={sort_applied} period_applied={period_applied} "
        "content_changed={content_changed} quality={quality} pool={pool_size} "
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
    (DEBUG_DIR / "cards.json").write_text(
        json.dumps(_LAST_EXTRACTION_CANDIDATES, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    if error:
        (DEBUG_DIR / "seaart-error.txt").write_text(str(error), encoding="utf-8")


def get_seaart_state_args():
    # Load SeaArt storage state if available.
    seaart_state = os.environ.get("SEAART_STATE_FILE", "")
    seaart_require_login = os.environ.get("SEAART_REQUIRE_LOGIN", "0") == "1"

    state_args = {}
    auth_state = "missing"
    if seaart_state and Path(seaart_state).exists():
        try:
            with open(seaart_state) as f:
                json.load(f)
            state_args["storage_state"] = seaart_state
            auth_state = "loaded"
            log(f"auth_state=loaded file={seaart_state}")
        except Exception as e:
            auth_state = "invalid"
            log(f"auth_state=invalid file={seaart_state} error={e}")
            if seaart_require_login:
                raise RuntimeError("SeaArt login state is required but invalid")
    else:
        log("auth_state=missing anonymous=true")

    return state_args, auth_state


def log_login_state(page, auth_state):
    logged_in_js = """
        () => {
            const text = document.body.innerText || '';
            const hasLoginBtn = /Đăng nhập|Login|Sign in|Log in/i.test(text);
            const hasAvatar = !!document.querySelector(
                '[class*="avatar"], [class*="Avatar"], [class*="user-menu"], [class*="UserMenu"]'
            );
            return !hasLoginBtn || hasAvatar;
        }
    """
    is_logged_in = page.evaluate(logged_in_js)
    log(f"logged_in={str(is_logged_in).lower()} auth_state={auth_state}")
    return is_logged_in


def new_mobile_context(browser, state_args=None):
    state_args = state_args or {}
    return browser.new_context(
        **state_args,
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
    scroll_to_main_feed_toolbar(page)
    raw_cards = extract_cards(page, pool_size)
    selector_matches = page.locator(CARD_SELECTOR).count()
    results = dedupe_items(raw_cards, pool_size)
    candidates = len(results)

    if len(results) < count / 2:
        page.mouse.wheel(0, 350)
        page.wait_for_timeout(500)
        raw_cards = extract_cards(page, pool_size)
        selector_matches = page.locator(CARD_SELECTOR).count()
        results = dedupe_items(raw_cards, pool_size)
        candidates = len(results)

    results.sort(key=lambda x: (x.get('likes', 0), x.get('views', 0)), reverse=True)
    return raw_cards, results[:count], selector_matches, candidates


def try_url_filter_probe(page, filter_status):
    """Fallback probe for SeaArt query parameters when UI filters do not mutate the feed."""
    if SEAART_SORT != "hot" or SEAART_PERIOD != "week":
        return False

    before_fingerprint = filter_status.get("before_fingerprint") or []
    if not before_fingerprint:
        log("URL filter probe skipped: no baseline fingerprint")
        return False

    before_fp = fingerprint_json(before_fingerprint)
    test_urls = [
        "https://www.seaart.ai/post?sort=hot&period=week",
        "https://www.seaart.ai/post?sortBy=hot&timeRange=week",
    ]
    for url in test_urls:
        try:
            log(f"URL filter probe loading {url}")
            page.goto(url, wait_until='domcontentloaded', timeout=30000)
            wait_for_cards(page, min_count=5)
            fp = feed_fingerprint(page)
            fp_json = fingerprint_json(fp)
            if len(fp) >= 5:
                log(f"URL {url} returned {len(fp)} cards")
                filter_status["after_fingerprint"] = fp
                log(f"url_probe_fingerprint_ids={fingerprint_ids(fp)}")
                if fp_json != before_fp:
                    filter_status["content_changed"] = True
                    filter_status["quality"] = "hot_week"
                    filter_status["warning"] = ""
                    log("URL filter probe changed content")
                    return True
        except Exception as e:
            log(f"URL filter probe failed for {url}: {e}")

    return False


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
        "toolbar_sort_found": False,
        "toolbar_hot_clicked": False,
        "toolbar_sort_content_changed": False,
        "week_clicked": False,
        "week_content_changed": False,
        "content_changed": False,
        "quality": "default_feed",
        "warning": "",
    }
    artifact_page = None

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        try:
            state_args, auth_state = get_seaart_state_args()
            context = browser.new_context(
                **state_args,
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

                log_login_state(page, auth_state)
                try:
                    page.wait_for_selector(CARD_SELECTOR, timeout=15000)
                except Exception as e:
                    error = f"post cards did not appear: {e}"
                    log(error)
                visible_cards = wait_for_cards(page, min_count=5, attempts=15, scroll=True)
                if visible_cards < 5:
                    log(f"only {visible_cards} visible postDetail cards after load wait; continuing with fallback paths")

                debug_probe_page(page, f"desktop-after-load-{attempt}")
                filter_status = apply_filters(page, context)
                extraction_page = page

                if not filter_status.get("content_changed"):
                    try_url_filter_probe(page, filter_status)

                if not filter_status.get("content_changed"):
                    log("desktop filter did not change content; retrying with mobile browser context")
                    try:
                        mobile_context = new_mobile_context(browser, state_args)
                        mobile_page = mobile_context.new_page()
                        log("loading trending page in mobile context")
                        mobile_page.goto(SEAART_POST_URL, wait_until='domcontentloaded', timeout=30000)
                        log_login_state(mobile_page, auth_state)
                        try:
                            mobile_page.wait_for_selector(CARD_SELECTOR, timeout=15000)
                        except Exception as e:
                            log(f"mobile post cards did not appear: {e}")
                        mobile_visible_cards = wait_for_cards(
                            mobile_page, min_count=5, attempts=15, scroll=True
                        )
                        if mobile_visible_cards < 5:
                            log(f"only {mobile_visible_cards} visible mobile postDetail cards after load wait")
                        mobile_page.wait_for_timeout(1500)
                        debug_probe_page(mobile_page, f"mobile-after-load-{attempt}")
                        mobile_filter_status = apply_filters(mobile_page, mobile_context)
                        if not mobile_filter_status.get("content_changed"):
                            try_url_filter_probe(mobile_page, mobile_filter_status)
                        if mobile_filter_status.get("content_changed"):
                            log("mobile filter changed content; extracting from mobile page")
                            filter_status = mobile_filter_status
                            extraction_page = mobile_page
                            artifact_page = mobile_page
                        else:
                            log("mobile filter did not change content; falling back to default desktop feed")
                            mobile_context.close()
                    except Exception as e:
                        log(f"mobile context filter retry failed: {e}")

                try:
                    save_debug_screenshot(extraction_page, "before-extraction")
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
