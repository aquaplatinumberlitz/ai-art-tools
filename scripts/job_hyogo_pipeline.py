#!/usr/bin/env python3
"""Job Hyogo Pipeline — deterministic crawl → parse → filter → dedupe → rank → export → render.

Usage:
    python3 scripts/job_hyogo_pipeline.py --config config/job_hyogo.yaml

This script runs standalone with NO dependency on hermes_tools.
Telegram output is written to a file for Hermes cron to pick up.
"""

import argparse
import json
import logging
import os
import re
import subprocess
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import yaml

# Add scripts dir to path so imports work
_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from models import JobItem
from sources import (
    crawl_jobhouse,
    crawl_job_harima,
    crawl_hello_work,
    crawl_koujou_works,
    crawl_kyujin_box,
    crawl_facebook,
    crawl_official_careers,
)

logger = logging.getLogger("job_hyogo.pipeline")

# ── Type aliases ──
RawItem = dict[str, Any]
Config = dict[str, Any]

# ── Constants ──
SOURCE_TYPE_MAP: dict[str, str] = {
    "JobHouse": "JobHouse",
    "JOB Harima": "JOB Harima",
    "HelloWork": "HelloWork",
    "求人ボックス": "求人ボックス",
    "工場ワークス": "工場ワークス",
    "Facebook": "Facebook",
}

SOURCE_MAP: dict[str, str] = {
    "JobHouse": "JobHouse",
    "JOB Harima": "JOB Harima",
    "HelloWork": "HelloWork",
    "求人ボックス": "求人ボックス",
    "工場ワークス": "工場ワークス",
    "Facebook": "Facebook",
}


