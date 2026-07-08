#!/usr/bin/env python3
"""Crawler for Facebook job group search (via web_search or curl).

Facebook requires authentication and JS rendering for full access.
This module attempts lightweight scraping of public Facebook content
via textise dot iitty or curl with public group links. Many groups
require login, so this may return empty results.
"""

import logging
import re
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("job_hyogo.sources.facebook_search")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
}

SEARCH_URLS = [
    "https://www.facebook.com/search/posts/?q=Hyogo+job+hire+foreigner",
    "https://www.facebook.com/search/posts/?q=Himeji+factory+recruit",
    "https://www.facebook.com/search/posts/?q=Kobe+job+foreigner",
    "https://www.facebook.com/search/posts/?q=Hyogo+%E5%B7%A5%E5%A0%B4+%E6%B1%82%E4%BA%BA",
]


def crawl(
    urls: list[str] | None = None,
    keywords: list[str] | None = None,
) -> list[dict[str, Any]]:
    """Crawl Facebook for job-related posts.

    Args:
        urls: List of Facebook search/group URLs to crawl.
              Defaults to pre-defined search URLs.
        keywords: Additional search keywords (unused in basic mode).

    Returns:
        List of raw Facebook post item dicts (may be empty due to login walls).
    """
    target_urls = urls or SEARCH_URLS
    raw_items: list[dict[str, Any]] = []

    # Try textise dot iitty as a fallback for public content
    textise_urls = [
        f"https://r.jina.ai/http://www.facebook.com/search/posts/?q=Hyogo+job",
        f"https://r.jina.ai/http://www.facebook.com/search/posts/?q=Hyogo+%E5%B7%A5%E5%A0%B4",
    ]

    for fb_url in target_urls:
        logger.info("Crawling Facebook: %s", fb_url)
        try:
            resp = requests.get(
                fb_url,
                headers=HEADERS,
                timeout=30,
                allow_redirects=True,
            )
            resp.encoding = "utf-8"

            if resp.status_code == 200:
                items = _parse_facebook_page(resp.text, fb_url)
                raw_items.extend(items)
                logger.info("Facebook %s: found %d items", fb_url, len(items))
            else:
                logger.debug("Facebook %s: HTTP %d", fb_url, resp.status_code)
        except requests.RequestException as e:
            logger.debug("Facebook crawl failed for %s: %s", fb_url, e)
            continue

    # Try textise fallback
    if not raw_items:
        logger.info("Facebook: trying textise fallback URLs")
        for fb_url in textise_urls:
            try:
                resp = requests.get(fb_url, headers=HEADERS, timeout=30)
                resp.encoding = "utf-8"
                if resp.status_code == 200 and len(resp.text) > 500:
                    items = _parse_generic(resp.text, fb_url)
                    raw_items.extend(items)
                    logger.info("Textise fallback: found %d items", len(items))
            except requests.RequestException:
                continue

    if not raw_items:
        logger.warning(
            "Facebook crawl returned 0 results (login/JS wall). "
            "Consider using a browser-based approach."
        )

    return raw_items


def _parse_facebook_page(html: str, source_url: str) -> list[dict[str, Any]]:
    """Parse Facebook search results page (limited due to JS rendering)."""
    items: list[dict[str, Any]] = []
    soup = BeautifulSoup(html, "html.parser")

    # Look for post excerpts in page source or noscript
    for noscript in soup.select("noscript"):
        text = noscript.get_text(strip=True)
        if text and len(text) > 50:
            # Check if it looks like a job post
            if any(kw in text.lower() for kw in ["job", "hire", "recruit", "求人", "工場"]):
                items.append({
                    "title": text[:100],
                    "content": text[:500],
                    "url": source_url,
                    "source_name": "Facebook",
                    "area": "",
                    "company": "",
                    "salary": "",
                })

    # Also look for text content
    body_text = soup.get_text(separator="\n", strip=True)
    for paragraph in body_text.split("\n"):
        p = paragraph.strip()
        if len(p) > 80 and any(kw in p.lower() for kw in ["job", "hire", "recruit", "求人"]):
            items.append({
                "title": p[:100],
                "content": p[:500],
                "url": source_url,
                "source_name": "Facebook",
                "area": "",
                "company": "",
                "salary": "",
            })
            if len(items) >= 5:
                break

    return items


def _parse_generic(html: str, source_url: str) -> list[dict[str, Any]]:
    """Parse generic textise/rendered page content."""
    items: list[dict[str, Any]] = []
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator="\n", strip=True)

    for paragraph in text.split("\n"):
        p = paragraph.strip()
        if len(p) > 60 and any(kw in p.lower() for kw in ["job", "hire", "recruit", "求人", "工場"]):
            items.append({
                "title": p[:100],
                "content": p[:500],
                "url": source_url,
                "source_name": "Facebook",
                "area": "",
                "company": "",
                "salary": "",
            })
            if len(items) >= 5:
                break

    return items


# Backward-compatible alias
crawl_facebook = crawl

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    items = crawl()
    print(f"Found {len(items)} Facebook items")
    for item in items[:3]:
        print(f"  - {item['title'][:60]}")
