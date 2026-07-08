#!/usr/bin/env python3
"""Crawler for HelloWork Info (hello-work.info).

HTML structure:
  div.search-result-box (per job entry)
    a[href] — links to detail page
      div.search-result-box-main
        h2 — job title (includes company name in text)
        div — salary, location info
"""

import logging
import re
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("job_hyogo.sources.hello_work_info")

BASE_URL = "https://hello-work.info"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
}


def crawl(url: str | None = None) -> list[dict[str, Any]]:
    """Crawl HelloWork Info for foreigner-friendly jobs in Hyogo.

    Args:
        url: URL to crawl. Defaults to the Hyogo foreigner jobs page.

    Returns:
        List of raw job item dicts.
    """
    target_url = url or f"{BASE_URL}/%E5%A4%96%E5%9B%BD%E4%BA%BA%E9%96%A2%E9%80%A3%E3%81%AE%E6%B1%82%E4%BA%BA-%E5%85%B5%E5%BA%AB%E7%9C%8C/"
    logger.info("Crawling HelloWork Info: %s", target_url)
    raw_items: list[dict[str, Any]] = []

    try:
        resp = requests.get(target_url, headers=HEADERS, timeout=30)
        resp.encoding = "utf-8"
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("HelloWork crawl failed: %s", e)
        return raw_items

    soup = BeautifulSoup(resp.text, "html.parser")

    # Each job is in a div.search-result-box
    cards = soup.select("div.search-result-box")

    # If no results, look for alternative containers
    if not cards:
        # Check if there are any h2 elements with job-like text
        h2s = soup.select("h2")
        if h2s and len(h2s) > 5:
            # The h2 elements may be standalone in list
            for h2 in h2s:
                text = h2.get_text(strip=True)
                if any(kw in text for kw in ["求人", "募集", "採用", "正社員"]):
                    link = h2.find_parent("a")
                    raw_items.append({
                        "title": text[:200],
                        "company": "",
                        "area": "",
                        "salary": "",
                        "employment_type": "",
                        "description": "",
                        "url": urljoin(BASE_URL, str(link.get("href", ""))) if link else "",
                        "source_name": "HelloWork",
                    })
            logger.info("HelloWork: parsed %d items from h2 fallback", len(raw_items))
            return raw_items

    if not cards:
        logger.info("HelloWork: no job cards found")
        return raw_items

    logger.info("Found %d job card(s) on HelloWork", len(cards))

    for card in cards:
        try:
            item = _parse_card(card)
            if item:
                raw_items.append(item)
        except Exception as e:
            logger.debug("Error parsing HelloWork card: %s", e)
            continue

    logger.info("HelloWork: parsed %d raw items", len(raw_items))
    return raw_items


def _parse_card(card) -> dict[str, Any] | None:
    """Parse a single HelloWork search-result-box."""
    # Find the wrapping <a> for URL
    link_el = card if card.name == "a" else card.select_one("a[href]")
    url = ""
    if link_el:
        href = str(link_el.get("href", "") or "")
        if href:
            if not href.startswith("http"):
                href = urljoin(BASE_URL, href)
            url = href

    # Title from h2 inside the card
    title_el = card.select_one("h2")
    if not title_el:
        return None
    full_title = title_el.get_text(strip=True)
    if not full_title or len(full_title) < 5:
        return None

    # Try to split company from title
    # Format often: "JOB TITLE / COMPANY NAME"
    company = ""
    title = full_title
    if "／" in full_title:
        parts = full_title.split("／")
        if len(parts) >= 2:
            title = parts[0].strip()
            company = parts[1].strip()
    elif "／" in full_title:
        parts = full_title.split("／")
        if len(parts) >= 2:
            title = parts[0].strip()
            company = parts[1].strip()

    # Strip "新着" prefix
    title = re.sub(r'^新着', '', title).strip()
    company = re.sub(r'^新着', '', company).strip()

    # Salary and location from the card body
    # Look for text that contains salary info
    card_text = card.get_text(separator="\n", strip=True)
    salary = ""
    salary_match = re.search(r'(月給|時給|年収|日給)[^。\n]*\d+[万千万億]?円', card_text)
    if salary_match:
        salary = salary_match.group(0)

    # Area
    area = ""
    area_match = re.search(r'(兵庫県[^　\s)（\n]*)', card_text)
    if area_match:
        area = area_match.group(1)
    elif "兵庫県" in card_text:
        area = "兵庫県"

    # Employment type
    employment_type = ""
    for etype in ["正社員", "契約社員", "派遣", "無期雇用派遣", "パート", "アルバイト"]:
        if etype in card_text:
            employment_type = etype
            break

    return {
        "title": title,
        "company": company,
        "area": area,
        "salary": salary,
        "employment_type": employment_type,
        "description": card_text[:500],
        "url": url,
        "source_name": "HelloWork",
    }


# Backward-compatible alias
crawl_hello_work = crawl

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    items = crawl()
    print(f"Found {len(items)} items")
    for item in items[:5]:
        print(f"  - {item['title'][:60]} @ {item.get('company', '?')} | {item.get('area', '')} | {item.get('salary', '')}")
