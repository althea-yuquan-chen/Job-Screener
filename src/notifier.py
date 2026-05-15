"""
notifier.py — sends a Gmail alert when high-scoring jobs (90+) are found.
Uses Gmail API with service account credentials.
"""

import os
import json
import base64
import logging
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from google.oauth2.service_account import Credentials
from googleapiclient.discovery import build

logger = logging.getLogger(__name__)

SCOPES = ["https://www.googleapis.com/auth/gmail.send"]
HIGH_SCORE_THRESHOLD = 90


def _get_gmail_service():
    creds_json = os.environ["GOOGLE_CREDENTIALS_JSON"]
    creds_dict = json.loads(creds_json)
    # Gmail requires domain-wide delegation — use the user email to impersonate
    sender_email = os.environ["ALERT_EMAIL"]
    creds = Credentials.from_service_account_info(
        creds_dict,
        scopes=SCOPES,
        subject=sender_email
    )
    return build("gmail", "v1", credentials=creds)


def _build_email_html(high_score_jobs: list[dict]) -> str:
    tier_colors = {1: "#185FA5", 2: "#0F6E56", 3: "#993C1D"}

    job_cards = ""
    for job in high_score_jobs:
        tier = job.get("tier", 1)
        color = tier_colors.get(tier, "#185FA5")
        reasons_html = "".join(
            f"<li>{r}</li>" for r in job.get("match_reasons", [])
        )
        concerns_html = ""
        if job.get("concerns"):
            concerns_list = "".join(f"<li>{c}</li>" for c in job["concerns"])
            concerns_html = f"""
            <p style="margin:8px 0 4px;font-size:13px;color:#666;">Concerns:</p>
            <ul style="margin:0;padding-left:16px;color:#888;font-size:13px;">{concerns_list}</ul>
            """

        job_cards += f"""
        <div style="background:#fff;border:1px solid #e0e0e0;border-radius:8px;
                    padding:20px;margin-bottom:16px;border-left:4px solid {color};">
          <div style="display:flex;align-items:center;gap:12px;margin-bottom:8px;">
            <span style="font-size:28px;font-weight:700;color:{color};">{job.get('score')}%</span>
            <div>
              <p style="margin:0;font-size:18px;font-weight:600;color:#1a1a1a;">{job.get('title')}</p>
              <p style="margin:0;font-size:14px;color:#555;">{job.get('company')} · {job.get('location','Remote/US')}</p>
            </div>
          </div>
          <p style="margin:8px 0;font-size:14px;color:#333;font-style:italic;">"{job.get('summary')}"</p>
          <p style="margin:8px 0 4px;font-size:13px;color:#666;">Why it fits:</p>
          <ul style="margin:0;padding-left:16px;color:#333;font-size:13px;">{reasons_html}</ul>
          {concerns_html}
          <a href="{job.get('url','#')}"
             style="display:inline-block;margin-top:14px;padding:8px 18px;
                    background:{color};color:#fff;border-radius:6px;
                    text-decoration:none;font-size:13px;font-weight:500;">
            View & Apply →
          </a>
        </div>
        """

    return f"""
    <html><body style="font-family:-apple-system,sans-serif;background:#f5f5f5;
                       padding:24px;max-width:640px;margin:0 auto;">
      <div style="background:#fff;border-radius:12px;padding:28px;
                  border:1px solid #e0e0e0;margin-bottom:20px;">
        <h1 style="margin:0 0 4px;font-size:22px;color:#1a1a1a;">
          🎯 {len(high_score_jobs)} High-Match Job{"s" if len(high_score_jobs)>1 else ""} Found
        </h1>
        <p style="margin:0;color:#666;font-size:14px;">
          Score 90%+ · Daily Job Screener · Apply before these get crowded
        </p>
      </div>
      {job_cards}
      <p style="text-align:center;color:#aaa;font-size:12px;margin-top:20px;">
        Full results in your
        <a href="https://docs.google.com/spreadsheets/d/{os.environ.get('GOOGLE_SHEET_ID','')}"
           style="color:#185FA5;">Google Sheet</a>
        · Automated by your job screener
      </p>
    </body></html>
    """


def send_alert(high_score_jobs: list[dict]) -> bool:
    """
    Sends an email alert for jobs scoring 90+.
    Returns True if sent successfully.
    """
    jobs_to_alert = [j for j in high_score_jobs if j.get("score", 0) >= HIGH_SCORE_THRESHOLD]

    if not jobs_to_alert:
        logger.info("No high-score jobs — skipping email alert.")
        return False

    recipient = os.environ["ALERT_EMAIL"]
    subject = f"🎯 {len(jobs_to_alert)} Job Match{'es' if len(jobs_to_alert)>1 else ''} Score 90%+ — Apply Now"

    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"]    = recipient
    msg["To"]      = recipient

    html_content = _build_email_html(jobs_to_alert)
    msg.attach(MIMEText(html_content, "html"))

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode()

    try:
        service = _get_gmail_service()
        service.users().messages().send(
            userId="me",
            body={"raw": raw}
        ).execute()
        logger.info(f"Alert email sent for {len(jobs_to_alert)} high-score jobs.")
        return True
    except Exception as e:
        logger.error(f"Failed to send email alert: {e}")
        return False
