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


# ── Ashby ─────────────────────────────────────────────────────────────────────

def fetch_ashby(company_id: str, company_name: str) -> list[dict]:
    """Ashby has a public job-board API — no auth needed. Unlike Workday, the
    list endpoint already includes full description HTML, so no separate
    per-job enrichment step is needed."""
    url = f"https://api.ashbyhq.com/posting-api/job-board/{company_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        jobs = []
        for j in data.get("jobs", []):
            jobs.append({
                "company": company_name,
                "title": j.get("title", ""),
                "location": j.get("location", ""),
                "url": j.get("jobUrl", ""),
                "job_id": j.get("id", ""),
                "description": _clean_html(j.get("descriptionHtml", "")),
                "posted_at": (j.get("publishedAt") or "")[:10],
                "ats": "ashby",
            })
        return jobs
    except Exception as e:
        logger.warning(f"Ashby fetch failed for {company_name}: {e}")
        return []


# ── Workable ──────────────────────────────────────────────────────────────────

def fetch_workable(company_id: str, company_name: str) -> list[dict]:
    """Workable has a public widget API — no auth needed."""
    url = f"https://apply.workable.com/api/v1/widget/accounts/{company_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        jobs = []
        for j in data.get("jobs", []):
            location_parts = [p for p in (
                j.get("city"), j.get("state"), j.get("country")
            ) if p]
            jobs.append({
                "company": company_name,
                "title": j.get("title", ""),
                "location": ", ".join(location_parts),
                "url": j.get("url", ""),
                "job_id": j.get("shortcode", ""),
                "description": _clean_html(j.get("description", "")),
                "posted_at": (j.get("published_on") or "")[:10],
                "ats": "workable",
            })
        return jobs
    except Exception as e:
        logger.warning(f"Workable fetch failed for {company_name}: {e}")
        return []


# ── SmartRecruiters ───────────────────────────────────────────────────────────

def fetch_smartrecruiters(company_id: str, company_name: str) -> list[dict]:
    """
    SmartRecruiters has a public postings API — no auth needed.

    Like Workday, the list endpoint does NOT include full description text
    (only title/location/id) — full descriptions require a separate per-job
    detail request; see fetch_smartrecruiters_description, which main.py
    calls only for jobs that survive the keyword filter.
    """
    jobs = []
    offset = 0
    PAGE_SIZE = 100
    try:
        while True:
            url = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings"
            r = requests.get(url, headers=HEADERS, params={"limit": PAGE_SIZE, "offset": offset}, timeout=15)
            r.raise_for_status()
            data = r.json()
            content = data.get("content", [])
            if not content:
                break
            for j in content:
                jobs.append({
                    "company": company_name,
                    "title": j.get("name", ""),
                    "location": _format_smartrecruiters_location(j.get("location", {})),
                    "url": j.get("applyUrl", "") or j.get("ref", ""),
                    "job_id": j.get("id", ""),
                    "description": "",
                    "posted_at": (j.get("releasedDate") or "")[:10],
                    "ats": "smartrecruiters",
                    "_smartrecruiters_company_id": company_id,
                })
            offset += PAGE_SIZE
            if offset >= data.get("totalFound", 0):
                break
        return jobs
    except Exception as e:
        logger.warning(f"SmartRecruiters fetch failed for {company_name} (got {len(jobs)} before failure): {e}")
        return jobs


def _format_smartrecruiters_location(loc: dict) -> str:
    parts = [loc.get(k) for k in ("city", "region", "country") if loc.get(k)]
    return ", ".join(parts)


def fetch_smartrecruiters_description(job: dict) -> str:
    """Fetches full JD text for a single SmartRecruiters job (see note on
    fetch_smartrecruiters). Call this only for jobs that already survived
    the keyword filter, since it's one HTTP request per job."""
    company_id = job.get("_smartrecruiters_company_id", "")
    job_id = job.get("job_id", "")
    if not company_id or not job_id:
        return ""
    url = f"https://api.smartrecruiters.com/v1/companies/{company_id}/postings/{job_id}"
    try:
        r = requests.get(url, headers=HEADERS, timeout=15)
        r.raise_for_status()
        data = r.json()
        sections = data.get("jobAd", {}).get("sections", {})
        text = " ".join(
            _clean_html(s.get("text", "")) for s in sections.values() if isinstance(s, dict)
        )
        return text[:3000]
    except Exception as e:
        logger.warning(f"SmartRecruiters detail fetch failed for {job.get('company')} — {job.get('title')}: {e}")
        return ""


