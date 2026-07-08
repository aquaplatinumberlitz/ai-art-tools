#!/usr/bin/env python3
"""Render job Hyogo report HTML from JSON data.

Usage:
  python3 render_job_hyogo_report.py <input_json_path> [output_html_path]

If output_html_path is omitted, it replaces .json with .html in input path.
"""

import json, sys, os, html as html_mod
from datetime import datetime
from typing import Any

# ── Style constants ──
STYLE = """
* { box-sizing: border-box; }
body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; background: #f6f4ef; color: #1f2933; margin: 0; padding: 16px; line-height: 1.5; }
.container { max-width: 960px; margin: 0 auto; }
h1 { color: #c46a2b; font-size: 1.4em; border-bottom: 2px solid #c46a2b; padding-bottom: 8px; margin: 0 0 16px; }
h2 { color: #1f2933; font-size: 1.1em; margin: 24px 0 10px; border-left: 3px solid #c46a2b; padding-left: 10px; }
h3 { font-size: 0.95em; margin: 16px 0 6px; color: #334155; }
.toc { background: #fff; border: 1px solid #e5e0d8; border-radius: 10px; padding: 12px 16px; margin: 12px 0; }
.toc a { color: #c46a2b; text-decoration: none; font-size: 0.88em; display: block; padding: 3px 0; }
.toc a:hover { text-decoration: underline; }
.summary-grid { display: grid; grid-template-columns: repeat(auto-fit, minmax(140px, 1fr)); gap: 10px; margin: 12px 0; }
.summary-card { background: #fff; border: 1px solid #e5e0d8; border-radius: 10px; padding: 14px 10px; text-align: center; }
.summary-card .num { font-size: 1.6em; font-weight: 700; color: #c46a2b; display: block; }
.summary-card .label { font-size: 0.78em; color: #64748b; display: block; margin-top: 2px; }
.job-card { background: #fff; border: 1px solid #e5e0d8; border-radius: 10px; padding: 14px; margin: 10px 0; }
.job-card .title { font-weight: 600; font-size: 1em; margin: 0 0 4px; }
.job-card .meta { font-size: 0.82em; color: #475569; display: flex; flex-wrap: wrap; gap: 4px 14px; margin: 4px 0; }
.job-card .meta strong { color: #1f2933; }
.job-card .why { font-size: 0.82em; color: #334155; margin: 6px 0 2px; padding: 6px 10px; background: #fefce8; border-radius: 6px; border-left: 3px solid #c46a2b; }
.job-card .risks { font-size: 0.78em; color: #991b1b; margin: 4px 0; }
.badge { display: inline-block; padding: 2px 7px; border-radius: 5px; font-size: 0.72em; font-weight: 600; margin: 2px 3px 2px 0; white-space: nowrap; }
.badge-ky-su { background: #e8f0fe; color: #1a56db; }
.badge-xuong { background: #fef3c7; color: #b45309; }
.badge-facebook { background: #e7f5ff; color: #1877f2; }
.badge-cty-lon { background: #ede9fe; color: #6d28d9; }
.badge-seishain { background: #dcfce7; color: #166534; }
.badge-keiyaku { background: #f0f9ff; color: #0369a1; }
.badge-haken { background: #fef2f2; color: #b91c1c; }
.badge-agency { background: #f5f5f4; color: #44403c; }
.badge-cao { background: #dcfce7; color: #166534; }
.badge-trung-binh { background: #fef3c7; color: #92400e; }
.badge-thap { background: #fef2f2; color: #991b1b; }
.badge-generic { background: #f1f5f9; color: #475569; }
.link-btn { display: inline-block; padding: 5px 12px; background: #c46a2b; color: #fff; border-radius: 6px; text-decoration: none; font-size: 0.8em; margin-top: 6px; }
.link-btn:hover { background: #a85722; }
.missing-info { font-size: 0.78em; color: #b91c1c; font-style: italic; margin-top: 4px; }
.table-wrap { overflow-x: auto; }
table { width: 100%; border-collapse: collapse; font-size: 0.82em; background: #fff; border-radius: 8px; }
th { background: #f1f5f9; text-align: left; padding: 8px 10px; border-bottom: 2px solid #e5e0d8; font-weight: 600; }
td { padding: 8px 10px; border-bottom: 1px solid #e5e0d8; }
.fb-item { background: #fff; border: 1px solid #e5e0d8; border-radius: 8px; padding: 10px 12px; margin: 6px 0; font-size: 0.85em; }
.fb-item .fb-meta { color: #64748b; font-size: 0.82em; }
.empty-msg { color: #94a3b8; font-style: italic; font-size: 0.88em; padding: 10px 0; }
.footer-note { font-size: 0.75em; color: #94a3b8; text-align: center; margin-top: 24px; padding-top: 12px; border-top: 1px solid #e5e0d8; }
@media (max-width:600px) { .summary-grid { grid-template-columns: repeat(2, 1fr); } .job-card { padding: 10px; } body { padding: 10px; } }
"""

