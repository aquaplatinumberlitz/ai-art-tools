#!/usr/bin/env python3
"""Crawler for JOB Harima (job-harima.jp/recruitment).

HTML structure:
  div.sec_joboffer (per job posting)
    div.sec_besic-info
      h3 — job title
      p — details (recruitment period, etc.)
      ul.list_side — employment type and other info
    div.sec_joboffer-detail (hidden/show more)
"""

import logging
import re
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("job_hyogo.sources.job_harima")

BASE_URL = "https://www.job-harima.jp"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
}


def crawl(url: str | None = None) -> list[dict[str, Any]]:
    """Crawl JOB Harima recruitment listings.

    Args:
        url: URL to crawl. Defaults to the recruitment page.

    Returns:
        List of raw job item dicts.
    """
    target_url = url or f"{BASE_URL}/recruitment"
    logger.info("Crawling JOB Harima: %s", target_url)
    raw_items: list[dict[str, Any]] = []

    try:
        resp = requests.get(target_url, headers=HEADERS, timeout=30)
        resp.encoding = "utf-8"
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("JOB Harima crawl failed: %s", e)
        return raw_items

    soup = BeautifulSoup(resp.text, "html.parser")

    # Each job posting is a div.sec_joboffer
    cards = soup.select("div.sec_joboffer")

    if not cards:
        # Fallback: look for any div with job offer content
        cards = soup.select("div.inner.sec_work > div.contents > div")
        # Filter to those containing h3
        cards = [c for c in cards if c.select_one("h3")]

    if not cards:
        logger.info("JOB Harima: no job cards found (site may require JS)")
        return raw_items

    logger.info("Found %d job card(s) on JOB Harima", len(cards))

    for card in cards:
        try:
            item = _parse_card(card)
            if item:
                raw_items.append(item)
        except Exception as e:
            logger.debug("Error parsing JOB Harima card: %s", e)
            continue

    logger.info("JOB Harima: parsed %d raw items", len(raw_items))
    return raw_items


def _parse_card(card) -> dict[str, Any] | None:
    """Parse a single JOB Harima job offer div."""
    # Title
    title_el = card.select_one("h3")
    if not title_el:
        return None
    title = title_el.get_text(strip=True)
    # Remove "NEW" suffix if present
    title = re.sub(r'\s*NEW\s*$', '', title).strip()
    if not title or len(title) < 3:
        return None

    # Employment type from ul.list_side (often contains text like "正社員（既卒）")
    list_side = card.select_one("ul.list_side")
    employment_type = ""
    if list_side:
        employment_type = list_side.get_text(strip=True)

    # Normalize employment type
    if "正社員" in employment_type:
        employment_type = "正社員"
    elif "契約社員" in employment_type:
        employment_type = "契約社員"
    elif "派遣" in employment_type:
        employment_type = "派遣"

    # Description from the <p> sibling or other elements
    desc_el = card.select_one(".sec_besic-info p, p")
    description = desc_el.get_text(strip=True) if desc_el else ""

    # Area from title or description
    area = ""
    all_text = f"{title} {description}"
    # Look for Hyogo cities in text
    city_pattern = r'(兵庫県[^　\s)（]*|[^　\s)（]+市)'
    cities = re.findall(city_pattern, all_text)
    if cities:
        area = cities[0]

    # Salary
    salary = ""
    salary_match = re.search(r'(月給|時給|年収|日給)[^。]*\d+[万千万億]?円', all_text)
    if salary_match:
        salary = salary_match.group(0)

    return {
        "title": title,
        "company": "JOB Harima",  # Company info is usually not visible on list page
        "area": area,
        "salary": salary,
        "employment_type": employment_type,
        "description": description[:500] if description else "",
        "url": "",  # Detail pages are linked via buttons, hard to extract from list
        "source_name": "JOB Harima",
    }


# Backward-compatible alias
crawl_job_harima = crawl

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    items = crawl()
    print(f"Found {len(items)} items")
    for item in items[:5]:
        print(f"  - {item['title'][:60]} | {item.get('employment_type', '')} | {item.get('area', '')}")
