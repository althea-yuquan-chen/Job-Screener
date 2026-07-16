"""
discover_cn.py — pulls campus-recruiting job listings from Nowcoder (牛客网)
for a curated list of Chinese companies.

Unlike discover.py (US pipeline), there is no public feed that auto-discovers
which companies to target — Nowcoder has no open "which companies use us"
directory, so config/companies_cn.json is a hand-curated list (same spirit as
config/companies_supplemental.json). See config/companies_cn.json for how to
add more: visit https://www.nowcoder.com/enterprise/{id} for a company and
copy the numeric id out of the URL.

Nowcoder's general keyword search (nowcoder.com/jobs/fulltime) is login-gated
(confirmed live: renders nothing but a login modal for an unauthenticated
request). But each company's "enterprise" page calls an open, unauthenticated
JSON API for its own job list — no auth headers, no signature, no cookies
required (confirmed live against several companies). That's what this module
calls directly, bypassing the page entirely.

recruitType semantics (0=全部/1/2/3) were probed live: recruitType=0 reliably
returns the union of 1+2+3 (verified by count against two companies), but
what 1/2/3 individually mean was NOT consistent/reliable across companies in
that probe (title content didn't cleanly map to a single "校招 vs 实习 vs 社招"
story). So this module just fetches recruitType=0 (everything) per company
and leaves recruit-type/seniority judgment to filter_cn.py's keyword filter
and the LLM scorer, rather than trusting an unreliable query param.
"""

import json
import logging
import time
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

JOB_LIST_URL = "https://nowpick.nowcoder.com/u/company/job/list/v2"
PAGE_SIZE = 50   # API confirmed to accept at least 20; larger cuts request count
MAX_PAGES = 20   # safety bound per company (1000 postings) — no company we target is this big

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.nowcoder.com/",
}

_companies_path = Path(__file__).parent.parent / "config" / "companies_cn.json"


def load_companies() -> list[dict]:
    with open(_companies_path, encoding="utf-8") as f:
        return json.load(f)


def _parse_ext(ext_raw: str) -> str:
    """`ext` is a JSON-encoded string with `requirements` (任职要求) and
    `infos` (岗位职责) sub-fields — concatenate into one description blob."""
    if not ext_raw:
        return ""
    try:
        ext = json.loads(ext_raw)
    except (json.JSONDecodeError, TypeError):
        return ext_raw[:3000]
    parts = []
    if ext.get("infos"):
        parts.append("岗位职责：" + ext["infos"])
    if ext.get("requirements"):
        parts.append("任职要求：" + ext["requirements"])
    return "\n\n".join(parts)[:3000]


def fetch_jobs_for_company(company: dict) -> list[dict]:
    """Fetches and paginates every posting for one company via Nowcoder's
    open job/list/v2 API. Resilient: any failure logs and returns whatever
    was collected so far, never raises — one company's failure shouldn't
    kill the whole run (same resilience pattern as fetcher.py)."""
    name = company["name"]
    company_id = company["nowcoder_company_id"]
    jobs = []

    try:
        for page in range(1, MAX_PAGES + 1):
            resp = requests.post(
                JOB_LIST_URL,
                headers=HEADERS,
                data={
                    "recruitType": "0",
                    "page": str(page),
                    "pageSize": str(PAGE_SIZE),
                    "companyId": str(company_id),
                },
                timeout=20,
            )
            resp.raise_for_status()
            body = resp.json()
            if body.get("code") != 0:
                logger.warning(f"Nowcoder API error for {name}: {body.get('msg')}")
                break

            data = body.get("data", {})
            entries = data.get("datas", [])
            if not entries:
                break

            for entry in entries:
                d = entry.get("data", {})
                jobs.append({
                    "company": name,
                    "company_id": company_id,
                    "job_id": str(d.get("id", "")),
                    "title": d.get("jobName", ""),
                    "career_job_name": d.get("careerJobName", ""),
                    "location": d.get("jobCity", ""),
                    "description": _parse_ext(d.get("ext", "")),
                    "salary_min": d.get("salaryMin"),
                    "salary_max": d.get("salaryMax"),
                    "degree": d.get("eduLevel"),
                    "recruit_type": d.get("recruitType"),
                    "posted_at": "",  # refreshTime is epoch ms; left blank, not worth the noise
                    "url": f"https://www.nowcoder.com/enterprise/{company_id}",
                    "ats": "nowcoder",
                    "tier": None,
                    "company_source": "nowcoder_curated",
                })

            total_page = data.get("totalPage", 1)
            if page >= total_page:
                break
            time.sleep(0.3)  # be polite between pages of the same company

    except Exception as e:
        logger.warning(f"Nowcoder fetch failed for {name} (got {len(jobs)} before failure): {e}")

    time.sleep(0.3)  # be polite between companies
    return jobs


def fetch_all_company_jobs(companies: list[dict] | None = None) -> list[dict]:
    """Sequential, not threaded — the curated company list is small (single
    digits to low tens), so parallelism isn't worth the added complexity of
    being polite to a single upstream API from multiple threads at once."""
    if companies is None:
        companies = load_companies()

    all_jobs = []
    for company in companies:
        jobs = fetch_jobs_for_company(company)
        logger.info(f"  → {company['name']}: {len(jobs)} postings")
        all_jobs.extend(jobs)
    return all_jobs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    jobs = fetch_all_company_jobs()
    print(f"\nTotal postings fetched: {len(jobs)}")
    from collections import Counter
    by_career = Counter(j["career_job_name"] for j in jobs)
    for name, count in by_career.most_common(20):
        print(f"  {name or '(none)'}: {count}")