def esc(text):
    """HTML escape."""
    if text is None:
        return ""
    return html_mod.escape(str(text))

def badge(text, cls, title=None):
    t = f' title="{esc(title)}"' if title else ''
    return f'<span class="badge badge-{cls}"{t}>{esc(text)}</span>'

def render_job_card(job, show_link=True):
    parts = []
    parts.append(f'<div class="job-card">')
    parts.append(f'<div class="title">{esc(job.get("title", ""))}</div>')

    # Badges
    badges_html = ""
    cat = job.get("job_category", "")
    etype = job.get("employment_type", "")
    fit = job.get("fit_level", "")
    is_agency = job.get("is_agency", False)
    is_large = job.get("is_large_company", False)

    if cat == "Engineer":
        badges_html += badge("Kỹ sư", "ky-su")
    elif cat == "Factory/Warehouse":
        badges_html += badge("Xưởng/kho", "xuong")
    elif cat == "Facebook":
        badges_html += badge("Facebook", "facebook")
    elif cat == "LargeCompany":
        badges_html += badge("Cty lớn", "cty-lon")

    if etype == "正社員":
        badges_html += badge("正社員", "seishain")
    elif etype == "契約社員":
        badges_html += badge("契約社員", "keiyaku")
    elif etype == "派遣":
        badges_html += badge("派遣", "haken")

    if is_agency:
        badges_html += badge("Agency", "agency")
    if is_large:
        badges_html += badge("Cty lớn", "cty-lon")

    for b in job.get("badges", []):
        if b not in ["Kỹ sư", "Xưởng/kho", "Facebook", "Cty lớn", "正社員", "契約社員", "派遣", "Agency"]:
            badges_html += badge(b, "generic")

    if fit == "Cao":
        badges_html += badge("Cao", "cao")
    elif fit == "Trung bình":
        badges_html += badge("Trung bình", "trung-binh")
    elif fit == "Thấp":
        badges_html += badge("Thấp", "thap")

    parts.append(f'<div>{badges_html}</div>')

    # Meta
    meta = []
    m = [
        ("Công ty", job.get("company")),
        ("Nguồn", job.get("source_name")),
        ("Khu vực", job.get("area")),
        ("Loại", job.get("job_category")),
        ("Hình thức", job.get("employment_type")),
        ("Lương", job.get("salary")),
        ("Ca làm", job.get("shift")),
        ("Yêu cầu Nhật", job.get("japanese_requirement")),
        ("Visa", job.get("visa")),
    ]
    for label, val in m:
        if val and str(val).strip() and str(val).strip() != "Unknown":
            meta.append(f'<strong>{esc(label)}:</strong> {esc(val)}')
    parts.append(f'<div class="meta">{" · ".join(meta)}</div>')

    # Why notable
    why = job.get("why_notable", "")
    if why:
        parts.append(f'<div class="why">⭐ {esc(why)}</div>')

    # Risks
    risks = job.get("risks", [])
    if risks:
        r_text = " ⚠️ " + " · ".join(esc(r) for r in risks)
        parts.append(f'<div class="risks">{r_text}</div>')

    # Missing info
    missing = job.get("missing_info", [])
    if missing:
        m_text = " ❓ Thiếu: " + ", ".join(esc(m) for m in missing)
        parts.append(f'<div class="missing-info">{m_text}</div>')

    # Duplicate sources
    dups = job.get("duplicate_sources", [])
    if dups:
        parts.append(f'<div class="missing-info">📎 Nguồn phụ: {", ".join(esc(d) for d in dups)}</div>')

    # Link button
    if show_link:
        url = job.get("source_url", "")
        if url:
            parts.append(f'<a class="link-btn" href="{esc(url)}" target="_blank" rel="noopener">🔗 Mở link gốc</a>')
        else:
            parts.append(f'<div class="missing-info">⚠️ Thiếu link gốc</div>')

    parts.append('</div>')
    return "\n".join(parts)