def safe_int(v: Any, default: int = 0) -> int:
    """Safely convert a value to int."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


# ═══════════════════════════════════════════
#  STEP 1: Load Config
# ═══════════════════════════════════════════
def load_config(config_path: str) -> Config:
    """Load YAML config file."""
    path = Path(config_path)
    if not path.exists():
        # Try relative to repo root
        alt_path = Path(_SCRIPTS_DIR).parent / config_path
        if alt_path.exists():
            path = alt_path
        else:
            logger.error("Config file not found: %s", config_path)
            sys.exit(1)

    with open(path, "r", encoding="utf-8") as f:
        cfg = yaml.safe_load(f)
    logger.info("Config loaded from %s", path)
    return cfg


# ═══════════════════════════════════════════
#  STEP 2: Crawl
# ═══════════════════════════════════════════
def _get_source_url(cfg: Config, source_key: str) -> str | None:
    """Get enabled source URL from config."""
    src = cfg.get("sources", {}).get(source_key, {})
    if src.get("enabled", False):
        return src.get("url", "")
    return None


def crawl_all(cfg: Config) -> dict[str, list[RawItem]]:
    """Run all enabled crawlers. Each crawler is isolated — failures don't stop the pipeline."""
    results: dict[str, list[RawItem]] = {}
    total = 0

    # Job sources
    source_crawlers = [
        ("jobhouse", crawl_jobhouse, _get_source_url(cfg, "jobhouse")),
        ("job_harima", crawl_job_harima, _get_source_url(cfg, "job_harima")),
        ("hello_work", crawl_hello_work, _get_source_url(cfg, "hello_work")),
        ("koujou_works", crawl_koujou_works, _get_source_url(cfg, "koujou_works")),
        ("kyujin_box", crawl_kyujin_box, _get_source_url(cfg, "kyujin_box")),
    ]

    for name, crawler, url in source_crawlers:
        if url is None:
            logger.info("Skipping disabled source: %s", name)
            results[name] = []
            continue
        try:
            items = crawler(url)
            results[name] = items
            total += len(items)
            logger.info("Crawl %s: %d items", name, len(items))
        except Exception as e:
            logger.warning("Crawl %s failed: %s", name, e)
            results[name] = []

    # Facebook
    fb_cfg = cfg.get("facebook", {})
    if fb_cfg.get("enabled", False):
        try:
            if fb_cfg.get("use_browser", False):
                # Browser-based crawl (requires Playwright + credentials)
                from sources.facebook_browser import crawl as crawl_facebook_browser
                from sources.facebook_browser import extract_job_posts
                group_configs = fb_cfg.get("groups", [])
                if group_configs:
                    logger.info("Starting browser-based Facebook crawl (%d groups)...", len(group_configs))
                    fb_result = crawl_facebook_browser(group_configs)
                    raw_posts = fb_result.get("posts", [])
                    errors = fb_result.get("errors", [])
                    logged_in = fb_result.get("logged_in", False)
                    if not logged_in:
                        logger.warning("Facebook login failed — falling back to static search")
                        fb_items = crawl_facebook()
                    else:
                        # Save raw posts
                        raw_dir = os.path.join(cfg.get("report", {}).get("output_dir", "reports"), "job_hyogo")
                        os.makedirs(raw_dir, exist_ok=True)
                        raw_file = os.path.join(raw_dir, f"facebook_raw_{datetime.now().strftime('%Y%m%d')}.json")
                        with open(raw_file, "w", encoding="utf-8") as f:
                            json.dump({"posts": raw_posts, "errors": errors, "logged_in": logged_in, "groups_visited": fb_result.get("groups_visited", 0), "groups_failed": fb_result.get("groups_failed", 0)}, f, ensure_ascii=False, indent=2)

                        # Extract job posts from raw
                        job_posts = extract_job_posts(raw_posts)
                        fb_items = []
                        for jp in job_posts:
                            item = JobItem(
                                id=str(uuid.uuid4()),
                                title=jp.get("title", "Facebook job post"),
                                company=jp.get("company", ""),
                                source_name="Facebook",
                                source_type="Facebook",
                                source_url=jp.get("source_url", ""),
                                area=jp.get("area", ""),
                                geo_tier=_geo_tier_for_area(jp.get("area", ""), cfg),
                                job_category="Facebook",
                                employment_type="Unknown",
                                salary=jp.get("salary", ""),
                                shift="",
                                japanese_requirement="",
                                visa="",
                                fit_level="Trung bình",
                                fit_score=3,
                                badges=["Facebook"],
                                why_notable="Facebook post",
                                risks=["Cần xác minh thông tin qua Facebook"],
                                missing_info=[],
                                is_agency=False,
                                is_large_company=False,
                                is_duplicate=False,
                                duplicate_sources=[],
                            )
                            fb_items.append(item)

                        logger.info("Browser Facebook crawl: %d raw, %d job posts, errors=%s",
                                    len(raw_posts), len(fb_items), errors)
                else:
                    logger.info("No Facebook groups configured — skipping browser crawl")
                    fb_items = []
            else:
                # Static HTTP search fallback
                fb_items = crawl_facebook()
                logger.info("Static Facebook search: %d items", len(fb_items))
            results["facebook"] = fb_items
            total += len(fb_items)
        except ImportError as e:
            logger.warning("Facebook browser module not available: %s — using static search", e)
            try:
                fb_items = crawl_facebook()
                results["facebook"] = fb_items
                total += len(fb_items)
            except Exception as e2:
                logger.warning("Static Facebook search also failed: %s", e2)
                results["facebook"] = []
        except Exception as e:
            logger.warning("Browser Facebook crawl failed: %s — falling back to static search", e)
            try:
                fb_items = crawl_facebook()
                results["facebook"] = fb_items
                total += len(fb_items)
            except Exception as e2:
                logger.warning("Static Facebook search also failed: %s", e2)
                results["facebook"] = []
    else:
        results["facebook"] = []

    # Official careers
    large_cos = cfg.get("large_companies", {})
    enabled_cos = {}
    for key, co in large_cos.items():
        if co.get("enabled", False):
            enabled_cos[key] = co
    if enabled_cos:
        try:
            co_items = crawl_official_careers(enabled_cos)
            results["official_careers"] = co_items
            total += len(co_items)
            logger.info("Crawl official_careers: %d items", len(co_items))
        except Exception as e:
            logger.warning("Crawl official_careers failed: %s", e)
            results["official_careers"] = []
    else:
        results["official_careers"] = []

    logger.info("Total raw items crawled: %d", total)
    return results


