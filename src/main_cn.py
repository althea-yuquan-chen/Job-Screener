"""
main_cn.py — China (Nowcoder) job screener, run manually: `python src/main_cn.py`

Parallel to main.py (the US pipeline) but deliberately NOT wired into the
same daily GitHub Actions cron — the company list here is a small, hand-
curated set (config/companies_cn.json), so there's no need for full
automation yet, and Althea asked for a manually-run script for v1.

Flow:
  1. Discover: fetch every posting for the curated company list from
     Nowcoder's open per-company job API (discover_cn.py)
  2. Filter: category keyword pre-filter (Data / AI应用开发 only — PM is
     out of scope for this automated pipeline; she works PM by hand) +
     dedupe against .seen_job_ids_cn.json (filter_cn.py)
  3. Score: Claude Code CLI against a China-market candidate profile
     (scorer_cn.py)
  4. For jobs scoring high enough: attach the existing static
     resume/resume_zh.pdf (no per-job tailoring in v1 — see plan notes)
  5. Write to the "China Jobs" tab of the same Google Sheet used by the US
     pipeline (sheets.py, sheet_name parameterized for this)
"""

import json
import logging
import sys
from pathlib import Path

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-8s  %(message)s",
    datefmt="%H:%M:%S",
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

sys.path.insert(0, str(Path(__file__).parent))
from discover_cn import fetch_all_company_jobs
from filter_cn   import passes_category_filter, deduplicate
from scorer_cn   import score_jobs_batch
from drive       import upload_resume
from sheets      import append_jobs

SEEN_IDS_FILE = Path(__file__).parent.parent / ".seen_job_ids_cn.json"
RESUME_ZH_PDF = Path(__file__).parent.parent / "resume" / "resume_zh.pdf"
SHEET_NAME = "China Jobs"
SCORE_THRESHOLD = 75


def load_seen_ids() -> set:
    if SEEN_IDS_FILE.exists():
        return set(json.loads(SEEN_IDS_FILE.read_text()))
    return set()


def save_seen_ids(seen: set):
    SEEN_IDS_FILE.write_text(json.dumps(list(seen)))


def run():
    logger.info("=" * 60)
    logger.info("CHINA JOB SCREENER (Nowcoder) — manual run starting")
    logger.info("=" * 60)

    seen_ids = load_seen_ids()

    # ── Step 1: Discover + fetch ──────────────────────────────────────────────
    all_jobs = fetch_all_company_jobs()
    logger.info(f"\nTotal raw postings fetched: {len(all_jobs)}")

    # ── Step 2: Category filter ───────────────────────────────────────────────
    category_passed = [j for j in all_jobs if passes_category_filter(j)]
    logger.info(f"After category filter (Data / AI应用开发): {len(category_passed)} postings")

    # ── Step 3: Deduplicate ───────────────────────────────────────────────────
    new_jobs, seen_ids = deduplicate(category_passed, seen_ids)
    logger.info(f"New (not previously seen): {len(new_jobs)} postings")

    if not new_jobs:
        logger.info("No new jobs to score today. Done.")
        save_seen_ids(seen_ids)
        return

    # ── Step 4: AI scoring ────────────────────────────────────────────────────
    logger.info(f"\nScoring {len(new_jobs)} jobs...")
    scored_jobs = score_jobs_batch(new_jobs)
    scored_jobs.sort(key=lambda j: j.get("score", 0), reverse=True)

    logger.info("\n── TOP MATCHES ──────────────────────────────────────")
    for j in scored_jobs[:10]:
        logger.info(f"  {j['score']:3d}%  [{j['company']}]  {j['title']}  ({j['location']})")

    qualifying_jobs = [j for j in scored_jobs if j.get("score", 0) >= SCORE_THRESHOLD]
    logger.info(f"\nJobs scoring {SCORE_THRESHOLD}+ (written to sheet): {len(qualifying_jobs)}")

    # ── Step 5: Attach the static Chinese resume (uploaded once per run) ──────
    resume_link = ""
    if qualifying_jobs:
        if RESUME_ZH_PDF.exists():
            resume_link = upload_resume(RESUME_ZH_PDF, "陈雨荃_简历.pdf") or ""
        else:
            logger.warning(f"{RESUME_ZH_PDF} not found — skipping resume upload.")
        for j in qualifying_jobs:
            j["resume_link"] = resume_link

    # ── Step 6: Write to Google Sheet ─────────────────────────────────────────
    # Google credentials (GOOGLE_CREDENTIALS_JSON / GOOGLE_SHEET_ID) are still a
    # pending one-time setup step for the whole project (see project memory) —
    # if they're not configured yet, fall back to logging what would have been
    # written instead of crashing, so the rest of the pipeline stays testable.
    logger.info(f"\nWriting to Google Sheet tab '{SHEET_NAME}'...")
    try:
        rows_written = append_jobs(qualifying_jobs, sheet_name=SHEET_NAME)
        logger.info(f"Rows written: {rows_written}")
    except KeyError as e:
        rows_written = 0
        logger.warning(f"Google Sheets not configured yet (missing env var {e}) — "
                        f"dry run only. Would have written {len(qualifying_jobs)} rows:")
        for j in qualifying_jobs:
            logger.info(f"  [DRY RUN] {j['score']:3d}%  [{j['company']}]  {j['title']}  ({j['location']})")

    # ── Save seen IDs ─────────────────────────────────────────────────────────
    save_seen_ids(seen_ids)

    logger.info("\n" + "=" * 60)
    logger.info(f"Done. {len(scored_jobs)} jobs scored, {rows_written} written.")
    logger.info("=" * 60)


if __name__ == "__main__":
    run()
