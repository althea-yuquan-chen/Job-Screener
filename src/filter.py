"""
filter.py — lightweight keyword pre-filter.
Runs before the Claude API call to eliminate obvious non-matches cheaply.
"""

import json
import logging
import re
from pathlib import Path

logger = logging.getLogger(__name__)

_config_path = Path(__file__).parent.parent / "config" / "filters.json"
with open(_config_path) as f:
    FILTERS = json.load(f)

INCLUDE_TITLE     = [kw.lower().strip() for kw in FILTERS["include_title_keywords"]]
EXCLUDE_TITLE      = [kw.lower().strip() for kw in FILTERS["exclude_title_keywords"]]
LOCATIONS          = [loc.lower() for loc in FILTERS["locations"]]
EXCLUDE_LOCATIONS  = [loc.lower() for loc in FILTERS.get("exclude_locations", [])]

# Phrases in a job description that mean "US citizens/permanent residents/US
# persons only" -- disqualifying for a candidate on F-1/STEM OPT who will
# need H-1B sponsorship. Checked with regex (not simple keyword matching)
# since these show up as short phrases embedded in longer sentences.
WORK_AUTH_EXCLUDE_PATTERNS = [
    r"\bu\.?s\.?\s+citizens?\s+only\b",
    r"\bu\.?s\.?\s+persons?\s+only\b",
    r"\bonly\s+u\.?s\.?\s+persons?\b",
    r"\bmust\s+be\s+a\s+u\.?s\.?\s+person\b",
    r"\bmust\s+be\s+(?:a\s+)?(?:u\.?s\.?|united states)\s+citizen",
    r"\bu\.?s\.?\s+citizenship\s+is\s+required\b",
    r"\bcitizenship\s+required\b",
    r"\bcitizens?\s+or\s+green\s*card\s+holders?\s+only\b",
    r"\bgreen\s*card\s+holders?\s+only\b",
    r"\bactive\s+(?:secret|top\s+secret|ts/sci|dod|government)\s+security\s+clearance\b",
    r"\bmust\s+(?:currently\s+)?(?:hold|possess)\s+(?:an?\s+)?(?:active\s+)?security\s+clearance\b",
    r"\bno\s+(?:visa\s+)?sponsorship\b",
    r"\b(?:will|does|do|can)\s*not\s+(?:currently\s+)?sponsor\b",
    r"\bunable\s+to\s+(?:provide|offer|sponsor)\b[^.]{0,30}sponsorship",
    r"\bnot\s+(?:eligible|able)\s+for\s+(?:visa\s+)?sponsorship\b",
]
_WORK_AUTH_EXCLUDE_RE = [re.compile(p, re.IGNORECASE) for p in WORK_AUTH_EXCLUDE_PATTERNS]


def _keyword_match(text: str, keywords: list[str]) -> str | None:
    """Word-boundary match so e.g. 'intern' doesn't match 'international'."""
    for kw in keywords:
        if re.search(rf"\b{re.escape(kw)}\b", text):
            return kw
    return None


def passes_keyword_filter(job: dict) -> bool:
    """
    Returns True if the job should be sent to AI scoring.
    Fast title + location check — keeps API costs low.
    """
    title = job.get("title", "").lower()
    location = job.get("location", "").lower()

    # Hard exclude: senior/lead/manager roles, internships, non-full-time roles
    hit = _keyword_match(title, EXCLUDE_TITLE)
    if hit:
        logger.debug(f"EXCLUDED (keyword '{hit}'): {job['company']} — {job['title']}")
        return False

    # Must match at least one target title keyword
    title_match = _keyword_match(title, INCLUDE_TITLE) is not None
    if not title_match:
        logger.debug(f"EXCLUDED (title mismatch): {job['company']} — {job['title']}")
        return False

    # Location: hard-exclude known non-US countries/cities first (catches
    # multi-location postings like "New York / Toronto / London" and
    # "Remote - Canada", where a US-sounding fragment would otherwise also
    # match below), then require a US/remote marker. No location specified
    # is treated as remote/US.
    if location:
        exclude_hit = _keyword_match(location, EXCLUDE_LOCATIONS)
        if exclude_hit:
            logger.debug(f"EXCLUDED (non-US location '{exclude_hit}'): {job['company']} — {job['title']} ({location})")
            return False

        location_ok = _keyword_match(location, LOCATIONS) is not None
        if not location_ok:
            logger.debug(f"EXCLUDED (location): {job['company']} — {job['title']} ({location})")
            return False

    return True


def passes_work_auth_filter(job: dict) -> bool:
    """
    Returns False if the job description states the role is limited to US
    citizens/permanent residents/"US persons" (ITAR/export-control language)
    or otherwise disqualifying (security clearance, no visa sponsorship).
    Candidate is on F-1/STEM OPT and needs H-1B sponsorship, so these are a
    hard exclude rather than a scoring penalty. No-op if no description text
    is available yet (e.g. Workday jobs before detail enrichment).
    """
    text = job.get("description", "")
    if not text:
        return True
    for pattern in _WORK_AUTH_EXCLUDE_RE:
        m = pattern.search(text)
        if m:
            logger.debug(f"EXCLUDED (work auth '{m.group(0)}'): {job['company']} — {job['title']}")
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
