"""
fetcher.py — pulls job listings from Greenhouse, Lever, and Workday ATS platforms.
Returns a normalized list of job dicts regardless of source.
"""

import requests
import json
import re
import time
import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
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
            # descriptionPlain/description are top-level plain-text/HTML strings on
            # the real API (not a nested dict, despite what the field name suggests)
            description = j.get("descriptionPlain", "") or ""
            for list_section in j.get("lists", []):
                description += " " + _clean_html(list_section.get("content", ""))
            description = description.strip()[:3000]

            jobs.append({
                "company": company_name,
                "title": j.get("text", ""),
                "location": j.get("categories", {}).get("location", ""),
                "url": j.get("hostedUrl", ""),
                "job_id": j.get("id", ""),
                "description": description,
                "posted_at": _timestamp_to_date(j.get("createdAt", 0)),
                "ats": "lever",
            })
        return jobs
    except Exception as e:
        logger.warning(f"Lever fetch failed for {company_name}: {e}")
        return []


# ── Workday ───────────────────────────────────────────────────────────────────

def _workday_headers(workday_url: str) -> dict:
    """
    Workday tenants sit behind bot-protection that rejects requests missing
    Origin/Referer/Accept headers matching the tenant's own domain (returns a
    generic HTTP 400/422 otherwise, even with a legit User-Agent).
    """
    origin = re.match(r"https?://[^/]+", workday_url).group(0)
    return {
        **HEADERS,
        "Accept": "application/json",
        "Content-Type": "application/json",
        "Origin": origin,
        "Referer": origin + "/",
    }


def fetch_workday(workday_url: str, company_name: str) -> list[dict]:
    """
    Workday exposes a CXS API endpoint. We POST a search query.

    workday_url is pre-resolved by discover.py (either from the supplemental
    company list or derived from a company's public career page URL) — there
    is no static lookup table here, so any Workday tenant works.

    Note: Workday's list endpoint does NOT include full job description text
    (unlike Greenhouse/Lever) — only title/location/id. Full descriptions
    require a separate per-job detail request; see fetch_workday_description,
    which main.py calls only for jobs that survive the keyword filter, to
    avoid making a detail request for every one of a large employer's postings.
    """
    url = workday_url
    if not url:
        logger.warning(f"No Workday URL provided for {company_name}")
        return []

    PAGE_SIZE = 20   # Workday's CXS API hard-caps limit at 20 — larger values 400
    MAX_PAGES = 15   # bounds runtime for huge employers (e.g. Accenture has 2000+ reqs)

    cxs_base = url.rsplit("/jobs", 1)[0]  # e.g. https://x.wdN.myworkdayjobs.com/wday/cxs/x/Tenant
    public_base = re.sub(r"/wday/cxs/[^/]+/", "/", cxs_base, count=1)
    headers = _workday_headers(url)

    jobs = []
    total_count = None  # Workday only reports an accurate `total` on the first page;
                        # later pages have been observed to report 0 — don't trust it after page 0.
    try:
        for page in range(MAX_PAGES):
            payload = {"appliedFacets": {}, "limit": PAGE_SIZE, "offset": page * PAGE_SIZE, "searchText": ""}
            r = requests.post(url, json=payload, headers=headers, timeout=20)
            r.raise_for_status()
            data = r.json()
            postings = data.get("jobPostings", [])
            if not postings:
                break
            if page == 0:
                total_count = data.get("total", 0)

            for j in postings:
                ext_url = j.get("externalPath", "")
                jobs.append({
                    "company": company_name,
                    "title": j.get("title", ""),
                    "location": j.get("locationsText", ""),
                    "url": (public_base + ext_url) if ext_url else "",
                    "job_id": j.get("bulletFields", [""])[0] if j.get("bulletFields") else "",
                    "description": "",
                    "posted_at": j.get("postedOn", "")[:10] if j.get("postedOn") else "",
                    "ats": "workday",
                    "_workday_detail_url": (cxs_base + ext_url) if ext_url else "",
                })

            if len(jobs) >= (total_count or 0) or len(postings) < PAGE_SIZE:
                break
        return jobs
    except Exception as e:
        logger.warning(f"Workday fetch failed for {company_name} (got {len(jobs)} before failure): {e}")
        return jobs


def fetch_workday_description(job: dict) -> str:
    """
    Fetches full JD text for a single Workday job (see note on fetch_workday).
    Call this only for jobs that already survived the keyword filter, since
    it's one HTTP request per job.
    """
    detail_url = job.get("_workday_detail_url", "")
    if not detail_url:
        return ""
    try:
        r = requests.get(detail_url, headers=_workday_headers(detail_url), timeout=15)
        r.raise_for_status()
        data = r.json()
        html = data.get("jobPostingInfo", {}).get("jobDescription", "")
        return _clean_html(html)
    except Exception as e:
        logger.warning(f"Workday detail fetch failed for {job.get('company')} — {job.get('title')}: {e}")
        return ""


# ── Dispatcher ────────────────────────────────────────────────────────────────

def fetch_jobs_for_company(company: dict) -> list[dict]:
    ats = company["ats"]
    cid = company["id"]
    name = company["name"]
    tier = company.get("tier")

    if ats == "greenhouse":
        jobs = fetch_greenhouse(cid, name)
    elif ats == "lever":
        jobs = fetch_lever(cid, name)
    elif ats == "workday":
        jobs = fetch_workday(company.get("workday_url", ""), name)
    else:
        logger.warning(f"Unknown ATS '{ats}' for {name}")
        jobs = []

    # Attach tier + source to each job
    for j in jobs:
        j["tier"] = tier
        j["company_source"] = company.get("source", "unknown")

    time.sleep(0.3)  # Be polite — small delay per company request
    return jobs


def fetch_all_companies(companies: list[dict], max_workers: int = 10) -> list[dict]:
    """
    Fetches jobs for many companies concurrently. With ~350+ companies,
    sequential fetching (even at a fraction of a second each) adds up to
    many minutes; a small thread pool keeps total runtime reasonable while
    still being polite per-request.
    """
    all_jobs = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_jobs_for_company, c): c for c in companies}
        for future in as_completed(futures):
            company = futures[future]
            try:
                jobs = future.result()
                logger.info(f"  → {company['name']} ({company['ats'].upper()}): {len(jobs)} postings")
                all_jobs.extend(jobs)
            except Exception as e:
                logger.warning(f"Fetch failed for {company['name']}: {e}")
    return all_jobs


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