def render_compact_card(job):
    url = job.get("source_url", "")
    link = f' <a href="{esc(url)}" target="_blank" rel="noopener" style="color:#c46a2b;font-size:0.82em;">🔗</a>' if url else ''
    badge_fit = badge(job.get("fit_level", ""), job.get("fit_level", "").lower().replace(" ", "-") if job.get("fit_level") else "generic")
    return f'<div class="job-card" style="padding:10px 12px;"><strong>{esc(job.get("title",""))}</strong> · {esc(job.get("company",""))} · {esc(job.get("area",""))} · {esc(job.get("salary",""))} · {esc(job.get("employment_type",""))} {badge_fit}{link}</div>'

def render_fb_item(item):
    parts = [f'<div class="fb-item">']
    if item.get("group"):
        parts.append(f'<div><strong>Group:</strong> {esc(item["group"])}</div>')
    url = item.get("link", "")
    if url:
        parts.append(f'<div class="fb-meta"><a href="{esc(url)}" target="_blank" rel="noopener" style="color:#c46a2b;">🔗 Link bài</a></div>')
    detail = []
    for k, v in [("Ngày", "date"), ("Người đăng", "poster"), ("Nội dung", "content"),
                  ("Khu vực", "area"), ("Lương", "salary"), ("Công ty", "company"),
                  ("Môi giới?", "is_broker"), ("Mức độ", "fit_level"), ("Lý do giữ", "reason_kept")]:
        val = item.get(v, "")
        if val and str(val).strip():
            detail.append(f'<strong>{k}:</strong> {esc(val)}')
    if detail:
        parts.append(f'<div class="fb-meta">{" · ".join(detail)}</div>')
    parts.append('</div>')
    return "\n".join(parts)