# ═══════════════════════════════════════════
#  STEP 3: Parse / Normalize to JobItem
# ═══════════════════════════════════════════
def _geo_tier_for_area(area: str, cfg: Config) -> str:
    """Determine geo tier based on area string."""
    if not area:
        return "Unknown"

    area_lower = area.lower()
    cities_a = [c.lower() for c in cfg.get("areas", {}).get("tier_a", {}).get("cities", [])]
    cities_b = [c.lower() for c in cfg.get("areas", {}).get("tier_b", {}).get("cities", [])]
    cities_c = [c.lower() for c in cfg.get("areas", {}).get("tier_c", {}).get("cities", [])]

    for city in cities_a:
        if city in area_lower:
            return "A"
    for city in cities_b:
        if city in area_lower:
            return "B"
    for city in cities_c:
        if city in area_lower:
            return "C"
    return "Unknown"


def _determine_job_category(item: JobItem, cfg: Config) -> str:
    """Determine job category based on title, company, and source."""
    text = f"{item.title} {item.company} {item.area}".lower()

    if item.source_type == "Facebook":
        return "Facebook"
    if item.is_large_company:
        return "LargeCompany"

    engineer_kw = ["engineer", "エンジニア", "技術", "mechanical", "電気", "生産技術"]
    factory_kw = ["factory", "工場", "製造", "ライン", "作業", "組立", "倉庫", "物流", "フォークリフト"]

    for kw in engineer_kw:
        if kw in text:
            return "Engineer"
    for kw in factory_kw:
        if kw in text:
            return "Factory/Warehouse"

    return "Other"


def _determine_employment_type(item: RawItem) -> str:
    """Parse employment type from raw item."""
    etype = (item.get("employment_type") or "").strip()
    title_desc = f"{item.get('title', '')} {item.get('description', '')}"

    if "正社員" in etype or "正社員" in title_desc:
        return "正社員"
    if "契約社員" in etype or "契約社員" in title_desc:
        return "契約社員"
    if "無期雇用派遣" in etype or "無期雇用派遣" in title_desc:
        return "無期雇用派遣"
    if "派遣" in etype or "派遣" in title_desc:
        return "派遣"
    return "Unknown"


def _determine_fit(item: JobItem, cfg: Config) -> tuple[str, int]:
    """Calculate fit level (Cao/Trung bình/Thấp) and score (1-5)."""
    score = 3
    reasons: list[str] = []

    # Geo tier bonus
    if item.geo_tier == "A":
        score += 1
        reasons.append("Tier A area")
    elif item.geo_tier == "B":
        score += 0
    elif item.geo_tier == "C":
        score -= 0

    # Employment type bonus
    if item.employment_type == "正社員":
        score += 1
        reasons.append("正社員")
    elif item.employment_type == "契約社員":
        score += 0
    elif item.employment_type == "派遣":
        score -= 0

    # Large company bonus
    if item.is_large_company:
        score += 1
        reasons.append("Large company")

    # Local source bonus
    if item.source_type == "JOB Harima":
        score += 1
        reasons.append("Local direct source")

    # Agency penalty
    if item.is_agency:
        score -= 1
        reasons.append("Agency")

    # Missing info penalty
    missing_penalty = len(item.missing_info)
    score -= missing_penalty

    # Clamp
    score = max(1, min(5, score))

    if score >= 4:
        return "Cao", score
    elif score >= 3:
        return "Trung bình", score
    else:
        return "Thấp", score


def _is_agency(source_name: str, title: str, company: str) -> bool:
    """Detect if a job is from an agency/recruiter."""
    text = f"{source_name} {title} {company}".lower()
    agency_kw = [
        "jobhouse", "agent", "agency", "recruit", "staff", "service",
        "人材", "派遣", "紹介", "スタッフ",
    ]
    for kw in agency_kw:
        if kw in text:
            return True
    return False


