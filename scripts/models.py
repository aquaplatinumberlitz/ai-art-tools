#!/usr/bin/env python3
"""JobItem dataclass and related types for the Job Hyogo pipeline."""

from dataclasses import dataclass, field, asdict
from typing import Optional
import uuid


@dataclass
class JobItem:
    """Normalized job listing item."""

    id: str = field(default_factory=lambda: uuid.uuid4().hex[:12])
    title: str = ""
    company: str = ""
    source_name: str = ""        # Human-readable source name
    source_type: str = "Other"   # Facebook | JobHouse | JOB Harima | HelloWork | 求人ボックス | 工場ワークス | Official Career | Other
    source_url: str = ""
    area: str = ""
    geo_tier: str = "Unknown"    # A | B | C | Unknown
    job_category: str = "Other"  # Engineer | Factory/Warehouse | LargeCompany | Facebook | Other
    employment_type: str = "Unknown"  # 正社員 | 契約社員 | 派遣 | 無期雇用派遣 | Unknown
    salary: str = ""
    shift: str = ""
    japanese_requirement: str = ""
    visa: str = ""
    fit_level: str = "Trung bình"  # Cao | Trung bình | Thấp
    fit_score: int = 3           # 1-5
    badges: list[str] = field(default_factory=list)
    why_notable: str = ""
    risks: list[str] = field(default_factory=list)
    missing_info: list[str] = field(default_factory=list)
    is_agency: bool = False
    is_large_company: bool = False
    is_duplicate: bool = False
    duplicate_sources: list[str] = field(default_factory=list)
    raw_title: str = ""

    def to_dict(self) -> dict:
        """Convert to dict for JSON serialization."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict) -> "JobItem":
        """Create JobItem from dict (for deserialization)."""
        valid_fields = {f.name for f in cls.__dataclass_fields__.values()}
        filtered = {k: v for k, v in data.items() if k in valid_fields}
        return cls(**filtered)
