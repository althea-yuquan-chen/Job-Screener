"""
filter.py — lightweight keyword pre-filter.
Runs before the Claude API call to eliminate obvious non-matches cheaply.
"""

import json
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

_config_path = Path(__file__).parent.parent / "config" / "filters.json"
with open(_config_path) as f:
    FILTERS = json.load(f)

INCLUDE_TITLE = [kw.lower() for kw in FILTERS["include_title_keywords"]]
EXCLUDE_TITLE = [kw.lower() for kw in FILTERS["exclude_title_keywords"]]
LOCATIONS     = [loc.lower() for loc in FILTERS["locations"]]


def passes_keyword_filter(job: dict) -> bool:
    """
    Returns True if the job should be sent to AI scoring.
    Fast title + location check — keeps API costs low.
    """
    title = job.get("title", "").lower()
    location = job.get("location", "").lower()

    # Hard exclude: senior/lead/manager roles
    for kw in EXCLUDE_TITLE:
        if kw in title:
            logger.debug(f"EXCLUDED (seniority): {job['company']} — {job['title']}")
            return False

    # Must match at least one target title keyword
    title_match = any(kw in title for kw in INCLUDE_TITLE)
    if not title_match:
        logger.debug(f"EXCLUDED (title mismatch): {job['company']} — {job['title']}")
        return False

    # Location: pass if remote, US, or no location specified (assume remote)
    if location:
        location_ok = any(loc in location for loc in LOCATIONS)
        if not location_ok:
            logger.debug(f"EXCLUDED (location): {job['company']} — {job['title']} ({location})")
            return False

    return True


def deduplicate(jobs: list[dict], seen_ids: set) -> tuple[list[dict], set]:
    """
    Remove jobs already seen in previous runs (by job_id + company key).
    Returns new jobs only + updated seen_ids set.
    """
    new_jobs = []
    for job in jobs:
        key = f"{job['company']}::{job['job_id']}"
        if key not in seen_ids:
            new_jobs.append(job)
            seen_ids.add(key)
    return new_jobs, seen_ids
