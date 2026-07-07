"""
sheets.py — writes scored jobs to a Google Sheet (master log, one row per job).
Deduplicates by checking existing job_id values in the sheet.
"""

import os
import json
import logging
from datetime import date
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]

SHEET_COLUMNS = [
    "Date Found",
    "Score",
    "Tier",
    "Company",
    "Title",
    "Location",
    "Summary",
    "Match Reasons",
    "Concerns",
    "URL",
    "Posted Date",
    "Job ID",
    "ATS",
    "Tailored Resume",
    "Source",
]

TIER_LABELS = {1: "P1 — AI/Agent", 2: "P2 — Tech Consulting", 3: "P3 — MBB Stretch"}
SOURCE_LABELS = {"feed": "New-Grad Feed", "supplemental": "Curated", "unknown": ""}


def _get_service():
    creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return build("sheets", "v4", credentials=creds)


def _get_sheet_id():
    return os.environ["GOOGLE_SHEET_ID"]


def get_existing_job_ids(service) -> set:
    """Pull all existing Job IDs from column L (index 11) to avoid duplicates."""
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=_get_sheet_id(),
            range="Jobs!L2:L"  # Column L = Job ID, skip header row
        ).execute()
        values = result.get("values", [])
        return {row[0] for row in values if row}
    except Exception as e:
        logger.warning(f"Could not fetch existing job IDs: {e}")
        return set()


def ensure_header(service):
    """Write the header row if the sheet is empty."""
    try:
        result = service.spreadsheets().values().get(
            spreadsheetId=_get_sheet_id(),
            range="Jobs!A1:O1"
        ).execute()
        if not result.get("values"):
            service.spreadsheets().values().update(
                spreadsheetId=_get_sheet_id(),
                range="Jobs!A1",
                valueInputOption="RAW",
                body={"values": [SHEET_COLUMNS]}
            ).execute()
            logger.info("Header row written to sheet.")
    except Exception as e:
        logger.warning(f"Could not ensure header: {e}")


def append_jobs(jobs: list[dict]) -> int:
    """
    Append new scored jobs to the sheet.
    Skips any job whose job_id already exists.
    Returns count of rows actually written.
    """
    service = _get_service()
    ensure_header(service)
    existing_ids = get_existing_job_ids(service)

    today = date.today().isoformat()
    rows = []

    for job in jobs:
        job_id = str(job.get("job_id", ""))
        # Deduplicate: skip if we've already logged this job
        if job_id and job_id in existing_ids:
            continue

        match_reasons = " | ".join(job.get("match_reasons", []))
        concerns      = " | ".join(job.get("concerns", []))
        tier = job.get("tier")
        tier_label = TIER_LABELS.get(tier, "") if tier is not None else ""
        source_label = SOURCE_LABELS.get(job.get("company_source", "unknown"), "")

        row = [
            today,
            job.get("score", ""),
            tier_label,
            job.get("company", ""),
            job.get("title", ""),
            job.get("location", ""),
            job.get("summary", ""),
            match_reasons,
            concerns,
            job.get("url", ""),
            job.get("posted_at", ""),
            job_id,
            job.get("ats", ""),
            job.get("resume_link", ""),
            source_label,
        ]
        rows.append(row)

    if not rows:
        logger.info("No new jobs to write to sheet.")
        return 0

    # Sort by score descending before writing
    rows.sort(key=lambda r: r[1] if isinstance(r[1], int) else 0, reverse=True)

    service.spreadsheets().values().append(
        spreadsheetId=_get_sheet_id(),
        range="Jobs!A1",
        valueInputOption="RAW",
        insertDataOption="INSERT_ROWS",
        body={"values": rows}
    ).execute()

    logger.info(f"Wrote {len(rows)} new jobs to Google Sheet.")
    return len(rows)