def render(data):
    """Render JSON data dict to full HTML string."""
    rd = data.get("report_date", "Unknown")
    summary = data.get("summary", {})
    top = data.get("top_jobs", [])
    priority = data.get("priority_jobs", [])
    engineer = data.get("engineer_jobs", [])
    factory = data.get("factory_warehouse_jobs", [])
    large = data.get("large_company_jobs", [])
    fb = data.get("facebook_findings", [])
    rejected = data.get("rejected", {})
    fb_log = data.get("facebook_crawl_log", [])
    src_stats = data.get("source_stats", {})

    lines = []
    lines.append('<!DOCTYPE html>')
    lines.append('<html lang="vi">')
    lines.append('<head>')
    lines.append(f'<meta charset="UTF-8">')
    lines.append(f'<meta name="viewport" content="width=device-width,initial-scale=1.0">')
    lines.append(f'<title>Báo cáo job Hyogo - {esc(rd)}</title>')
    lines.append(f'<style>{STYLE}</style>')
    lines.append('</head>')
    lines.append('<body><div class="container">')

    # Title
    lines.append(f'<h1>📋 Báo cáo job Hyogo — {esc(rd)}</h1>')

    # TOC
    lines.append('<div class="toc"><strong>📑 Mục lục</strong>')
    sections = [
        ("summary", "Tổng quan"),
        ("top", "Top jobs"),
        ("priority", "Job ưu tiên cao"),
        ("engineer", "Job kỹ sư chuyển việc"),
        ("factory", "Job lao động phổ thông"),
        ("large", "Công ty lớn / Nhà máy lớn"),
        ("facebook", "Facebook crawl findings"),
        ("rejected", "Tin bị loại"),
    ]
    for anchor, label in sections:
        lines.append(f'<a href="#{anchor}">📌 {esc(label)}</a>')
    lines.append('</div>')

    # Summary cards
    lines.append(f'<h2 id="summary">📊 Tổng quan</h2>')
    lines.append('<div class="summary-grid">')
    cards = [
        ("Tổng job phù hợp", summary.get("total_matched", 0)),
        ("Kỹ sư chuyển việc", summary.get("engineer_jobs", 0)),
        ("LĐ phổ thông/xưởng/kho", summary.get("factory_warehouse_jobs", 0)),
        ("Facebook giữ lại", summary.get("facebook_kept", 0)),
        ("Công ty lớn", summary.get("large_company_jobs", 0)),
        ("Tin bị loại", summary.get("rejected_total", 0)),
    ]
    for label, val in cards:
        lines.append(f'<div class="summary-card"><span class="num">{int(val)}</span><span class="label">{esc(label)}</span></div>')
    lines.append('</div>')

    # Source stats
    if src_stats:
        lines.append('<div style="font-size:0.8em;color:#64748b;margin:8px 0;">')
        src_items = [f"{esc(k)}: {v}" for k, v in sorted(src_stats.items())]
        lines.append("Nguồn: " + " · ".join(src_items))
        lines.append('</div>')

    # Top jobs
    lines.append(f'<h2 id="top">🏆 Top 5 job nổi bật</h2>')
    if top:
        for job in top[:5]:
            lines.append(render_compact_card(job))
    else:
        lines.append('<div class="empty-msg">Không có.</div>')

    # Priority jobs
    lines.append(f'<h2 id="priority">⭐ Job ưu tiên cao</h2>')
    if priority:
        for job in priority:
            lines.append(render_job_card(job))
    else:
        lines.append('<div class="empty-msg">Không có job ưu tiên cao.</div>')

    # Engineer jobs
    lines.append(f'<h2 id="engineer">🔧 Job kỹ sư chuyển việc</h2>')
    if engineer:
        lines.append('<div class="table-wrap"><table><tr><th>Job</th><th>Công ty</th><th>KV</th><th>Hình thức</th><th>Link</th></tr>')
        for job in engineer:
            url = job.get("source_url", "")
            link_cell = f'<a href="{esc(url)}" target="_blank" rel="noopener" style="color:#c46a2b;">🔗</a>' if url else '—'
            lines.append(f'<tr><td>{esc(job.get("title",""))}</td><td>{esc(job.get("company",""))}</td><td>{esc(job.get("area",""))}</td><td>{esc(job.get("employment_type",""))}</td><td>{link_cell}</td></tr>')
        lines.append('</table></div>')
    else:
        lines.append('<div class="empty-msg">Không có job kỹ sư phù hợp kỳ này.</div>')

    # Factory jobs
    lines.append(f'<h2 id="factory">🏭 Job lao động phổ thông / xưởng / kho</h2>')
    if factory:
        lines.append('<div class="table-wrap"><table><tr><th>Job</th><th>Công ty</th><th>KV</th><th>Hình thức</th><th>Link</th></tr>')
        for job in factory:
            url = job.get("source_url", "")
            link_cell = f'<a href="{esc(url)}" target="_blank" rel="noopener" style="color:#c46a2b;">🔗</a>' if url else '—'
            lines.append(f'<tr><td>{esc(job.get("title",""))}</td><td>{esc(job.get("company",""))}</td><td>{esc(job.get("area",""))}</td><td>{esc(job.get("employment_type",""))}</td><td>{link_cell}</td></tr>')
        lines.append('</table></div>')
    else:
        lines.append('<div class="empty-msg">Không có job lao động phổ thông phù hợp kỳ này.</div>')

    # Large companies
    lines.append(f'<h2 id="large">🏢 Công ty lớn / Nhà máy lớn</h2>')
    if large:
        for job in large:
            lines.append(render_job_card(job))
    else:
        lines.append('<div class="empty-msg">Không tìm thấy job từ công ty lớn kỳ này. Một số trang career site công ty lớn có dynamic/JS/login-wall, không crawl được bằng static HTML — cần browser để kiểm tra trực tiếp.</div>')

    # Facebook
    lines.append(f'<h2 id="facebook">📘 Facebook crawl findings</h2>')
    if fb_log:
        lines.append('<div style="font-size:0.82em;color:#475569;margin:8px 0;">')
        for log_entry in fb_log:
            lines.append(f'<div>• Group {esc(log_entry.get("group",""))}: keyword "{esc(log_entry.get("keyword",""))}" → {log_entry.get("kept",0)} giữ, {log_entry.get("rejected",0)} loại{", " + esc(log_entry.get("note","")) if log_entry.get("note") else ""}</div>')
        lines.append('</div>')
    if fb:
        for item in fb:
            lines.append(render_fb_item(item))
    else:
        lines.append('<div class="empty-msg">Không có bài Facebook phù hợp kỳ này.</div>')

    # Rejected
    lines.append(f'<h2 id="rejected">🚫 Tin bị loại</h2>')
    rej_labels = [
        ("tokutei", "Tokutei / 特定技能"),
        ("baito", "Baito / バイト / パート"),
        ("it", "IT / lập trình"),
        ("construction", "Xây dựng / 建設"),
        ("unclear_company_salary_location", "Không rõ cty/lương/địa điểm"),
        ("suspicious_broker", "Môi giới mập mờ"),
        ("outside_area", "Ngoài khu vực"),
        ("other", "Khác"),
    ]
    lines.append('<div class="table-wrap"><table><tr><th>Lý do</th><th>Số lượng</th></tr>')
    total = 0
    for key, label in rej_labels:
        val = rejected.get(key, 0) or 0
        total += val
        if val > 0:
            lines.append(f'<tr><td>{esc(label)}</td><td>{val}</td></tr>')
    if total == 0:
        lines.append(f'<tr><td colspan="2" style="color:#94a3b8;font-style:italic;">Không có tin bị loại.</td></tr>')
    lines.append('</table></div>')

    lines.append(f'<div class="footer-note">Báo cáo tự động · Job Hyogo · {esc(rd)}</div>')
    lines.append('</div></body></html>')
    return "\n".join(lines)


