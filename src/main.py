"""
main.py — daily job screener orchestrator.

Flow:
  1. Load company list
  2. Fetch all job listings from each company's ATS
  3. Keyword pre-filter (fast, free)
  4. AI score remaining jobs against candidate profile (Claude API)
  5. Write all scored jobs to Google Sheet (deduped)
  6. Send email alert for jobs scoring 90+
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
from fetcher   import fetch_jobs_for_company
from filter    import passes_keyword_filter, deduplicate
from scorer    import score_jobs_batch
from sheets    import append_jobs
from notifier  import send_alert

# ── Config paths ──────────────────────────────────────────────────────────────
CONFIG_DIR   = Path(__file__).parent.parent / "config"
COMPANIES    = json.loads((CONFIG_DIR / "companies.json").read_text())
SEEN_IDS_FILE = Path(__file__).parent.parent / ".seen_job_ids.json"


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

    # ── Step 1: Fetch ─────────────────────────────────────────────────────────
    all_jobs = []
    for company in COMPANIES:
        logger.info(f"Fetching: {company['name']} ({company['ats'].upper()})")
        jobs = fetch_jobs_for_company(company)
        logger.info(f"  → {len(jobs)} postings found")
        all_jobs.extend(jobs)

    logger.info(f"\nTotal raw postings fetched: {len(all_jobs)}")

    # ── Step 2: Keyword pre-filter ────────────────────────────────────────────
    keyword_passed = [j for j in all_jobs if passes_keyword_filter(j)]
    logger.info(f"After keyword filter: {len(keyword_passed)} postings")

    # ── Step 3: Deduplicate (skip jobs we've scored before) ───────────────────
    new_jobs, seen_ids = deduplicate(keyword_passed, seen_ids)
    logger.info(f"New (not previously seen): {len(new_jobs)} postings")

    if not new_jobs:
        logger.info("No new jobs to score today. Done.")
        save_seen_ids(seen_ids)
        return

    # ── Step 4: AI scoring ────────────────────────────────────────────────────
    logger.info(f"\nScoring {len(new_jobs)} jobs with Claude API...")
    scored_jobs = score_jobs_batch(new_jobs)

    # Sort by score for logging
    scored_jobs.sort(key=lambda j: j.get("score", 0), reverse=True)

    logger.info("\n── TOP MATCHES ──────────────────────────────────────")
    for j in scored_jobs[:10]:
        logger.info(f"  {j['score']:3d}%  [{j['company']}]  {j['title']}  ({j['location']})")

    high_score_count = sum(1 for j in scored_jobs if j.get("score", 0) >= 90)
    logger.info(f"\nJobs scoring 90%+: {high_score_count}")

    # ── Step 5: Write to Google Sheet ─────────────────────────────────────────
    logger.info("\nWriting to Google Sheet...")
    rows_written = append_jobs(scored_jobs)
    logger.info(f"Rows written: {rows_written}")

    # ── Step 6: Email alert ───────────────────────────────────────────────────
    if high_score_count > 0:
        logger.info("Sending email alert for high-score matches...")
        send_alert(scored_jobs)

    # ── Save seen IDs ─────────────────────────────────────────────────────────
    save_seen_ids(seen_ids)

    logger.info("\n" + "=" * 60)
    logger.info(f"Done. {len(scored_jobs)} jobs scored, {rows_written} written, "
                f"{high_score_count} alerts sent.")
    logger.info("=" * 60)


if __name__ == "__main__":
    run()
