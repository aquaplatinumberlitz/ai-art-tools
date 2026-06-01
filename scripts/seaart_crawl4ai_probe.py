#!/usr/bin/env python3
"""Probe crawl4ai against SeaArt's public post listing.

This is intentionally standalone and non-production. It avoids proxy and
anti-bot bypass features so it can answer whether crawl4ai's normal rendered
page flow can see SeaArt cards and lazy-loaded images.
"""
import argparse
import asyncio
import base64
import importlib.util
import inspect
import json
import os
import re
import sys
import time
from html.parser import HTMLParser
from pathlib import Path
from typing import Any
from urllib.parse import urljoin


SEAART_POST_URL = "https://seaart.ai/post"
POST_RE = re.compile(r"/postDetail/([^?\"'#<\s]+)")


CSS_SCHEMA = {
    "name": "seaart_posts",
    "baseSelector": 'a[href*="postDetail"]',
    "fields": [
        {"name": "href", "type": "attribute", "attribute": "href"},
        {"name": "image_src", "selector": "img", "type": "attribute", "attribute": "src"},
        {"name": "image_data_src", "selector": "img", "type": "attribute", "attribute": "data-src"},
        {"name": "image_srcset", "selector": "img", "type": "attribute", "attribute": "srcset"},
        {"name": "image_alt", "selector": "img", "type": "attribute", "attribute": "alt"},
        {
            "name": "title",
            "selector": '[class*="title"], [class*="Title"], [class*="name"], [class*="Name"]',
            "type": "text",
        },
        {
            "name": "author",
            "selector": '[class*="author"], [class*="Author"], [class*="user"], [class*="User"]',
            "type": "text",
        },
        {"name": "text", "type": "text"},
    ],
}


def log(message: str) -> None:
    print(f"seaart-crawl4ai-probe: {message}", file=sys.stderr)


def supported_kwargs(cls: Any, kwargs: dict[str, Any]) -> dict[str, Any]:
    params = inspect.signature(cls).parameters
    return {k: v for k, v in kwargs.items() if k in params}


def cover_to_file_url(cover_url: str) -> str:
    clean_url = (cover_url or "").split("?", 1)[0]
    m = re.search(
        r"temp-convert-webp/(?:highwebp|png|mp4)/([^/]+)/([^/]+)/(.+?)(?:_low|_high)?\.webp$",
        clean_url,
    )
    if m:
        date_part, task_part, hash_part = m.group(1), m.group(2), m.group(3)
        return f"https://image.cdn2.seaart.me/{date_part}/{task_part}/{hash_part}_high.webp"
    return clean_url.replace("_low", "_high")


def parse_int(value: Any) -> int:
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


def first_src_from_srcset(srcset: str) -> str:
    if not srcset:
        return ""
    first = srcset.split(",", 1)[0].strip()
    return first.split(" ", 1)[0].strip()


def normalize_item(raw: dict[str, Any]) -> dict[str, Any] | None:
    href = raw.get("href") or raw.get("url") or ""
    match = POST_RE.search(href)
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
    image_url = urljoin(SEAART_POST_URL, image_url) if image_url else ""

    title = clean_text(raw.get("title") or raw.get("image_alt") or "")
    author = clean_text(raw.get("author") or "")
    if not title:
        title = derive_title(raw.get("text") or "")

    return {
        "id": post_id,
        "title": title,
        "author": author,
        "image_url": cover_to_file_url(image_url),
        "url": f"https://seaart.ai/postDetail/{post_id}",
        "likes": parse_int(raw.get("likes")),
        "views": parse_int(raw.get("views")),
        "collections": parse_int(raw.get("collections")),
        "created_at": raw.get("created_at") or 0,
    }


def clean_text(value: Any) -> str:
    return re.sub(r"\s+", " ", str(value or "")).strip()


def derive_title(text: str) -> str:
    text = clean_text(text)
    if not text:
        return ""
    for part in re.split(r"(?<=[.!?])\s+|\n+", text):
        part = clean_text(part)
        if 3 < len(part) < 100:
            return part
    return text[:99]


class PostLinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.items: list[dict[str, Any]] = []
        self._current: dict[str, Any] | None = None
        self._capture_text = False
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attr = {k: v or "" for k, v in attrs}
        if tag == "a" and "postDetail" in attr.get("href", ""):
            self._current = {"href": attr.get("href", "")}
            self._text = []
            self._capture_text = True
        elif self._current is not None and tag == "img":
            self._current.setdefault("image_src", attr.get("src", ""))
            self._current.setdefault("image_data_src", attr.get("data-src", ""))
            self._current.setdefault("image_srcset", attr.get("srcset", ""))
            self._current.setdefault("image_alt", attr.get("alt", ""))

    def handle_data(self, data: str) -> None:
        if self._capture_text:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._current is not None:
            self._current["text"] = " ".join(self._text)
            self.items.append(self._current)
            self._current = None
            self._capture_text = False
            self._text = []


