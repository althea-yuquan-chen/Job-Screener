"""
drive.py — uploads tailored resume PDFs to Althea's own Google Drive folder.

Service accounts cannot create new files in a personal (non-Workspace)
Google Drive — they have no storage quota of their own, and Google requires
either Shared Drives (a Workspace-only feature) or real OAuth user
delegation instead. This was confirmed via a live 403 "storageQuotaExceeded"
error during setup, not assumed — so this module authenticates as Althea
herself via a pre-obtained OAuth refresh token (see
scripts/get_drive_refresh_token.py for the one-time setup that produces it),
rather than the service account used by sheets.py. Uploads count against
her own Drive quota, and files land directly in her regular Drive.
"""

import os
import logging
from pathlib import Path

from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.http import MediaFileUpload

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/drive.file"]


def _get_service():
    creds = Credentials(
        token=None,
        refresh_token=os.environ["GOOGLE_OAUTH_REFRESH_TOKEN"],
        token_uri="https://oauth2.googleapis.com/token",
        client_id=os.environ["GOOGLE_OAUTH_CLIENT_ID"],
        client_secret=os.environ["GOOGLE_OAUTH_CLIENT_SECRET"],
        scopes=SCOPES,
    )
    return build("drive", "v3", credentials=creds)


def upload_resume(pdf_path: Path, filename: str) -> str | None:
    """
    Uploads a tailored resume PDF into Althea's Drive folder.
    Returns a Drive view link, or None if the upload failed.
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