# ── New functions for pipeline integration ──

def safe_int(v: Any, default: int = 0) -> int:
    """Safely convert a value to int."""
    try:
        return int(v)
    except (TypeError, ValueError):
        return default


def validate_report(data: dict) -> list[str]:
    """Validate report data structure. Returns list of issues (empty if valid)."""
    issues: list[str] = []

    if not data.get("report_date"):
        issues.append("Missing report_date")

    summary = data.get("summary", {})
    if not isinstance(summary, dict):
        issues.append("summary is not a dict")
    else:
        for field in ["total_matched", "engineer_jobs", "factory_warehouse_jobs",
                       "large_company_jobs", "facebook_kept", "rejected_total"]:
            if field not in summary:
                issues.append(f"summary missing: {field}")

    rejected = data.get("rejected", {})
    if not isinstance(rejected, dict):
        issues.append("rejected is not a dict")

    for section in ["top_jobs", "priority_jobs", "engineer_jobs",
                    "factory_warehouse_jobs", "large_company_jobs", "facebook_findings"]:
        items = data.get(section, [])
        if not isinstance(items, list):
            issues.append(f"{section} is not a list")

    if not issues:
        issues.append("OK")

    return issues


def build_telegram_summary(data: dict) -> str:
    """Build a Telegram-friendly summary string from the report data."""
    rd = data.get("report_date", "Unknown")
    summary = data.get("summary", {})
    src_stats = data.get("source_stats", {})
    rejected = data.get("rejected", {})

    lines: list[str] = []
    lines.append(f"**📋 Job Hyogo Report — {rd}**")
    lines.append("")

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

    if src_stats:
        lines.append(f"**📡 Nguồn:**")
        for k, v in sorted(src_stats.items()):
            lines.append(f"• {k}: {v}")
        lines.append("")

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

    priority = data.get("priority_jobs", [])
    if priority:
        lines.append(f"**⭐ Ưu tiên cao ({len(priority)} job):**")
        for job in priority[:5]:
            title = job.get("title", "?")
            company = job.get("company", "?")
            lines.append(f"• {title} @ {company}")
        lines.append("")

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
    return "\n".join(lines)


def cleanup_old_reports(report_dir: str, keep: int = 10) -> None:
    """Remove old report files, keeping the most recent `keep` reports."""
    import os
    from pathlib import Path

    path = Path(report_dir)
    if not path.exists():
        return

    report_files: list[Path] = []
    for ext in ("*.json", "*.html", "*.md"):
        report_files.extend(path.glob(f"job_hyogo_report*{ext}"))

    report_files.sort(key=lambda p: p.stat().st_mtime, reverse=True)

    if len(report_files) > keep:
        to_remove = report_files[keep:]
        for f in to_remove:
            try:
                f.unlink()
                print(f"  Cleaned up old report: {f.name}")
            except OSError as e:
                print(f"  Failed to remove {f.name}: {e}")

    print(f"  Cleanup: kept {min(len(report_files), keep)} reports, "
          f"removed {max(0, len(report_files) - keep)}")


def main():
    if len(sys.argv) < 2:
        print("Usage: render_job_hyogo_report.py <input_json> [output_html]", file=sys.stderr)
        sys.exit(1)

    input_path = sys.argv[1]
    if len(sys.argv) >= 3:
        output_path = sys.argv[2]
    else:
        output_path = input_path.replace(".json", ".html")

    with open(input_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    html_output = render(data)

    os.makedirs(os.path.dirname(output_path) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_output)

    print(f"✅ HTML report generated: {output_path}")

if __name__ == "__main__":
    main()