def fallback_extract(html: str, markdown: str, limit: int) -> list[dict[str, Any]]:
    parser = PostLinkParser()
    try:
        parser.feed(html or "")
    except Exception as exc:
        log(f"HTML fallback parser failed: {exc}")

    raw_items = parser.items
    if not raw_items and markdown:
        raw_items = [{"href": match.group(0)} for match in POST_RE.finditer(markdown)]

    return dedupe_items(normalize_item(item) for item in raw_items)[:limit]


def dedupe_items(items: Any) -> list[dict[str, Any]]:
    seen: set[str] = set()
    out: list[dict[str, Any]] = []
    for item in items:
        if not item or item["id"] in seen:
            continue
        seen.add(item["id"])
        out.append(item)
    return out


def result_attr(result: Any, name: str, default: Any = None) -> Any:
    return getattr(result, name, default)


def markdown_text(result: Any) -> str:
    markdown = result_attr(result, "markdown", "") or ""
    if isinstance(markdown, str):
        return markdown
    return getattr(markdown, "raw_markdown", "") or str(markdown)


def parse_extracted_content(content: Any, limit: int) -> list[dict[str, Any]]:
    if not content:
        return []
    if isinstance(content, str):
        try:
            content = json.loads(content)
        except json.JSONDecodeError:
            return []
    if isinstance(content, dict):
        content = [content]
    return dedupe_items(normalize_item(item) for item in content if isinstance(item, dict))[:limit]


def write_debug_artifacts(debug_dir: Path, result: Any = None, error: str = "") -> None:
    debug_dir.mkdir(parents=True, exist_ok=True)
    if error:
        (debug_dir / "error.txt").write_text(error, encoding="utf-8")
    if result is None:
        return

    html = result_attr(result, "html", "") or ""
    if html:
        (debug_dir / "seaart-post.html").write_text(html, encoding="utf-8")

    md = markdown_text(result)
    if md:
        (debug_dir / "seaart-post.md").write_text(md, encoding="utf-8")

    screenshot = result_attr(result, "screenshot", None)
    if screenshot:
        try:
            data = screenshot.split(",", 1)[-1] if isinstance(screenshot, str) else screenshot
            (debug_dir / "seaart-post.png").write_bytes(base64.b64decode(data))
        except Exception as exc:
            (debug_dir / "screenshot-error.txt").write_text(str(exc), encoding="utf-8")


def item_stats(items: list[dict[str, Any]]) -> dict[str, int]:
    return {
        "item_count": len(items),
        "items_with_images": sum(1 for item in items if item.get("image_url")),
        "items_with_titles": sum(1 for item in items if item.get("title")),
        "items_with_authors": sum(1 for item in items if item.get("author")),
    }


async def run_crawl4ai(limit: int, save_debug: bool, debug_dir: Path) -> dict[str, Any]:
    started = time.perf_counter()
    try:
        from crawl4ai import AsyncWebCrawler, BrowserConfig, CrawlerRunConfig
        from crawl4ai.extraction_strategy import JsonCssExtractionStrategy
    except Exception as exc:
        return {"ok": False, "runtime": 0, "failure_reason": f"import failed: {exc}", "items": []}

    browser_config = BrowserConfig(
        **supported_kwargs(
            BrowserConfig,
            {
                "browser_type": "chromium",
                "headless": True,
                "viewport_width": 1440,
                "viewport_height": 900,
                "ignore_https_errors": True,
                "java_script_enabled": True,
                "verbose": False,
                "user_agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "enable_stealth": False,
                "proxy": None,
                "proxy_config": None,
            },
        )
    )

    run_config = CrawlerRunConfig(
        **supported_kwargs(
            CrawlerRunConfig,
            {
                "wait_for": 'css:a[href*="postDetail"]',
                "wait_for_timeout": 30000,
                "page_timeout": 30000,
                "wait_until": "domcontentloaded",
                "scan_full_page": True,
                "scroll_delay": 0.35,
                "max_scroll_steps": 8,
                "wait_for_images": True,
                "delay_before_return_html": 1.0,
                "extraction_strategy": JsonCssExtractionStrategy(CSS_SCHEMA),
                "screenshot": save_debug,
                "verbose": False,
            },
        )
    )

    result = None
    try:
        async with AsyncWebCrawler(config=browser_config) as crawler:
            result = await crawler.arun(url=SEAART_POST_URL, config=run_config)
    except Exception as exc:
        if save_debug:
            write_debug_artifacts(debug_dir, error=f"crawl exception: {exc}")
        log(f"crawl4ai fetch failed: {exc}")
        return {
            "ok": False,
            "runtime": round(time.perf_counter() - started, 3),
            "failure_reason": f"crawl exception: {exc}",
            "items": [],
            "schema": CSS_SCHEMA,
        }

    status_code = result_attr(result, "status_code", None)
    error_message = result_attr(result, "error_message", None)
    success = bool(result_attr(result, "success", False))
    html = result_attr(result, "html", "") or ""
    markdown = markdown_text(result)

    extracted = parse_extracted_content(result_attr(result, "extracted_content", None), limit)
    fallback = fallback_extract(html, markdown, limit)
    items = extracted or fallback
    selector_count = len(POST_RE.findall(html or markdown or ""))

    failure = ""
    if not success:
        failure = error_message or f"crawl returned success={success}"
    elif not selector_count:
        failure = 'selector failure: no a[href*="postDetail"] links in returned content'
    elif not items:
        failure = "extraction failure: post links found but no normalized items"

    if failure:
        log(f"status={status_code} failure={failure}")
    if save_debug and failure:
        write_debug_artifacts(debug_dir, result=result, error=f"status={status_code}\n{failure}\n")

    return {
        "ok": success and bool(items) and not failure,
        "runtime": round(time.perf_counter() - started, 3),
        "status": status_code,
        "failure_reason": failure,
        "selector_matches": selector_count,
        "used_extraction": "JsonCssExtractionStrategy" if extracted else "fallback_html_markdown",
        "lazy_image_check": {
            "wait_for_images_requested": "wait_for_images" in inspect.signature(CrawlerRunConfig).parameters,
            "scan_full_page_requested": "scan_full_page" in inspect.signature(CrawlerRunConfig).parameters,
            "items_with_images": sum(1 for item in items if item.get("image_url")),
        },
        "schema": CSS_SCHEMA,
        "items": items[:limit],
    }


