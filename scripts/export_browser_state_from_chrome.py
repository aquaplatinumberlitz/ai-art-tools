#!/usr/bin/env python3
"""Export allowlisted Playwright storage_state from a running Chrome CDP session."""

import argparse
import json
import os
import platform
import stat
import sys
from pathlib import Path
from urllib.parse import urlparse

from playwright.sync_api import TimeoutError as PlaywrightTimeoutError
from playwright.sync_api import sync_playwright


SITES = {
    "seaart": {
        "urls": ["https://www.seaart.ai", "https://seaart.ai"],
        "origin": "https://www.seaart.ai",
        "output": "seaart_state.json",
    },
    "pixai": {
        "urls": ["https://pixai.art", "https://www.pixai.art"],
        "origin": "https://pixai.art",
        "output": "pixai_state.json",
    },
    "civitai": {
        "urls": ["https://civitai.com"],
        "origin": "https://civitai.com",
        "output": "civitai_state.json",
    },
    "huggingface": {
        "urls": ["https://huggingface.co"],
        "origin": "https://huggingface.co",
        "output": "huggingface_state.json",
    },
    "danbooru": {
        "urls": ["https://danbooru.donmai.us"],
        "origin": "https://danbooru.donmai.us",
        "output": "danbooru_state.json",
    },
    "reddit": {
        "urls": ["https://www.reddit.com", "https://reddit.com"],
        "origin": "https://www.reddit.com",
        "output": "reddit_state.json",
    },
}

CHROME_CDP_URL = os.environ.get("CHROME_CDP_URL", "http://127.0.0.1:9222")
BLOCKED_COOKIE_DOMAINS = ("google.com", "facebook.com")
LOCAL_CDP_HOSTS = {"127.0.0.1", "localhost"}


def parse_args():
    parser = argparse.ArgumentParser(
        description="Export allowlisted Playwright storage_state from running Chrome via local CDP."
    )
    group = parser.add_mutually_exclusive_group(required=True)
    group.add_argument("--site", choices=sorted(SITES), help="Allowlisted site to export.")
    group.add_argument("--all", action="store_true", help="Export all allowlisted sites.")
    parser.add_argument(
        "--allow-non-local-cdp",
        action="store_true",
        help="Allow CHROME_CDP_URL hosts other than 127.0.0.1 or localhost.",
    )
    return parser.parse_args()


def references_dir():
    configured = os.environ.get("HERMES_REFERENCES_DIR")
    if configured:
        return Path(configured).expanduser()
    if platform.system() == "Windows":
        home = os.environ.get("USERPROFILE") or str(Path.home())
        return Path(home) / "hermes-auth-export"
    return Path.home() / ".hermes" / "references"


def validate_cdp_url(cdp_url, allow_non_local):
    parsed = urlparse(cdp_url)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("CHROME_CDP_URL must be an http(s) URL with a hostname.")
    if not allow_non_local and parsed.hostname not in LOCAL_CDP_HOSTS:
        raise ValueError(
            "Refusing non-local CDP URL. Use 127.0.0.1 or localhost, or pass "
            "--allow-non-local-cdp for an explicit override."
        )


def hostname_for_url(url):
    return (urlparse(url).hostname or "").lower()


def allowed_hosts(site):
    return {hostname_for_url(url) for url in site["urls"]}


def cookie_domain_allowed(cookie, hosts):
    domain = cookie.get("domain", "").lstrip(".").lower()
    if not domain:
        return False
    if any(domain == blocked or domain.endswith("." + blocked) for blocked in BLOCKED_COOKIE_DOMAINS):
        return False
    return any(domain == host or host.endswith("." + domain) or domain.endswith("." + host) for host in hosts)


def page_matches_site(page, hosts):
    parsed = urlparse(page.url)
    host = (parsed.hostname or "").lower()
    return parsed.scheme in {"http", "https"} and host in hosts


def page_matches_origin(page, origin):
    parsed_page = urlparse(page.url)
    parsed_origin = urlparse(origin)
    return (
        parsed_page.scheme == parsed_origin.scheme
        and (parsed_page.hostname or "").lower() == (parsed_origin.hostname or "").lower()
        and (parsed_page.port or default_port(parsed_page.scheme))
        == (parsed_origin.port or default_port(parsed_origin.scheme))
    )


def default_port(scheme):
    return 443 if scheme == "https" else 80


def all_pages(browser):
    pages = []
    for context in browser.contexts:
        pages.extend(context.pages)
    return pages


def first_context(browser):
    if browser.contexts:
        return browser.contexts[0]
    return browser.new_context()


def wait_for_page(page):
    try:
        page.wait_for_load_state("load", timeout=30000)
    except PlaywrightTimeoutError:
        pass
    page.wait_for_timeout(1000)


def get_or_open_site_page(browser, context, site):
    hosts = allowed_hosts(site)
    for page in all_pages(browser):
        if page_matches_site(page, hosts):
            wait_for_page(page)
            return page

    page = context.new_page()
    page.goto(site["urls"][0], wait_until="domcontentloaded", timeout=60000)
    wait_for_page(page)
    return page


def get_or_open_origin_page(browser, context, site):
    for page in all_pages(browser):
        if page_matches_origin(page, site["origin"]):
            wait_for_page(page)
            return page

    page = context.new_page()
    page.goto(site["origin"], wait_until="domcontentloaded", timeout=60000)
    wait_for_page(page)
    return page


def export_local_storage(page):
    return page.evaluate(
        """() => Object.entries(window.localStorage).map(([name, value]) => ({ name, value }))"""
    )


def export_site(browser, site_name, site, out_dir):
    context = first_context(browser)
    get_or_open_site_page(browser, context, site)

    cookies = context.cookies(site["urls"])
    hosts = allowed_hosts(site)
    cookies = [cookie for cookie in cookies if cookie_domain_allowed(cookie, hosts)]

    origin_page = get_or_open_origin_page(browser, context, site)
    local_storage = export_local_storage(origin_page)

    state = {
        "cookies": cookies,
        "origins": [{"origin": site["origin"], "localStorage": local_storage}],
    }

    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / site["output"]
    with out_path.open("w", encoding="utf-8") as f:
        json.dump(state, f, indent=2, sort_keys=True)
        f.write("\n")

    try:
        out_path.chmod(stat.S_IRUSR | stat.S_IWUSR)
    except OSError:
        pass

    print(
        f"{site_name}: wrote {out_path} "
        f"({len(cookies)} cookies, {len(local_storage)} localStorage items)"
    )


def main():
    args = parse_args()
    cdp_url = CHROME_CDP_URL
    try:
        validate_cdp_url(cdp_url, args.allow_non_local_cdp)
    except ValueError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    selected = SITES if args.all else {args.site: SITES[args.site]}
    out_dir = references_dir()

    print(f"Connecting to Chrome CDP at {cdp_url}")
    print(f"Output directory: {out_dir}")
    print("Only allowlisted site cookies and origin localStorage will be exported.")

    with sync_playwright() as playwright:
        browser = playwright.chromium.connect_over_cdp(cdp_url)
        for site_name, site in selected.items():
            export_site(browser, site_name, site, out_dir)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