def fetch_smartrecruiters_descriptions(jobs: list[dict], max_workers: int = 10) -> None:
    """Enriches SmartRecruiters jobs in-place with full JD text, concurrently
    (same pattern as fetch_workday_descriptions)."""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_smartrecruiters_description, j): j for j in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                job["description"] = future.result()
            except Exception as e:
                logger.warning(f"SmartRecruiters description enrichment failed for {job.get('company')} — {job.get('title')}: {e}")
                job["description"] = ""


# ── Amazon ────────────────────────────────────────────────────────────────────

def fetch_amazon(company_id: str, company_name: str) -> list[dict]:
    """
    Amazon runs its own careers site (amazon.jobs), not a shared ATS — but its
    search page calls a public, unauthenticated JSON endpoint that returns
    full description text inline, so (unlike Workday/SmartRecruiters) no
    separate per-job detail request is needed.

    Scoped to the "Software Development" category to keep volume tractable —
    Amazon posts thousands of non-software roles (ops, retail, fulfillment,
    etc.) that are out of scope for this candidate profile.
    """
    jobs = []
    offset = 0
    PAGE_SIZE = 100
    MAX_PAGES = 20
    url = "https://www.amazon.jobs/en/search.json"
    try:
        for _ in range(MAX_PAGES):
            params = {
                "country": "USA",
                "category[]": "Software Development",
                "offset": offset,
                "result_limit": PAGE_SIZE,
                "sort": "recent",
            }
            r = requests.get(url, headers=HEADERS, params=params, timeout=20)
            r.raise_for_status()
            data = r.json()
            postings = data.get("jobs", [])
            if not postings:
                break
            for j in postings:
                job_path = j.get("job_path", "")
                jobs.append({
                    "company": company_name,
                    "title": j.get("title", ""),
                    "location": j.get("normalized_location", "") or j.get("location", ""),
                    "url": f"https://www.amazon.jobs{job_path}" if job_path else "",
                    "job_id": j.get("id_icims", "") or j.get("id", ""),
                    "description": _clean_html(j.get("description", "")),
                    "posted_at": _amazon_date_to_iso(j.get("posted_date", "")),
                    "ats": "amazon",
                })
            offset += PAGE_SIZE
            if len(postings) < PAGE_SIZE or offset >= data.get("hits", 0):
                break
        return jobs
    except Exception as e:
        logger.warning(f"Amazon fetch failed for {company_name} (got {len(jobs)} before failure): {e}")
        return jobs


def _amazon_date_to_iso(date_str: str) -> str:
    """Amazon's posted_date is a string like 'July 28, 2026'."""
    if not date_str:
        return ""
    from datetime import datetime
    try:
        return datetime.strptime(date_str, "%B %d, %Y").strftime("%Y-%m-%d")
    except Exception:
        return ""


# ── Eightfold ─────────────────────────────────────────────────────────────────

def fetch_eightfold(host: str, domain: str, company_name: str) -> list[dict]:
    """
    Eightfold.ai is a recruiting SaaS platform some large employers white-label
    under their own domain (e.g. Microsoft's careers site at
    apply.careers.microsoft.com runs on Eightfold under the hood — there's no
    way to tell from the domain name alone; this was found by inspecting the
    site's own network requests). Its public search API (`/api/pcsx/search`)
    needs no auth, but only returns 10 results per page and no description
    text — full JD requires a separate per-job request; see
    fetch_eightfold_description, called only on jobs that survive the keyword
    filter (same lazy-enrichment pattern as fetch_workday_description).

    Any other Eightfold-hosted company can be added via config alone (its own
    host + domain), no new code needed — see companies_supplemental.json.
    """
    if not host or not domain:
        logger.warning(f"No Eightfold host/domain configured for {company_name}")
        return []

    jobs = []
    offset = 0
    PAGE_SIZE = 10   # fixed by the API — larger `num`/`limit` params are ignored
    MAX_PAGES = 100  # bounds runtime for a large employer (Microsoft ~900 US postings)
    url = f"https://{host}/api/pcsx/search"
    try:
        for page_num in range(MAX_PAGES):
            if page_num > 0:
                time.sleep(0.4)  # the 10-results-per-page cap means ~90 requests for
                                  # a large employer; firing them back-to-back triggers
                                  # a 429 from Eightfold's rate limiter after ~25 requests
            params = {
                "domain": domain,
                "query": "",
                "location": "United States",
                "start": offset,
                "filter_include_remote": 1,
            }
            r = _get_with_retry(url, params)
            data = r.json().get("data", {})
            postings = data.get("positions", [])
            if not postings:
                break
            for j in postings:
                position_id = j.get("id", "")
                posted_ts = j.get("postedTs", 0)
                jobs.append({
                    "company": company_name,
                    "title": j.get("name", ""),
                    "location": ", ".join(j.get("locations", []) or []),
                    "url": f"https://{host}{j.get('positionUrl', '')}" if j.get("positionUrl") else "",
                    "job_id": str(position_id),
                    "description": "",
                    "posted_at": _timestamp_to_date(posted_ts * 1000) if posted_ts else "",
                    "ats": "eightfold",
                    "_eightfold_host": host,
                    "_eightfold_domain": domain,
                    "_eightfold_position_id": position_id,
                })
            offset += PAGE_SIZE
            if len(postings) < PAGE_SIZE or offset >= data.get("count", 0):
                break
        return jobs
    except Exception as e:
        logger.warning(f"Eightfold fetch failed for {company_name} (got {len(jobs)} before failure): {e}")
        return jobs


