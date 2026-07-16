"""
discover_cn.py — dynamically discovers which companies on Nowcoder (牛客网)
are currently recruiting for target roles in the target city, then pulls
every posting from each.

Nowcoder's general keyword search (nowcoder.com/jobs/fulltime) is login-gated
(confirmed live: renders nothing but a login modal for an unauthenticated
request). But two other endpoints are open and unauthenticated:

1. The 校招日程 (school-schedule) directory
   (www.nowcoder.com/np-api/u/school-schedule/list-card) — lists every
   company Nowcoder tracks with its current recruiting batch, the cities it's
   hiring in (cityList), the job categories it's currently posting
   (careerNameList), and its application-window deadline (wangshenEndDate).
   This replaces an earlier hand-curated company list (config/companies_cn.json,
   now removed) — Althea explicitly asked not to gatekeep by company, only by
   job content + location, and this endpoint makes that possible: filter to
   companies whose cityList contains the target city AND whose careerNameList
   intersects the target categories AND whose application window hasn't
   closed yet (of ~23,700 total companies tracked, ~1150 matched Shanghai +
   Data/AI category at all, but only ~60 had a still-open application window
   — expired postings are the overwhelming majority, hence the freshness
   filter matters a lot for keeping runtime sane).
2. Each company's own job list (nowpick.nowcoder.com/u/company/job/list/v2)
   — no auth headers, no signature, no cookies required (confirmed live
   against several companies). That's what fetch_jobs_for_company calls.

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
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import requests

logger = logging.getLogger(__name__)

SCHEDULE_LIST_URL = "https://www.nowcoder.com/np-api/u/school-schedule/list-card"
SCHEDULE_PAGE_SIZE = 1000  # confirmed the API accepts this; ~24 requests covers all ~23,700 companies

JOB_LIST_URL = "https://nowpick.nowcoder.com/u/company/job/list/v2"
JOB_PAGE_SIZE = 50   # API confirmed to accept at least 20; larger cuts request count
MAX_PAGES = 20        # safety bound per company (1000 postings)

HEADERS = {
    "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                  "AppleWebKit/537.36 (KHTML, like Gecko) "
                  "Chrome/120.0.0.0 Safari/537.36",
    "Referer": "https://www.nowcoder.com/",
}

_filters_path = Path(__file__).parent.parent / "config" / "filters_cn.json"
with open(_filters_path, encoding="utf-8") as f:
    _FILTERS = json.load(f)

TARGET_CITY = _FILTERS["target_city"]
TARGET_CAREER_KEYWORDS = _FILTERS["include_career_job_keywords"]


def _fetch_schedule_page(page: int, max_retries: int = 4) -> dict:
    """school-schedule/list-card sits behind an Aliyun WAF that returns a
    text/html JS-challenge page (not JSON) under bursty traffic — observed
    live during development after ~10 rapid-fire probing requests. A plain
    request retry with backoff clears it in practice (it appears to be a
    short rate-window block, not a persistent IP ban), so retry rather than
    fail outright on the first non-JSON response."""
    delay = 2
    for attempt in range(max_retries):
        resp = requests.post(
            SCHEDULE_LIST_URL,
            headers=HEADERS,
            data={"query": "", "propertyId": "", "page": str(page), "pageSize": str(SCHEDULE_PAGE_SIZE), "tab": "0"},
            timeout=20,
        )
        resp.raise_for_status()
        if "application/json" in resp.headers.get("Content-Type", ""):
            return resp.json()
        logger.warning(f"school-schedule page {page}: got non-JSON response (likely WAF rate-limit), "
                        f"retrying in {delay}s (attempt {attempt + 1}/{max_retries})")
        time.sleep(delay)
        delay *= 2
    raise RuntimeError(f"school-schedule/list-card kept returning non-JSON after {max_retries} retries "
                        f"(page {page}) — likely still rate-limited, try again later")


def discover_companies() -> list[dict]:
    """Paginates the full school-schedule directory once, then filters to
    companies currently recruiting in TARGET_CITY for TARGET_CAREER_KEYWORDS
    with a still-open application window. Returns [{name, nowcoder_company_id}]."""
    all_entries = []
    page = 1
    while True:
        body = _fetch_schedule_page(page)
        data = body.get("data", {})
        entries = data.get("datas", [])
        if not entries:
            break
        all_entries.extend(entries)
        if page >= data.get("totalPage", 1):
            break
        page += 1
        time.sleep(1)  # pace requests to this specific host to avoid re-tripping the WAF

    now_ms = int(time.time() * 1000)
    matched = []
    seen_ids = set()
    for c in all_entries:
        city_list = c.get("cityList") or []
        career_list = c.get("careerNameList") or []
        end_date = c.get("wangshenEndDate")

        if TARGET_CITY not in city_list:
            continue
        if not any(kw in career for career in career_list for kw in TARGET_CAREER_KEYWORDS):
            continue
        if end_date and end_date <= now_ms:
            continue  # application window already closed

        company_id = c.get("companyId")
        if company_id in seen_ids:
            continue
        seen_ids.add(company_id)
        matched.append({"name": c.get("name", ""), "nowcoder_company_id": company_id})

    logger.info(f"Discovered {len(matched)} companies currently recruiting in "
                f"{TARGET_CITY} for {TARGET_CAREER_KEYWORDS} (of {len(all_entries)} tracked)")
    return matched


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
                    "pageSize": str(JOB_PAGE_SIZE),
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
                    "company_source": "nowcoder_discovered",
                })

            total_page = data.get("totalPage", 1)
            if page >= total_page:
                break
            time.sleep(0.2)  # be polite between pages of the same company

    except Exception as e:
        logger.warning(f"Nowcoder fetch failed for {name} (got {len(jobs)} before failure): {e}")

    return jobs


def fetch_all_company_jobs(companies: list[dict] | None = None, max_workers: int = 8) -> list[dict]:
    """Discovers companies dynamically (unless an explicit list is passed,
    e.g. for testing) and fetches every posting for each, in parallel — the
    discovered set can run into the tens of companies, so a small thread
    pool keeps total runtime reasonable while staying polite per-request
    (same pattern as fetcher.py's fetch_all_companies)."""
    if companies is None:
        companies = discover_companies()

    all_jobs = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        futures = {executor.submit(fetch_jobs_for_company, c): c for c in companies}
        for future in as_completed(futures):
            company = futures[future]
            try:
                jobs = future.result()
                logger.info(f"  → {company['name']}: {len(jobs)} postings")
                all_jobs.extend(jobs)
            except Exception as e:
                logger.warning(f"Fetch failed for {company['name']}: {e}")
    return all_jobs


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    jobs = fetch_all_company_jobs()
    print(f"\nTotal postings fetched: {len(jobs)}")
    from collections import Counter
    by_career = Counter(j["career_job_name"] for j in jobs)
    for name, count in by_career.most_common(20):
        print(f"  {name or '(none)'}: {count}")
