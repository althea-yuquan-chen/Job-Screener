"""
drive.py — uploads tailored resume PDFs to a shared Google Drive folder.

Reuses the same service account as sheets.py (GOOGLE_CREDENTIALS_JSON), just
with Drive scope. Althea owns the target folder and shares it with the
service account as Editor (same pattern as the Sheet) — files the service
account creates inside that folder are then already visible to her through
the folder's own sharing, so no per-file public link is created (these PDFs
contain personal contact/education/work history and shouldn't be made
link-public).
"""

import os
import json
import logging
from pathlib import Path

from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _get_service():
    creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
    creds_dict = json.loads(creds_json)
    creds = Credentials.from_service_account_info(creds_dict, scopes=SCOPES)
    return build("drive", "v3", credentials=creds)


def upload_resume(pdf_path: Path, filename: str) -> str | None:
    """
    Uploads a tailored resume PDF into the shared Drive folder.
    Returns a Drive view link (accessible to whoever the folder is shared
    with — no public link is created), or None if the upload failed.
    """
    folder_id = os.environ.get("GOOGLE_DRIVE_FOLDER_ID")
    if not folder_id:
        logger.warning("GOOGLE_DRIVE_FOLDER_ID not set — skipping Drive upload.")
        return None

    try:
        service = _get_service()
        file_metadata = {"name": filename, "parents": [folder_id]}
        media = MediaFileUpload(str(pdf_path), mimetype="application/pdf")
        file = service.files().create(
            body=file_metadata, media_body=media, fields="id, webViewLink"
        ).execute()
        return file.get("webViewLink")
    except Exception as e:
        logger.warning(f"Drive upload failed for {filename}: {e}")
        return None
