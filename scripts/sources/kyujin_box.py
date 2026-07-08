#!/usr/bin/env python3
"""Crawler for 求人ボックス (xn--pckua2a7gp8552d.com)."""

import logging
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("job_hyogo.sources.kyujin_box")

BASE_URL = "https://xn--pckua2a7gp8552d.com"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
}


def crawl(url: str | None = None) -> list[dict[str, Any]]:
    """Crawl 求人ボックス (Kyujin Box) for job listings.

    Args:
        url: URL to crawl. Defaults to the main page.

    Returns:
        List of raw job item dicts.
    """
    target_url = url or BASE_URL
    logger.info("Crawling 求人ボックス: %s", target_url)
    raw_items: list[dict[str, Any]] = []

    try:
        resp = requests.get(target_url, headers=HEADERS, timeout=30)
        resp.encoding = "utf-8"
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("求人ボックス crawl failed: %s", e)
        return raw_items

    soup = BeautifulSoup(resp.text, "html.parser")

    cards = (
        soup.select("article.job-card, .job-item, .p-job-item, li.job-list-item")
        or soup.select("div.job-box, div.recruit-card, table.job-table tr")
        or soup.select("a[href*='/job'], a[href*='/detail'], a[href*='/kyujin']")
    )

    if not cards:
        logger.info("求人ボックス: no job cards found")
        return raw_items

    logger.info("Found %d job card(s) on 求人ボックス", len(cards))

    for card in cards:
        try:
            item = _parse_card(card)
            if item:
                raw_items.append(item)
        except Exception as e:
            logger.debug("Error parsing 求人ボックス card: %s", e)
            continue

    logger.info("求人ボックス: parsed %d raw items", len(raw_items))
    return raw_items


def _parse_card(card) -> dict[str, Any] | None:
    """Parse a single 求人ボックス job card."""
    title_el = card.select_one(
        "h3, h4, .job-title, .card-title, a[href*='/job'], a[href*='/detail'], a[href*='/kyujin']"
    )
    if not title_el:
        return None
    title = title_el.get_text(strip=True)
    if not title or len(title) < 3:
        return None

    link = ""
    if title_el.name == "a":
        link = title_el.get("href", "")
    else:
        link_el = card.select_one("a[href]")
        if link_el:
            link = link_el.get("href", "")
    if link and not link.startswith("http"):
        link = urljoin(BASE_URL, link)

    company_el = card.select_one(".company, .job-company, .card-company")
    company = company_el.get_text(strip=True) if company_el else ""

    area_el = card.select_one(".location, .area, .job-area, .card-location")
    area = area_el.get_text(strip=True) if area_el else ""

    salary_el = card.select_one(".salary, .price, .job-salary, .card-salary")
    salary = salary_el.get_text(strip=True) if salary_el else ""

    etype_el = card.select_one(".employment, .job-type, .card-type")
    employment_type = etype_el.get_text(strip=True) if etype_el else ""

    desc_el = card.select_one(".description, .job-desc, p")
    description = desc_el.get_text(strip=True) if desc_el else ""

    return {
        "title": title,
        "company": company,
        "area": area,
        "salary": salary,
        "employment_type": employment_type,
        "description": description,
        "url": link.rstrip("/") if link else "",
        "source_name": "求人ボックス",
    }


# Backward-compatible alias
crawl_kyujin_box = crawl

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    items = crawl()
    print(f"Found {len(items)} items")
    for item in items[:3]:
        print(f"  - {item['title']} @ {item.get('company', '?')}")
