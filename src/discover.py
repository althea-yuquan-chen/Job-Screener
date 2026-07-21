"""
discover.py — dynamically discovers companies to poll for job postings.

Replaces the old static config/companies.json. Pulls the public,
community-maintained SimplifyJobs/New-Grad-Positions dataset to find which
companies are *currently* posting new-grad roles on Greenhouse, Lever, or
Workday (the three ATS platforms fetcher.py knows how to pull full job
descriptions from), then merges in a small hand-picked supplemental list —
AI-agent-native and consulting companies the community feed doesn't
reliably tag by category.
"""

import json
import logging
import re
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

LISTINGS_URL = (
    "https://raw.githubusercontent.com/SimplifyJobs/New-Grad-Positions/"
    "dev/.github/scripts/listings.json"
)

RELEVANT_CATEGORIES = {
    "Software", "Software Engineering",
    "AI/ML/Data", "Data Science, AI & Machine Learning",
    "Product", "Product Management",
}

DISQUALIFYING_SPONSORSHIP = {"Does Not Offer Sponsorship", "U.S. Citizenship is Required"}

_SUPPLEMENTAL_PATH = Path(__file__).parent.parent / "config" / "companies_supplemental.json"

# job-boards.greenhouse.io / boards.greenhouse.io / job-boards.eu.greenhouse.io
# (the .eu. subdomain is a separate region Greenhouse serves EU-based orgs from —
# same API shape, just a different host)
_GREENHOUSE_RE = re.compile(r"(?:job-boards|boards)\.(?:eu\.)?greenhouse\.io/([^/]+)/")
_LEVER_RE = re.compile(r"jobs\.lever\.co/([^/]+)/")
_WORKDAY_RE = re.compile(
    r"https?://([a-z0-9\-]+)\.(wd\d+)\.myworkdayjobs\.com/(?:[a-z]{2}-[A-Z]{2}/)?([^/]+)/job/"
)
_ASHBY_RE = re.compile(r"jobs\.ashbyhq\.com/([^/]+)/")
_SMARTRECRUITERS_RE = re.compile(r"jobs\.smartrecruiters\.com/([^/]+)/")
_WORKABLE_RE = re.compile(r"apply\.workable\.com/([^/]+)/")


def _classify(entry: dict) -> dict | None:
    """Try to resolve a feed entry's URL to a company on a supported ATS."""
    for url in (entry.get("url", ""), entry.get("company_url", "")):
        if not url:
            continue
        m = _GREENHOUSE_RE.search(url)
        if m:
            return {"ats": "greenhouse", "id": m.group(1)}
        m = _LEVER_RE.search(url)
        if m:
            return {"ats": "lever", "id": m.group(1)}
        m = _ASHBY_RE.search(url)
        if m:
            return {"ats": "ashby", "id": m.group(1)}
        m = _WORKABLE_RE.search(url)
        if m:
            return {"ats": "workable", "id": m.group(1)}
        m = _SMARTRECRUITERS_RE.search(url)
        if m:
            return {"ats": "smartrecruiters", "id": m.group(1)}
        m = _WORKDAY_RE.search(url)
        if m:
            subdomain, wd_n, tenant = m.groups()
            workday_url = (
                f"https://{subdomain}.{wd_n}.myworkdayjobs.com"
                f"/wday/cxs/{subdomain}/{tenant}/jobs"
            )
            return {"ats": "workday", "id": subdomain, "workday_url": workday_url}
    return None


def _discover_from_feed() -> list[dict]:
    try:
        r = requests.get(LISTINGS_URL, timeout=60)
        r.raise_for_status()
        listings = r.json()
    except Exception as e:
        logger.warning(f"Could not fetch discovery feed, continuing with supplemental list only: {e}")
        return []

    seen = {}
    for entry in listings:
        if not entry.get("active"):
            continue
        if entry.get("category") not in RELEVANT_CATEGORIES:
            continue
        if entry.get("sponsorship") in DISQUALIFYING_SPONSORSHIP:
            continue

        resolved = _classify(entry)
        if not resolved:
            continue

        key = (resolved["ats"], resolved["id"])
        if key in seen:
            continue

        company = {
            "name": entry.get("company_name", resolved["id"]),
            "ats": resolved["ats"],
            "id": resolved["id"],
            "tier": None,
            "source": "feed",
        }
        if "workday_url" in resolved:
            company["workday_url"] = resolved["workday_url"]
        seen[key] = company

    logger.info(f"Discovered {len(seen)} companies from the New-Grad-Positions feed.")
    return list(seen.values())


def _load_supplemental() -> list[dict]:
    if not _SUPPLEMENTAL_PATH.exists():
        return []
    companies = json.loads(_SUPPLEMENTAL_PATH.read_text())
    for c in companies:
        c["source"] = "supplemental"
    return companies


def get_companies() -> list[dict]:
    """Returns the merged, deduped list of companies to poll today."""
    feed_companies = _discover_from_feed()
    supplemental = _load_supplemental()

    merged = {}
    for c in feed_companies:
        merged[(c["ats"], c["id"])] = c
    for c in supplemental:  # supplemental wins ties — keeps curated tier metadata
        merged[(c["ats"], c["id"])] = c

    companies = list(merged.values())
    feed_count = sum(1 for c in companies if c["source"] == "feed")
    supp_count = sum(1 for c in companies if c["source"] == "supplemental")
    logger.info(f"Total companies to poll: {len(companies)} ({feed_count} from feed, {supp_count} supplemental)")
    return companies