def run_playwright_compare(limit: int) -> dict[str, Any]:
    started = time.perf_counter()
    script_path = Path(__file__).with_name("seaart_trending.py")
    try:
        spec = importlib.util.spec_from_file_location("seaart_trending_probe_compare", script_path)
        if spec is None or spec.loader is None:
            raise RuntimeError("could not load seaart_trending.py")
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        items = module.fetch_trending(limit)
        return {
            "ok": True,
            "runtime": round(time.perf_counter() - started, 3),
            "failure_reason": "",
            **item_stats(items),
        }
    except Exception as exc:
        log(f"playwright comparison failed: {exc}")
        return {
            "ok": False,
            "runtime": round(time.perf_counter() - started, 3),
            "failure_reason": str(exc),
            "item_count": 0,
            "items_with_images": 0,
            "items_with_titles": 0,
            "items_with_authors": 0,
        }


def write_report(path: Path, summary: dict[str, Any]) -> None:
    crawl = summary["crawl4ai"]
    lines = [
        "SeaArt crawl4ai probe report",
        f"URL: {SEAART_POST_URL}",
        f"crawl4ai ok: {crawl.get('ok')}",
        f"crawl4ai runtime: {crawl.get('runtime')}s",
        f"crawl4ai status: {crawl.get('status')}",
        f"crawl4ai failure: {crawl.get('failure_reason') or ''}",
        f"crawl4ai extraction: {crawl.get('used_extraction')}",
        f"crawl4ai selector matches: {crawl.get('selector_matches')}",
        f"crawl4ai items: {crawl.get('item_count')}",
        f"crawl4ai items with images: {crawl.get('items_with_images')}",
        f"crawl4ai items with titles: {crawl.get('items_with_titles')}",
        f"crawl4ai items with authors: {crawl.get('items_with_authors')}",
        "JsonCssExtractionStrategy schema:",
        json.dumps(CSS_SCHEMA, indent=2),
        "Lazy-load observations:",
        json.dumps(crawl.get("lazy_image_check", {}), indent=2),
    ]
    if "playwright" in summary:
        pw = summary["playwright"]
        lines.extend(
            [
                "Playwright comparison:",
                json.dumps(pw, indent=2),
            ]
        )
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=10)
    parser.add_argument("--json-out", default="/tmp/seaart-crawl4ai-probe.json")
    parser.add_argument("--debug-dir", default="/tmp/seaart-crawl4ai-debug")
    parser.add_argument("--compare-playwright", action="store_true")
    parser.add_argument("--save-debug", action="store_true")
    return parser.parse_args()


async def async_main() -> int:
    args = parse_args()
    debug_dir = Path(args.debug_dir)
    summary: dict[str, Any] = {
        "url": SEAART_POST_URL,
        "limit": args.limit,
        "crawl4ai": await run_crawl4ai(args.limit, args.save_debug, debug_dir),
    }
    summary["crawl4ai"].update(item_stats(summary["crawl4ai"].get("items", [])))

    if args.compare_playwright:
        summary["playwright"] = await asyncio.to_thread(run_playwright_compare, args.limit)

    Path(args.json_out).write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    write_report(Path("/tmp/seaart-crawl4ai-report.txt"), summary)
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    try:
        return asyncio.run(async_main())
    except KeyboardInterrupt:
        log("interrupted")
        return 130
    except Exception as exc:
        log(f"probe failed without raw traceback: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
