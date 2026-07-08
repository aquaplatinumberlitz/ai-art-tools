#!/usr/bin/env python3
"""Crawler for 工場ワークス (04510.jp).

HTML structure:
  ul.p-contentSearchResult__jobList__list
    li.p-contentSearchResult__jobList__item (per job)
      div.p-jobCard
        div.p-jobCard__header
          div.p-jobCard__headerTitle
            h2.p-jobCard__title > a
            span.p-jobCard__subTitle — company name
        div.p-jobCard__body
          — salary, location, features
"""

import logging
import re
from typing import Any
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

logger = logging.getLogger("job_hyogo.sources.koujou_works")

BASE_URL = "https://04510.jp"

HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
    ),
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "ja,en;q=0.9",
}


def crawl(url: str | None = None) -> list[dict[str, Any]]:
    """Crawl 工場ワークス (Koujou Works) for Hyogo factory jobs.

    Args:
        url: URL to crawl. Defaults to Hyogo factory jobs page.

    Returns:
        List of raw job item dicts.
    """
    target_url = url or f"{BASE_URL}/jobs/areas/kansai/hyogo/B28210/"
    logger.info("Crawling 工場ワークス: %s", target_url)
    raw_items: list[dict[str, Any]] = []

    try:
        resp = requests.get(target_url, headers=HEADERS, timeout=30)
        resp.encoding = "utf-8"
        resp.raise_for_status()
    except requests.RequestException as e:
        logger.warning("工場ワークス crawl failed: %s", e)
        return raw_items

    soup = BeautifulSoup(resp.text, "html.parser")

    # Job cards are li items in the job list
    cards = soup.select("li.p-contentSearchResult__jobList__item")
    if not cards:
        cards = soup.select("div.p-jobCard")
    if not cards:
        cards = soup.select("li[class*='jobList']")
    if not cards:
        # Fallback: look for h2 elements with job-like text
        h2s = soup.select("h2")
        if h2s:
            for h2 in h2s:
                text = h2.get_text(strip=True)
                if any(kw in text for kw in ["求人", "募集", "月収", "時給"]) and len(text) > 30:
                    raw_items.append({
                        "title": text[:200],
                        "company": "",
                        "area": "",
                        "salary": "",
                        "employment_type": "",
                        "description": "",
                        "url": "",
                        "source_name": "工場ワークス",
                    })
            logger.info("工場ワークス: parsed %d items from h2 fallback", len(raw_items))
            return raw_items

    if not cards:
        logger.info("工場ワークス: no job cards found")
        return raw_items

    logger.info("Found %d job card(s) on 工場ワークス", len(cards))

    for card in cards:
        try:
            item = _parse_card(card)
            if item:
                raw_items.append(item)
        except Exception as e:
            logger.debug("Error parsing 工場ワークス card: %s", e)
            continue

    logger.info("工場ワークス: parsed %d raw items", len(raw_items))
    return raw_items


def _parse_card(card) -> dict[str, Any] | None:
    """Parse a single 工場ワークス job card."""
    # Title
    title_el = card.select_one("h2.p-cardJob__contents__ttl, h2[class*='ttl'], h2 a, h2")
    if not title_el:
        return None
    title = title_el.get_text(strip=True)
    if not title or len(title) < 5:
        return None

    # URL — look for the wrapping <a> with href
    url = ""
    link_el = card.select_one("a[href*='/jobs/'], a.p-cardJob__contents")
    if link_el:
        url = str(link_el.get("href", "") or "")
        if url and not url.startswith("http"):
            url = urljoin(BASE_URL, url)

    # Company — often embedded in title or in feature tags
    company_el = card.select_one("[class*='company'], [class*='subTitle']")
    company = company_el.get_text(strip=True) if company_el else ""

    # Full text
    card_text = card.get_text(separator="\n", strip=True)

    # Salary from dedicated element
    salary = ""
    salary_el = card.select_one("[class*='salary__amount'], [class*='salary']")
    if salary_el:
        salary = salary_el.get_text(strip=True)
    if not salary:
        salary_match = re.search(r'(月収|月給|時給|年収|日給)[^。\n]*\d+[,0-9万千万億]*円', card_text)
        if salary_match:
            salary = salary_match.group(0)

    # Area — often in the text
    area = ""
    area_match = re.search(r'(兵庫県[^　\s)（\n]*)', card_text)
    if area_match:
        area = area_match.group(1)
    if not area:
        city_match = re.search(r'[（(]([^)）]*[市町])[)）]', card_text)
        if city_match:
            area = city_match.group(1)

    # Employment type from feature tags or text
    employment_type = ""
    for etype in ["正社員", "契約社員", "派遣", "無期雇用派遣", "アルバイト", "パート"]:
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
        "url": url.rstrip("/") if url else "",
        "source_name": "工場ワークス",
    }


# Backward-compatible alias
crawl_koujou_works = crawl

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    items = crawl()
    print(f"Found {len(items)} items")
    for item in items[:5]:
        print(f"  - {item['title'][:60]} @ {item.get('company', '?')} | {item.get('area', '')} | {item.get('salary', '')}")