def normalize_raw_items(
    raw_results: dict[str, list[RawItem]],
    cfg: Config,
) -> list[JobItem]:
    """Normalize all raw items into JobItem dataclass instances."""
    items: list[JobItem] = []

    for source_key, raw_list in raw_results.items():
        for raw in raw_list:
            try:
                source_name = raw.get("source_name", "") or source_key
                source_type = SOURCE_TYPE_MAP.get(source_name, "Other")
                if source_name in cfg.get("large_companies", {}):
                    source_type = "Official Career"

                title = (raw.get("title") or "").strip()
                if not title:
                    continue

                company = (raw.get("company") or "").strip()
                area = (raw.get("area") or "").strip()
                salary = (raw.get("salary") or "").strip()
                url = (raw.get("url") or "").strip()

                is_large_company = any(
                    co_name.lower() in (company + title).lower()
                    for co_key, co in cfg.get("large_companies", {}).items()
                    if (co_name := co.get("name", ""))
                )

                geo_tier = _geo_tier_for_area(area, cfg)

                item = JobItem(
                    title=title,
                    raw_title=title,
                    company=company,
                    source_name=source_name,
                    source_type=source_type,
                    source_url=url,
                    area=area,
                    geo_tier=geo_tier,
                    salary=salary,
                    employment_type=_determine_employment_type(raw),
                    is_agency=_is_agency(source_name, title, company),
                    is_large_company=is_large_company,
                )

                item.job_category = _determine_job_category(item, cfg)
                fit_level, fit_score = _determine_fit(item, cfg)
                item.fit_level = fit_level
                item.fit_score = fit_score

                items.append(item)
            except Exception as e:
                logger.debug("Error normalizing raw item: %s", e)
                continue

    logger.info("Normalized %d JobItems from raw data", len(items))
    return items


# ═══════════════════════════════════════════
#  STEP 4: Filter
# ═══════════════════════════════════════════
def _get_exclude_keywords(cfg: Config) -> list[str]:
    """Get exclusion keywords from config."""
    return [kw.lower() for kw in cfg.get("keywords", {}).get("exclude", [])]


def _get_include_keywords(cfg: Config) -> list[str]:
    """Get inclusion keywords from config."""
    return [kw.lower() for kw in cfg.get("keywords", {}).get("include", [])]


def filter_items(items: list[JobItem], cfg: Config) -> tuple[list[JobItem], dict[str, int]]:
    """Filter out excluded jobs. Returns (kept, rejected_counts)."""
    excluded_kw = _get_exclude_keywords(cfg)
    # included_kw = _get_include_keywords(cfg)  # For future use

    kept: list[JobItem] = []
    rejected: dict[str, int] = {
        "tokutei": 0,
        "baito": 0,
        "it": 0,
        "construction": 0,
        "unclear_company_salary_location": 0,
        "suspicious_broker": 0,
        "outside_area": 0,
        "other": 0,
    }

    for item in items:
        text = f"{item.title} {item.company} {item.area}".lower()

        should_exclude = False
        reason = "other"

        for kw in excluded_kw:
            if kw.lower() in text:
                should_exclude = True
                if kw in ("特定技能", "技能実習"):
                    reason = "tokutei"
                elif kw in ("アルバイト", "バイト", "パート"):
                    reason = "baito"
                elif kw in ("it", "se", "プログラマー", "webエンジニア", "開発"):
                    reason = "it"
                elif kw in ("建設", "土木", "施工管理"):
                    reason = "construction"
                break

        if should_exclude:
            rejected[reason] = rejected.get(reason, 0) + 1
            continue

        # Skip items with no title/company/area (unclear)
        if not item.title or (not item.company and not item.area and not item.salary):
            rejected["unclear_company_salary_location"] = (
                rejected.get("unclear_company_salary_location", 0) + 1
            )
            continue

        kept.append(item)

    logger.info(
        "Filter: kept %d, rejected: %s",
        len(kept),
        {k: v for k, v in rejected.items() if v > 0},
    )
    return kept, rejected


# ═══════════════════════════════════════════
#  STEP 5: Deduplicate
# ═══════════════════════════════════════════
def _normalize_text(text: str) -> str:
    """Normalize text for comparison."""
    return re.sub(r"\s+", " ", text.lower().strip())


