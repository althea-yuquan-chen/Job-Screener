"""
fetcher.py — pulls job listings from Greenhouse, Lever, and Workday ATS platforms.
Returns a normalized list of job dicts regardless of source.
"""

import requests
import json
import re
import time
import logging
from typing import Optional
from bs4 import BeautifulSoup

logger = logging.getLogger(__name__)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36"
}

# ── Greenhouse ────────────────────────────────────────────────────────────────

def fetch_greenhouse(company_id: str, company_name: str) -> list[dict]:
    """Greenhouse has a public JSON API — no auth needed."""
    url = f"https://boards-api.greenhouse.io/v1/boards/{company_id}/jobs?content=true"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        jobs = []
        for j in data.get("jobs", []):
            jobs.append({
                "company": company_name,
                "title": j.get("title", ""),
                "location": _extract_location(j.get("location", {}).get("name", "")),
                "url": j.get("absolute_url", ""),
                "job_id": str(j.get("id", "")),
                "description": _clean_html(j.get("content", "")),
                "posted_at": j.get("updated_at", "")[:10],
                "ats": "greenhouse",
            })
        return jobs
    except Exception as e:
        logger.warning(f"Greenhouse fetch failed for {company_name}: {e}")
        return []


# ── Lever ─────────────────────────────────────────────────────────────────────

def fetch_lever(company_id: str, company_name: str) -> list[dict]:
    """Lever also has a public JSON API."""
    url = f"https://api.lever.co/v0/postings/{company_id}?mode=json"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        jobs = []
        for j in data:
            description = ""
            for section in j.get("descriptionBody", {}).get("descriptionPlain", []):
                description += section + " "
            for list_section in j.get("lists", []):
                description += list_section.get("content", "") + " "

            jobs.append({
                "company": company_name,
                "title": j.get("text", ""),
                "location": j.get("categories", {}).get("location", ""),
                "url": j.get("hostedUrl", ""),
                "job_id": j.get("id", ""),
                "description": description.strip(),
                "posted_at": _timestamp_to_date(j.get("createdAt", 0)),
                "ats": "lever",
            })
        return jobs
    except Exception as e:
        logger.warning(f"Lever fetch failed for {company_name}: {e}")
        return []


# ── Workday ───────────────────────────────────────────────────────────────────

WORKDAY_COMPANY_URLS = {
    "zsassociates": "https://zsassociates.wd5.myworkdayjobs.com/wday/cxs/zsassociates/External/jobs",
    "accenture":    "https://accenture.wd3.myworkdayjobs.com/wday/cxs/accenture/AccentureCareers/jobs",
    "deloitte":     "https://deloitte.wd1.myworkdayjobs.com/wday/cxs/deloitte/DeloitteCareers/jobs",
    "ey":           "https://ey.wd5.myworkdayjobs.com/wday/cxs/ey/EY_External_Careers_Site/jobs",
    "cognizant":    "https://cognizant.wd1.myworkdayjobs.com/wday/cxs/cognizant/Cognizant_Careers/jobs",
    "mckinsey":     "https://mckinsey.wd1.myworkdayjobs.com/wday/cxs/mckinsey/Search/jobs",
    "bcg":          "https://bcg.wd3.myworkdayjobs.com/wday/cxs/bcg/BCG_Career_Site/jobs",
    "bain":         "https://bain.wd1.myworkdayjobs.com/wday/cxs/bain/Global_Careers/jobs",
}

def fetch_workday(company_id: str, company_name: str) -> list[dict]:
    """Workday exposes a CXS API endpoint. We POST a search query."""
    url = WORKDAY_COMPANY_URLS.get(company_id)
    if not url:
        logger.warning(f"No Workday URL configured for {company_id}")
        return []

    payload = {
        "appliedFacets": {},
        "limit": 100,
        "offset": 0,
        "searchText": ""
    }
    try:
        r = requests.post(url, json=payload, headers={**HEADERS, "Content-Type": "application/json"}, timeout=20)
        r.raise_for_status()
        data = r.json()
        jobs = []
        for j in data.get("jobPostings", []):
            ext_url = j.get("externalPath", "")
            base_url = url.replace("/wday/cxs", "").split("/jobs")[0]
            full_url = base_url + ext_url if ext_url else ""
            jobs.append({
                "company": company_name,
                "title": j.get("title", ""),
                "location": j.get("locationsText", ""),
                "url": full_url,
                "job_id": j.get("bulletFields", [""])[0] if j.get("bulletFields") else "",
                "description": j.get("jobPostingId", ""),
                "posted_at": j.get("postedOn", "")[:10] if j.get("postedOn") else "",
                "ats": "workday",
            })
        return jobs
    except Exception as e:
        logger.warning(f"Workday fetch failed for {company_name}: {e}")
        return []


# ── Dispatcher ────────────────────────────────────────────────────────────────

def fetch_jobs_for_company(company: dict) -> list[dict]:
    ats = company["ats"]
    cid = company["id"]
    name = company["name"]
    tier = company["tier"]

    if ats == "greenhouse":
        jobs = fetch_greenhouse(cid, name)
    elif ats == "lever":
        jobs = fetch_lever(cid, name)
    elif ats == "workday":
        jobs = fetch_workday(cid, name)
    else:
        logger.warning(f"Unknown ATS '{ats}' for {name}")
        jobs = []

    # Attach tier to each job
    for j in jobs:
        j["tier"] = tier

    time.sleep(1)  # Be polite — 1 second between company requests
    return jobs


# ── Helpers ───────────────────────────────────────────────────────────────────

def _clean_html(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    text = soup.get_text(separator=" ")
    text = re.sub(r"\s+", " ", text).strip()
    return text[:3000]  # Cap description length for AI scoring

def _extract_location(loc_str: str) -> str:
    return loc_str.strip() if loc_str else ""

def _timestamp_to_date(ts: int) -> str:
    if not ts:
        return ""
    from datetime import datetime, timezone
    try:
        return datetime.fromtimestamp(ts / 1000, tz=timezone.utc).strftime("%Y-%m-%d")
    except Exception:
        return ""
