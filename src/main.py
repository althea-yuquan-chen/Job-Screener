"""
main.py — daily job screener orchestrator.

Flow:
  1. Discover companies dynamically (SimplifyJobs feed + supplemental list)
  2. Fetch all job listings from each company's ATS, in parallel
  3. Keyword pre-filter (fast, free)
  4. Deduplicate against previously-seen jobs
  5. Enrich Workday survivors with full JD text (deferred — one request per
     company for the list, but description text needs a per-job request)
  6. AI score remaining jobs against candidate profile (Claude API)
  7. For jobs scoring high enough: generate a tailored resume PDF and upload
     it to Drive
  8. Write all scored jobs to Google Sheet (deduped), with a resume link for
     the tailored ones
"""

import json
import logging
import sys
from pathlib import Path

# ── Logging setup ─────────────────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ── Local imports ─────────────────────────────────────────────────────────────
sys.path.insert(0, str(Path(__file__).parent))
from discover  import get_companies
from fetcher   import fetch_all_companies, fetch_workday_descriptions
from filter    import passes_keyword_filter, passes_work_auth_filter, deduplicate
from scorer    import score_jobs_batch
from tailor    import tailor_resumes
from drive     import upload_resume
from sheets    import append_jobs

# ── Config ─────────────────────────────────────────────────────────────────────
SEEN_IDS_FILE = Path(__file__).parent.parent / ".seen_job_ids.json"
SCORE_THRESHOLD = 75  # jobs scoring at/above this get a tailored resume + Drive upload, and are written to the sheet


def load_seen_ids() -> set:
    if SEEN_IDS_FILE.exists():
        return set(json.loads(SEEN_IDS_FILE.read_text()))
    return set()


def save_seen_ids(seen: set):
    SEEN_IDS_FILE.write_text(json.dumps(list(seen)))


def run():
    logger.info("=" * 60)
    logger.info("JOB SCREENER — daily run starting")
    logger.info("=" * 60)

    seen_ids = load_seen_ids()

    # ── Step 1: Discover companies ────────────────────────────────────────────
    companies = get_companies()

    # ── Step 2: Fetch (parallel) ───────────────────────────────────────────────
    logger.info(f"Fetching postings from {len(companies)} companies...")
    all_jobs = fetch_all_companies(companies)
    logger.info(f"\nTotal raw postings fetched: {len(all_jobs)}")

    # ── Step 3: Keyword pre-filter ────────────────────────────────────────────
    keyword_passed = [j for j in all_jobs if passes_keyword_filter(j)]
    logger.info(f"After keyword filter: {len(keyword_passed)} postings")

    # ── Step 4: Deduplicate (skip jobs we've scored before) ───────────────────
    new_jobs, seen_ids = deduplicate(keyword_passed, seen_ids)
    logger.info(f"New (not previously seen): {len(new_jobs)} postings")

    if not new_jobs:
        logger.info("No new jobs to score today. Done.")
        save_seen_ids(seen_ids)
        return

    # ── Step 5: Enrich Workday survivors with full JD text ────────────────────
    # Workday's list endpoint doesn't include description text (unlike
    # Greenhouse/Lever) — fetching it is one extra request per job, so this
    # only runs on jobs that already survived the keyword filter + dedupe.
    workday_jobs = [j for j in new_jobs if j.get("ats") == "workday"]
    if workday_jobs:
        logger.info(f"Fetching full descriptions for {len(workday_jobs)} Workday postings...")
        fetch_workday_descriptions(workday_jobs)

    # ── Step 5.5: Work-authorization filter ───────────────────────────────────
    # Now that every job has full JD text, drop postings that require US
    # citizenship/permanent residency/"US persons"/security clearance/no
    # sponsorship — hard exclude, not just a scoring penalty (candidate needs
    # H-1B sponsorship).
    new_jobs = [j for j in new_jobs if passes_work_auth_filter(j)]
    logger.info(f"After work-authorization filter: {len(new_jobs)} postings")

    if not new_jobs:
        logger.info("No jobs left after work-authorization filter. Done.")
        save_seen_ids(seen_ids)
        return

    # ── Step 6: AI scoring ────────────────────────────────────────────────────
    logger.info(f"\nScoring {len(new_jobs)} jobs with Claude API...")
    scored_jobs = score_jobs_batch(new_jobs)

    # Sort by score for logging
    scored_jobs.sort(key=lambda j: j.get("score", 0), reverse=True)

    logger.info("\n── TOP MATCHES ──────────────────────────────────────")
    for j in scored_jobs[:10]:
        logger.info(f"  {j['score']:3d}%  [{j['company']}]  {j['title']}  ({j['location']})")

    qualifying_jobs = [j for j in scored_jobs if j.get("score", 0) >= SCORE_THRESHOLD]
    logger.info(f"\nJobs scoring {SCORE_THRESHOLD}+ (tailored resume + written to sheet): {len(qualifying_jobs)}")

    # ── Step 7: Tailor + upload resumes for high-scoring matches ──────────────
    pdf_paths = tailor_resumes(qualifying_jobs)
    for i, j in enumerate(qualifying_jobs):
        pdf_path = pdf_paths.get(i)
        if not pdf_path:
            j["resume_link"] = ""
            continue
        filename = f"{j['company']} — {j['title']}.pdf".replace("/", "-")
        link = upload_resume(pdf_path, filename)
        j["resume_link"] = link or ""

    # ── Step 8: Write to Google Sheet ──────────────────────────────────────────
    logger.info("\nWriting to Google Sheet...")
    rows_written = append_jobs(qualifying_jobs)
    logger.info(f"Rows written: {rows_written}")

    # ── Save seen IDs ─────────────────────────────────────────────────────────
    save_seen_ids(seen_ids)

    logger.info("\n" + "=" * 60)
    logger.info(f"Done. {len(scored_jobs)} jobs scored, {rows_written} written, "
                f"{len(qualifying_jobs)} tailored resumes generated.")
    logger.info("=" * 60)


if __name__ == "__main__":
    run()
