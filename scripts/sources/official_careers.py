#!/usr/bin/env python3
"""Crawler for Official Company Career Pages.

Many large company career sites use dynamic JS rendering or have login walls.
This module attempts static HTML crawling and expects most to fail gracefully.
"""

import logging
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("job_hyogo.sources.official_careers")

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
}

DEFAULT_COMPANIES: dict[str, dict[str, str]] = {
    "mitsubishi_electric": {
        "name": "Mitsubishi Electric",
        "url": "https://www.mitsubishielectric.co.jp/recruitment/",
    },
    "sumitomo_rubber": {
        "name": "Sumitomo Rubber",
        "url": "https://www.srigroup.co.jp/recruit/",
    },
    "kobe_steel": {
        "name": "Kobe Steel",
        "url": "https://www.kobelco.co.jp/recruit/",
    },
    "kawasaki_heavy": {
        "name": "Kawasaki Heavy",
        "url": "https://www.khi.co.jp/recruit/",
    },
    "mitsubishi_heavy": {
        "name": "Mitsubishi Heavy",
        "url": "https://www.mhi.com/jp/careers/",
    },
    "hitachi": {
        "name": "Hitachi",
        "url": "https://www.hitachi.co.jp/recruit/",
    },
}


def crawl(companies: dict[str, dict[str, str]] | None = None) -> list[dict[str, Any]]:
    """Crawl official company career pages for job listings.

    Args:
        companies: Dict of company configs with 'name' and 'url' keys.
                   Defaults to pre-defined large companies in Hyogo area.

    Returns:
        List of raw job item dicts. Most will likely be empty due to JS walls.
    """
    targets = companies or DEFAULT_COMPANIES
    raw_items: list[dict[str, Any]] = []

    for key, cfg in targets.items():
        company_name = cfg.get("name", key)
        url = cfg.get("url", "")
        if not url:
            continue

        logger.info("Crawling official career page: %s (%s)", company_name, url)
        try:
            resp = requests.get(url, headers=HEADERS, timeout=30)
            resp.encoding = "utf-8"
            resp.raise_for_status()
        except requests.RequestException as e:
            logger.debug("Official career page %s failed: %s", company_name, e)
            continue

        items = _parse_career_page(resp.text, url, company_name)
        if items:
            raw_items.extend(items)
            logger.info("  → %s: found %d items", company_name, len(items))
        else:
            logger.info("  → %s: no items (likely JS-rendered or login wall)", company_name)

    logger.info(
        "Official careers: parsed %d items from %d companies",
        len(raw_items),
        len(targets),
    )
    return raw_items


def _parse_career_page(html: str, base_url: str, company_name: str) -> list[dict[str, Any]]:
    """Parse a company career page for job listings.

    Most career sites use JS rendering; this function makes a best-effort
    attempt with static HTML selectors.
    """
    items: list[dict[str, Any]] = []
    soup = BeautifulSoup(html, "html.parser")

    # Try common selectors for job listings on Japanese career sites
    cards = (
        soup.select("article.job-offer, .job-list-item, tr.job-row")
        or soup.select("div.recruit-item, div.job-item, li.job")
        or soup.select("a[href*='/job'], a[href*='/recruit'], a[href*='/career'], a[href*='/kyujin']")
    )

    for card in cards:
        try:
            title_el = card.select_one(
                "h3, h4, .job-title, .recruit-title, a[href*='/job'], a[href*='/recruit']"
            )
            if not title_el:
                continue
            title = title_el.get_text(strip=True)
            if not title or len(title) < 5:
                continue

            link = ""
            if title_el.name == "a":
                link = str(title_el.get("href", "") or "")
            else:
                link_el = card.select_one("a[href]")
                if link_el:
                    link = str(link_el.get("href", "") or "")
            if link and not link.startswith("http"):
                link = urljoin(base_url, link)

            area_el = card.select_one(".location, .area, .job-location")
            area = area_el.get_text(strip=True) if area_el else ""

            items.append({
                "title": title.strip(),
                "company": company_name,
                "area": area,
                "salary": "",
                "employment_type": "",
                "description": "",
                "url": link.rstrip("/") if link else "",
                "source_name": company_name,
            })
        except Exception:
            continue

    return items


# Backward-compatible alias
crawl_official_careers = crawl

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    items = crawl()
    print(f"Found {len(items)} items from official career pages")
    for item in items[:3]:
        print(f"  - {item['title']} @ {item['company']}")