def fetch_eightfold_description(job: dict) -> str:
    """Fetches full JD text for a single Eightfold job (see fetch_eightfold docstring)."""
    host = job.get("_eightfold_host", "")
    domain = job.get("_eightfold_domain", "")
    position_id = job.get("_eightfold_position_id", "")
    if not (host and domain and position_id):
        return ""
    url = f"https://{host}/api/pcsx/position_details"
    try:
        r = requests.get(
            url, headers=HEADERS,
            params={"position_id": position_id, "domain": domain, "hl": "en"},
            timeout=15,
        )
        r.raise_for_status()
        html = r.json().get("data", {}).get("jobDescription", "")
        return _clean_html(html)
    except Exception as e:
        logger.warning(f"Eightfold detail fetch failed for {job.get('company')} — {job.get('title')}: {e}")
        return ""


def fetch_eightfold_descriptions(jobs: list[dict], max_workers: int = 10) -> None:
    """Enriches Eightfold jobs in-place with full JD text, concurrently (same
    pattern as fetch_workday_descriptions)."""
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_eightfold_description, j): j for j in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                job["description"] = future.result()
            except Exception as e:
                logger.warning(f"Eightfold description enrichment failed for {job.get('company')} — {job.get('title')}: {e}")
                job["description"] = ""


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
                    # externalPath (the job's URL slug) is stable and unique per posting.
                    # bulletFields is NOT a reliable job ID source: it's a per-tenant-configurable
                    # list of display facts (e.g. "Full time", "Posted Today", location) and its
                    # order/content varies by tenant and can change between fetches of the same
                    # job (e.g. "Posted Today" -> "Posted Yesterday") -- using it as job_id broke
                    # deduplication, causing the same posting to look "new" on every run and
                    # generate a fresh tailored resume each time.
                    "job_id": ext_url,
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


def fetch_workday_descriptions(jobs: list[dict], max_workers: int = 10) -> None:
    """
    Enriches Workday jobs in-place with full JD text, concurrently. A real
    test run showed ~861 jobs takes ~8 minutes fetched one at a time —
    parallelizing this the same way as fetch_all_companies cuts that down.
    Call only on jobs that already survived the keyword filter (see
    fetch_workday_description's docstring for why).
    """
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_workday_description, j): j for j in jobs}
        for future in as_completed(futures):
            job = futures[future]
            try:
                job["description"] = future.result()
            except Exception as e:
                logger.warning(f"Workday description enrichment failed for {job.get('company')} — {job.get('title')}: {e}")
                job["description"] = ""


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
    elif ats == "ashby":
        jobs = fetch_ashby(cid, name)
    elif ats == "workable":
        jobs = fetch_workable(cid, name)
    elif ats == "smartrecruiters":
        jobs = fetch_smartrecruiters(cid, name)
    elif ats == "workday":
        jobs = fetch_workday(company.get("workday_url", ""), name)
    elif ats == "amazon":
        jobs = fetch_amazon(cid, name)
    elif ats == "eightfold":
        jobs = fetch_eightfold(company.get("eightfold_host", ""), company.get("eightfold_domain", ""), name)
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

def _get_with_retry(url: str, params: dict, max_retries: int = 3) -> requests.Response:
    """GET with backoff retry on 429 (rate limit). Used by Eightfold, whose
    pagination needs far more requests per company than the other ATS
    platforms and hits its rate limiter if retried naively."""
    for attempt in range(max_retries):
        r = requests.get(url, headers=HEADERS, params=params, timeout=20)
        if r.status_code == 429 and attempt < max_retries - 1:
            time.sleep(2 * (attempt + 1))
            continue
        r.raise_for_status()
        return r


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
