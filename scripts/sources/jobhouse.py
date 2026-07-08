#!/usr/bin/env python3
"""Crawler for JobHouse (jobhouse.jp/factory).

HTML structure:
  div.articleItem
    div.articleItem-header
      a.articleItem-imageWrapper[href] — detail link
      div.articleItem-txt
        p.articleItem-corporation — company name
        h3.articleItem-title > a[href] — job title + link
        div.articleItem-labels
          div.articleItem-label — labels (交替制, 正社員, job type, features)
    div.articleItem-body — salary, location, description
"""

import logging
import re
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("job_hyogo.sources.jobhouse")

BASE_URL = "https://jobhouse.jp"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
}


def _normalize_url(url: str) -> str:
    """Normalize URL for dedup purposes."""
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)
    cleaned = parsed._replace(query="", fragment="")
    return urlunparse(cleaned).rstrip("/")


def crawl(url: str | None = None) -> list[dict[str, Any]]:
    """Crawl JobHouse factory job listings in Hyogo.

    Args:
        url: URL to crawl. Defaults to the Hyogo factory listing page.

    Returns:
        List of raw job item dicts.
    """
    target_url = url or "https://jobhouse.jp/factory/articles/p_28/f_32"
    logger.info("Crawling JobHouse: %s", target_url)
    raw_items: list[dict[str, Any]] = []

    try:
        resp = requests.get(target_url, headers=HEADERS, timeout=30)
        resp.encoding = "utf-8"
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("JobHouse crawl failed: %s", e)
        return raw_items

    soup = BeautifulSoup(resp.text, "html.parser")
    cards = soup.select("div.articleItem")

    if not cards:
        # Fallback: articleItem elements may not be wrapped in div
        cards = soup.select("[class*='articleItem']")
        # Filter to actual card containers (not children)
        cards = [c for c in cards if "articleItem" in (c.get("class", ""))]

    logger.info("Found %d job card(s) on JobHouse", len(cards))

    for card in cards:
        try:
            item = _parse_card(card)
            if item:
                raw_items.append(item)
        except Exception as e:
            logger.debug("Error parsing JobHouse card: %s", e)
            continue

    logger.info("JobHouse: parsed %d raw items", len(raw_items))
    return raw_items


def _parse_card(card) -> dict[str, Any] | None:
    """Parse a single JobHouse articleItem div."""
    txt_div = card.select_one(".articleItem-txt")
    if not txt_div:
        return None

    # Title
    title_el = txt_div.select_one("h3.articleItem-title a, h3.articleItem-title")
    if not title_el:
        return None
    title = title_el.get_text(strip=True)
    if not title or len(title) < 5:
        return None

    # URL
    link = ""
    if title_el.name == "a":
        link = str(title_el.get("href", "") or "")
    else:
        link_el = title_el.select_one("a[href]")
        if link_el:
            link = str(link_el.get("href", "") or "")
    if link and not link.startswith("http"):
        link = urljoin(BASE_URL, link)
    link = _normalize_url(link) if link else ""

    # Company
    company_el = txt_div.select_one(".articleItem-corporation")
    company = company_el.get_text(strip=True) if company_el else ""

    # Labels
    labels = txt_div.select(".articleItem-label")
    label_texts = [lbl.get_text(strip=True) for lbl in labels]

    # Determine employment type from labels
    employment_type = ""
    for lbl in label_texts:
        if lbl in ("正社員", "契約社員", "派遣", "無期雇用派遣"):
            employment_type = lbl
            break

    # Body info (salary, location, etc.)
    body = card.select_one(".articleItem-body")
    body_text = body.get_text(separator="\n", strip=True) if body else ""

    # Parse salary from body
    salary = ""
    if body:
        salary_el = body.select_one(".articleItem-salary, [class*='salary'], [class*='price']")
        if salary_el:
            salary = salary_el.get_text(strip=True)

    # Parse area from body or image alt
    area = ""
    img_el = card.select_one(".articleItem-image img")
    if img_el:
        alt = str(img_el.get("alt", "") or "")
        # Hyogo cities are often mentioned in alt text
        area_match = re.search(r'(兵庫県[^　\s]*)', alt)
        if area_match:
            area = area_match.group(1)

    # Also check body for area mentions
    if not area and body:
        area_match = re.search(r'(兵庫県[^　\s]*)', body_text)
        if area_match:
            area = area_match.group(1)

    # Description
    description = body_text[:500] if body_text else ""

    return {
        "title": title,
        "company": company,
        "area": area,
        "salary": salary,
        "employment_type": employment_type,
        "description": description,
        "url": link,
        "source_name": "JobHouse",
    }


# Backward-compatible alias
crawl_jobhouse = crawl

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    items = crawl()
    print(f"Found {len(items)} items")
    for item in items[:3]:
        print(f"  - {item['title'][:80]} @ {item.get('company', '?')} [{item.get('area', '')}]")
