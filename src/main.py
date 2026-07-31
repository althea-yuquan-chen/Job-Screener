"""
main.py — daily job screener orchestrator.

Flow:
  1. Discover companies dynamically (SimplifyJobs feed + supplemental list)
  2. Fetch all job listings from each company's ATS, in parallel
  3. Keyword pre-filter (fast, free)
  4. Deduplicate against previously-seen jobs
  5. Enrich Workday survivors with full JD text (deferred — one request per
     company for the list, but description text needs a per-job request)
  6. Process remaining jobs one scoring-chunk at a time: AI score the chunk
     (Claude API), tailor + upload resumes for anything scoring high enough,
     write those rows to the Google Sheet, then mark the chunk as seen —
     all before moving to the next chunk (see run() docstring for why).
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
from fetcher   import fetch_all_companies, fetch_workday_descriptions, fetch_smartrecruiters_descriptions, fetch_eightfold_descriptions
from filter    import passes_keyword_filter, passes_work_auth_filter, job_key
from scorer    import score_jobs_batch, CHUNK_SIZE as SCORE_CHUNK_SIZE
from tailor    import tailor_resumes
from drive     import upload_resume
from sheets    import append_jobs

# ── Config ─────────────────────────────────────────────────────────────────────
SEEN_IDS_FILE = Path(__file__).parent.parent / ".seen_job_ids.json"
SCORE_THRESHOLD = 65  # jobs scoring at/above this get a tailored resume + Drive upload, and are written to the sheet


def load_seen_ids() -> set:
    if SEEN_IDS_FILE.exists():
        return set(json.loads(SEEN_IDS_FILE.read_text()))
    return set()


def save_seen_ids(seen: set):
    SEEN_IDS_FILE.write_text(json.dumps(list(seen)))


def _process_chunk(chunk: list[dict]) -> tuple[int, int]:
    """
    Scores one chunk, then immediately tailors/uploads/writes anything that
    qualifies. Returns (num_qualifying, num_written). Never raises -- a
    failure tailoring/uploading/writing this chunk is logged and swallowed,
    since the chunk's jobs still got scored and should still be marked seen
    by the caller rather than retried forever.
    """
    score_jobs_batch(chunk, chunk_size=len(chunk))  # already <= SCORE_CHUNK_SIZE -- one API call
    qualifying = [j for j in chunk if j.get("score", 0) >= SCORE_THRESHOLD]
    if not qualifying:
        return 0, 0

    try:
        pdf_paths = tailor_resumes(qualifying)
        for i, j in enumerate(qualifying):
            pdf_path = pdf_paths.get(i)
            if not pdf_path:
                j["resume_link"] = ""
                continue
            filename = f"{j['company']} — {j['title']}.pdf".replace("/", "-")
            link = upload_resume(pdf_path, filename)
            j["resume_link"] = link or ""

        rows_written = append_jobs(qualifying)
    except Exception as e:
        logger.warning(f"Tailoring/upload/sheet-write failed for this chunk: {e}")
        return len(qualifying), 0

    return len(qualifying), rows_written


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
    # Deliberately does NOT mark these jobs as seen yet (unlike a plain
    # dedup) -- scoring/tailoring can run long enough to hit the workflow
    # timeout, and if a job is marked seen before it's actually been through
    # the pipeline, an interrupted run permanently drops it (next run's
    # dedup skips it forever, even though it was never scored). Each job is
    # only added to seen_ids once its chunk has actually finished processing
    # -- see the loop below.
    new_jobs = [j for j in keyword_passed if job_key(j) not in seen_ids]
    logger.info(f"New (not previously seen): {len(new_jobs)} postings")

    if not new_jobs:
        logger.info("No new jobs to score today. Done.")
        return

    # ── Step 5: Enrich Workday/SmartRecruiters survivors with full JD text ────
    # Workday and SmartRecruiters list endpoints don't include description
    # text (unlike Greenhouse/Lever/Ashby) — fetching it is one extra request
    # per job, so this only runs on jobs that already survived the keyword
    # filter + dedupe.
    workday_jobs = [j for j in new_jobs if j.get("ats") == "workday"]
    if workday_jobs:
        logger.info(f"Fetching full descriptions for {len(workday_jobs)} Workday postings...")
        fetch_workday_descriptions(workday_jobs)

    smartrecruiters_jobs = [j for j in new_jobs if j.get("ats") == "smartrecruiters"]
    if smartrecruiters_jobs:
        logger.info(f"Fetching full descriptions for {len(smartrecruiters_jobs)} SmartRecruiters postings...")
        fetch_smartrecruiters_descriptions(smartrecruiters_jobs)

    eightfold_jobs = [j for j in new_jobs if j.get("ats") == "eightfold"]
    if eightfold_jobs:
        logger.info(f"Fetching full descriptions for {len(eightfold_jobs)} Eightfold postings...")
        fetch_eightfold_descriptions(eightfold_jobs)

    # ── Step 5.5: Work-authorization filter ───────────────────────────────────
    # Now that every job has full JD text, drop postings that require US
    # citizenship/permanent residency/"US persons"/security clearance/no
    # sponsorship — hard exclude, not just a scoring penalty (candidate needs
    # H-1B sponsorship).
    new_jobs = [j for j in new_jobs if passes_work_auth_filter(j)]
    logger.info(f"After work-authorization filter: {len(new_jobs)} postings")

    if not new_jobs:
        logger.info("No jobs left after work-authorization filter. Done.")
        return

    # ── Step 6: score → tailor → write, one chunk at a time ──────────────────
    # Processing (and persisting) a full chunk before moving to the next one
    # means an interruption (timeout, crash) only loses at most one chunk's
    # worth of work -- everything before it is already written to the sheet,
    # and everything after it is still unmarked-seen so tomorrow's run picks
    # up where this one left off, instead of the old all-or-nothing behavior
    # where nothing was written until every job had been scored.
    logger.info(f"\nScoring {len(new_jobs)} jobs with Claude API, {SCORE_CHUNK_SIZE} at a time...")
    total_qualifying = 0
    total_written = 0

    for start in range(0, len(new_jobs), SCORE_CHUNK_SIZE):
        chunk = new_jobs[start:start + SCORE_CHUNK_SIZE]
        logger.info(f"  Chunk {start + 1}-{start + len(chunk)} of {len(new_jobs)}...")

        num_qualifying, num_written = _process_chunk(chunk)
        total_qualifying += num_qualifying
        total_written += num_written

        for j in chunk:
            seen_ids.add(job_key(j))
        save_seen_ids(seen_ids)

        for j in sorted(chunk, key=lambda j: j.get("score", 0), reverse=True)[:3]:
            logger.info(f"    {j.get('score', 0):3d}%  [{j['company']}]  {j['title']}  ({j['location']})")

    logger.info("\n" + "=" * 60)
    logger.info(f"Done. {len(new_jobs)} jobs scored, {total_written} written, "
                f"{total_qualifying} tailored resumes generated.")
    logger.info("=" * 60)


if __name__ == "__main__":
    run()
