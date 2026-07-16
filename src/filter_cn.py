"""
filter_cn.py — lightweight keyword pre-filter for the Nowcoder pipeline.
Mirrors filter.py's role (cheap filter before the Claude scoring call) but
NOT its matching mechanics: filter.py uses \\b word-boundary regex, which
works for space-delimited English words but silently breaks on Chinese text
(CJK characters are all "\\w" to Python's re engine, and Chinese has no
spaces between words — "数据" inside "大数据分析师" has no word boundary
before it, so a \\b数据\\b pattern would never match real titles). This
module uses plain substring containment instead, which is what Chinese
keyword matching actually needs.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_config_path = Path(__file__).parent.parent / "config" / "filters_cn.json"
with open(_config_path, encoding="utf-8") as f:
    FILTERS = json.load(f)

INCLUDE_CAREER_JOB = [kw.strip() for kw in FILTERS["include_career_job_keywords"]]
EXCLUDE_TITLE = [kw.strip() for kw in FILTERS["exclude_title_keywords"]]
TARGET_CITY = FILTERS["target_city"]


def _contains_any(text: str, keywords: list[str]) -> str | None:
    for kw in keywords:
        if kw and kw.lower() in text.lower():
            return kw
    return None


def passes_category_filter(job: dict) -> bool:
    """Returns True if the job should be sent to AI scoring. Despite the
    name, this also enforces the location filter — job discovery is no
    longer company-gated (see discover_cn.py), so job content + location are
    the only two filter axes left, and they're naturally checked together."""
    title = job.get("title", "")
    career_job_name = job.get("career_job_name", "")
    location = job.get("location", "")

    if TARGET_CITY not in location:
        logger.debug(f"EXCLUDED (location '{location}' != {TARGET_CITY}): {job['company']} — {title}")
        return False

    hit = _contains_any(title, EXCLUDE_TITLE)
    if hit:
        logger.debug(f"EXCLUDED (keyword '{hit}'): {job['company']} — {title}")
        return False

    category_match = _contains_any(career_job_name, INCLUDE_CAREER_JOB) is not None
    if not category_match:
        logger.debug(f"EXCLUDED (category mismatch '{career_job_name}'): {job['company']} — {title}")
        return False

    return True


def deduplicate(jobs: list[dict], seen_ids: set) -> tuple[list[dict], set]:
    """Same scheme as filter.deduplicate, keyed on company_id::job_id (kept
    fully separate from the US pipeline's seen-ids store)."""
    new_jobs = []
    for job in jobs:
        key = f"{job['company_id']}::{job['job_id']}"
        if key not in seen_ids:
            new_jobs.append(job)
            seen_ids.add(key)
    return new_jobs, seen_ids