def _normalize_url(url: str) -> str:
    """Normalize URL for dedup comparison."""
    from urllib.parse import urlparse, urlunparse

    parsed = urlparse(url)
    # Remove query and fragment
    cleaned = parsed._replace(query="", fragment="")
    return urlunparse(cleaned).rstrip("/")


def deduplicate(items: list[JobItem]) -> list[JobItem]:
    """Deduplicate jobs by URL, title+company+area, and fuzzy title.

    Returns deduplicated list; sets is_duplicate and duplicate_sources on kept items.
    """
    deduped: list[JobItem] = []
    seen_urls: dict[str, JobItem] = {}
    seen_texts: dict[str, JobItem] = {}

    for item in items:
        is_dup = False
        dup_sources: list[str] = []

        # 1. Check URL
        norm_url = _normalize_url(item.source_url) if item.source_url else ""
        if norm_url and norm_url in seen_urls:
            existing = seen_urls[norm_url]
            existing.is_duplicate = True
            if item.source_name not in existing.duplicate_sources:
                existing.duplicate_sources.append(item.source_name)
            is_dup = True
            dup_sources.append(existing.source_name)

        # 2. Check title+company+area
        if not is_dup:
            text_key = f"{_normalize_text(item.title)}|{_normalize_text(item.company)}|{_normalize_text(item.area)}"
            if text_key in seen_texts and text_key.count("|") == 3:
                existing = seen_texts[text_key]
                existing.is_duplicate = True
                if item.source_name not in existing.duplicate_sources:
                    existing.duplicate_sources.append(item.source_name)
                is_dup = True
                dup_sources.append(existing.source_name)

        if is_dup:
            item.is_duplicate = True
            item.duplicate_sources = dup_sources

        # Skip exact URL duplicates
        if norm_url and norm_url in seen_urls:
            continue

        # Register
        if item.source_url:
            seen_urls[_normalize_url(item.source_url)] = item
        text_key = f"{_normalize_text(item.title)}|{_normalize_text(item.company)}|{_normalize_text(item.area)}"
        if text_key not in seen_texts:
            seen_texts[text_key] = item

        deduped.append(item)

    logger.info("Dedup: %d items after dedup (removed %d URL duplicates)", len(deduped), len(items) - len(deduped))
    return deduped


# ═══════════════════════════════════════════
#  STEP 6: Rank
# ═══════════════════════════════════════════
def _employment_rank(etype: str) -> int:
    """Rank employment types for sorting."""
    ranks = {
        "正社員": 4,
        "契約社員": 3,
        "無期雇用派遣": 2,
        "派遣": 1,
    }
    return ranks.get(etype, 0)


def _source_rank(source_type: str) -> int:
    """Rank source types for sorting. Prefer local direct sources."""
    ranks = {
        "JOB Harima": 5,
        "HelloWork": 4,
        "工場ワークス": 3,
        "JobHouse": 2,
        "求人ボックス": 2,
        "Official Career": 4,
        "Facebook": 1,
    }
    return ranks.get(source_type, 0)


def _completeness_score(item: JobItem) -> int:
    """Score how complete a job listing is (more info = higher score)."""
    score = 0
    if item.source_url:
        score += 2
    if item.salary and item.salary not in ("", "Unknown"):
        score += 2
    if item.area and item.area not in ("", "Unknown"):
        score += 1
    if item.company and item.company not in ("", "Unknown"):
        score += 1
    if item.employment_type not in ("", "Unknown"):
        score += 1
    if item.shift and item.shift not in ("", "Unknown"):
        score += 1
    if item.japanese_requirement and item.japanese_requirement not in ("", "Unknown"):
        score += 1
    return score


def rank_items(items: list[JobItem]) -> list[JobItem]:
    """Rank items by multiple criteria. Returns sorted list (best first)."""
    ranked = sorted(
        items,
        key=lambda i: (
            # Tier A > B > C > Unknown
            {"A": 3, "B": 2, "C": 1, "Unknown": 0}.get(i.geo_tier, 0),
            # Fit score descending
            i.fit_score,
            # Employment type: 正社員 > 契約社員 > 無期雇用派遣 > 派遣
            _employment_rank(i.employment_type),
            # Source preference: local direct > agency > other
            _source_rank(i.source_type),
            # Completeness
            _completeness_score(i),
            # Large company bonus
            1 if i.is_large_company else 0,
            # Facebook lower priority
            0 if i.source_type == "Facebook" else 1,
        ),
        reverse=True,
    )
    logger.info("Ranked %d items", len(ranked))
    return ranked


# ═══════════════════════════════════════════
#  STEP 7: Categorize
# ═══════════════════════════════════════════
def categorize_jobs(items: list[JobItem]) -> dict[str, Any]:
    """Split ranked items into categories for the report."""
    top_jobs = items[:5]
    priority_jobs = [i for i in items if i.fit_level == "Cao"][:10]
    engineer_jobs = [i for i in items if i.job_category == "Engineer"]
    factory_jobs = [i for i in items if i.job_category == "Factory/Warehouse"]
    large_company_jobs = [i for i in items if i.is_large_company]
    facebook_jobs = [i for i in items if i.source_type == "Facebook"]

    return {
        "top_jobs": [i.to_dict() for i in top_jobs],
        "priority_jobs": [i.to_dict() for i in priority_jobs],
        "engineer_jobs": [i.to_dict() for i in engineer_jobs],
        "factory_warehouse_jobs": [i.to_dict() for i in factory_jobs],
        "large_company_jobs": [i.to_dict() for i in large_company_jobs],
        "facebook_findings": [i.to_dict() for i in facebook_jobs],
    }


# ═══════════════════════════════════════════
#  STEP 8: Export JSON
# ═══════════════════════════════════════════
def export_json(data: dict[str, Any], output_path: str) -> str:
    """Export data to JSON file."""
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2, default=str)
    logger.info("JSON exported: %s", path)
    return str(path)


# ═══════════════════════════════════════════
#  STEP 9: Render HTML
# ═══════════════════════════════════════════
def render_html(json_path: str, cfg: Config) -> str | None:
    """Call the external render script to produce HTML."""
    renderer = cfg.get("report", {}).get("renderer_script", "scripts/render_job_hyogo_report.py")
    renderer_path = Path(_SCRIPTS_DIR) / renderer
    if not renderer_path.exists():
        renderer_path = Path(_SCRIPTS_DIR) / "render_job_hyogo_report.py"

    if not renderer_path.exists():
        logger.error("Renderer script not found: %s", renderer_path)
        return None

    html_path = json_path.replace(".json", ".html")
    try:
        result = subprocess.run(
            [sys.executable, str(renderer_path), json_path, html_path],
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode == 0:
            logger.info("HTML rendered: %s", html_path)
            return html_path
        else:
            logger.warning("HTML render stderr: %s", result.stderr)
            return None
    except subprocess.TimeoutExpired:
        logger.warning("HTML render timed out")
        return None
    except Exception as e:
        logger.warning("HTML render failed: %s", e)
        return None


# ═══════════════════════════════════════════
#  STEP 10: Build Telegram Summary
# ═══════════════════════════════════════════
def build_telegram_summary(
    data: dict[str, Any],
    cfg: Config,
) -> str:
    """Build a Telegram-friendly summary string from the report data."""
    rd = data.get("report_date", "Unknown")
    summary = data.get("summary", {})
    src_stats = data.get("source_stats", {})
    rejected = data.get("rejected", {})

    lines: list[str] = []
    lines.append(f"**📋 Job Hyogo Report — {rd}**")
    lines.append("")

    # Summary cards
    total = summary.get("total_matched", 0)
    engineer = summary.get("engineer_jobs", 0)
    factory = summary.get("factory_warehouse_jobs", 0)
    large = summary.get("large_company_jobs", 0)
    facebook = summary.get("facebook_kept", 0)
    rejected_total = summary.get("rejected_total", 0)

    lines.append(f"📊 **Tổng quan:**")
    lines.append(f"• Tổng job phù hợp: {total}")
    lines.append(f"• Kỹ sư chuyển việc: {engineer}")
    lines.append(f"• LĐ phổ thông/xưởng/kho: {factory}")
    lines.append(f"• Công ty lớn: {large}")
    lines.append(f"• Facebook giữ lại: {facebook}")
    lines.append(f"• Tin bị loại: {rejected_total}")
    lines.append("")

    # Source stats
    if src_stats:
        lines.append(f"**📡 Nguồn:**")
        for k, v in sorted(src_stats.items()):
            lines.append(f"• {k}: {v}")
        lines.append("")

    # Top jobs
    top_jobs = data.get("top_jobs", [])
    if top_jobs:
        lines.append(f"**🏆 Top jobs:**")
        for i, job in enumerate(top_jobs[:5], 1):
            title = job.get("title", "?")
            company = job.get("company", "?")
            area = job.get("area", "?")
            salary = job.get("salary", "")
            fit = job.get("fit_level", "")
            salary_str = f" · 💰{salary}" if salary else ""
            lines.append(f"{i}. {title} @ {company} ({area}){salary_str} [{fit}]")
        lines.append("")

    # Priority jobs
    priority = data.get("priority_jobs", [])
    if priority:
        lines.append(f"**⭐ Ưu tiên cao ({len(priority)} job):**")
        for job in priority[:5]:
            title = job.get("title", "?")
            company = job.get("company", "?")
            lines.append(f"• {title} @ {company}")
        lines.append("")

    # Rejected breakdown
    if rejected_total > 0:
        lines.append(f"**🚫 Bị loại:**")
        for key, label in [
            ("tokutei", "Tokutei"),
            ("baito", "Baito/Part"),
            ("it", "IT/Lập trình"),
            ("construction", "Xây dựng"),
            ("unclear_company_salary_location", "Thiếu thông tin"),
            ("other", "Khác"),
        ]:
            val = rejected.get(key, 0) or 0
            if val > 0:
                lines.append(f"• {label}: {val}")
        lines.append("")

    lines.append(f"📁 Chi tiết: xem file HTML đính kèm.")
    lines.append("")

    return "\n".join(lines)


# ═══════════════════════════════════════════
#  STEP 11: Cleanup Old Reports
# ═══════════════════════════════════════════
def cleanup_old_reports(report_dir: str, keep: int = 10) -> None:
    """Remove old report files, keeping the most recent `keep` reports."""
    path = Path(report_dir)
    if not path.exists():
        return

    # Find all report JSON and HTML files
    report_files: list[Path] = []
    for ext in ("*.json", "*.html", "*.md"):
        report_files.extend(path.glob(f"job_hyogo_report*{ext[1:]}"))

    # Sort by modification time (newest first)
    report_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    if len(report_files) > keep:
        to_remove = report_files[keep:]
        for f in to_remove:
            try:
                f.unlink()
                logger.info("Cleaned up old report: %s", f.name)
            except OSError as e:
                logger.warning("Failed to remove %s: %s", f.name, e)

    logger.info("Cleanup: kept %d reports, removed %d", min(len(report_files), keep), max(0, len(report_files) - keep))


# ═══════════════════════════════════════════
#  MAIN PIPELINE
# ═══════════════════════════════════════════
def run_pipeline(cfg_path: str) -> dict[str, Any]:
    """Run the full job Hyogo pipeline end-to-end.

    Returns a dict with paths and summary data for the caller.
    """
    start_time = datetime.now(timezone.utc)
    report_date = start_time.strftime("%Y-%m-%d_%H-%M-%S")
    report_date_hr = start_time.strftime("%Y-%m-%d %H:%M")

    logger.info("=" * 60)
    logger.info("Job Hyogo Pipeline starting")
    logger.info("Config: %s", cfg_path)
    logger.info("Date: %s", report_date)
    logger.info("=" * 60)

    # 1. Load config
    cfg = load_config(cfg_path)

    # 2. Crawl
    raw_results = crawl_all(cfg)

    # 3. Normalize / Parse
    items = normalize_raw_items(raw_results, cfg)

    # 4. Filter
    items, rejected_counts = filter_items(items, cfg)

    # 5. Deduplicate
    items = deduplicate(items)

    # 6. Rank
    items = rank_items(items)

    # 7. Categorize
    report_dir = cfg.get("report", {}).get("output_dir", "reports")
    prefix = cfg.get("report", {}).get("filename_prefix", "job_hyogo_report")
    json_filename = f"{prefix}_{report_date}.json"

    report_dir_abs = Path(_SCRIPTS_DIR).parent / report_dir
    report_dir_abs.mkdir(parents=True, exist_ok=True)
    json_path = str(report_dir_abs / json_filename)

    # Source stats with display names
    display_names = {
        "jobhouse": "JobHouse",
        "job_harima": "JOB Harima",
        "hello_work": "HelloWork",
        "koujou_works": "工場ワークス",
        "kyujin_box": "求人ボックス",
        "facebook": "Facebook",
        "official_careers": "Career Pages",
    }
    source_stats: dict[str, int] = {}
    for source_key, raw_list in raw_results.items():
        display = display_names.get(source_key, source_key)
        source_stats[display] = len(raw_list)

    # Build data dict
    categorized = categorize_jobs(items)
    data: dict[str, Any] = {
        "report_date": report_date_hr,
        "report_timestamp": start_time.isoformat(),
        "summary": {
            "total_matched": len(items),
            "engineer_jobs": len(categorized["engineer_jobs"]),
            "factory_warehouse_jobs": len(categorized["factory_warehouse_jobs"]),
            "large_company_jobs": len(categorized["large_company_jobs"]),
            "facebook_kept": len(categorized["facebook_findings"]),
            "rejected_total": sum(rejected_counts.values()),
        },
        "source_stats": source_stats,
        "rejected": rejected_counts,
        "all_jobs": [i.to_dict() for i in items],
        **categorized,
        "facebook_crawl_log": [],
    }

    # 8. Export JSON
    json_path = export_json(data, json_path)

    # 9. Render HTML
    html_path = render_html(json_path, cfg)

    # 10. Telegram summary
    telegram_output = build_telegram_summary(data, cfg)

    # Write telegram summary to file
    telegram_dir = cfg.get("telegram", {}).get("output_file", "reports/telegram_summary.md")
    telegram_path = Path(_SCRIPTS_DIR).parent / telegram_dir
    telegram_path.parent.mkdir(parents=True, exist_ok=True)
    with open(telegram_path, "w", encoding="utf-8") as f:
        f.write(telegram_output)
    logger.info("Telegram summary written: %s", telegram_path)

    # 11. Cleanup old reports
    max_keep = safe_int(cfg.get("report", {}).get("max_reports_to_keep", 10), 10)
    cleanup_old_reports(str(report_dir_abs), keep=max_keep)

    elapsed = (datetime.now(timezone.utc) - start_time).total_seconds()
    logger.info("=" * 60)
    logger.info("Pipeline complete in %.1f seconds", elapsed)
    logger.info("JSON: %s", json_path)
    logger.info("HTML: %s", html_path or "N/A")
    logger.info("Telegram: %s", telegram_path)
    logger.info("=" * 60)

    return {
        "json_path": json_path,
        "html_path": html_path,
        "telegram_path": str(telegram_path),
        "total_items": len(items),
        "elapsed_seconds": elapsed,
    }


def main() -> None:
    """CLI entry point."""
    parser = argparse.ArgumentParser(description="Job Hyogo Pipeline")
    parser.add_argument(
        "--config",
        default="config/job_hyogo.yaml",
        help="Path to YAML config file",
    )
    parser.add_argument(
        "--log-level",
        default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
        help="Logging level",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Only crawl and parse, don't export or render",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stdout,
    )

    result = run_pipeline(args.config)

    if args.dry_run:
        logger.info("DRY RUN: Skipped export/render/cleanup")
        return

    print(f"\n✅ Pipeline complete!")
    print(f"   JSON: {result['json_path']}")
    print(f"   HTML: {result.get('html_path', 'N/A')}")
    print(f"   Telegram: {result['telegram_path']}")
    print(f"   Total jobs: {result['total_items']}")
    print(f"   Elapsed: {result['elapsed_seconds']:.1f}s")


if __name__ == "__main__":
    main()
