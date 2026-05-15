# Job Screener — Setup Guide

This runs every weekday morning, checks 26 target companies for new job postings,
scores them against your profile with Claude AI, writes results to Google Sheets,
and emails you when something scores 90%+.

**Time to set up: ~45 minutes (one-time)**
**Ongoing cost: ~$1–3/month in Claude API credits**

---

## What you need before starting

- A GitHub account (free)
- A Google account (your existing Gmail)
- An Anthropic account with API access

---

## Step 1 — Create the GitHub repository

1. Go to https://github.com/new
2. Name it `job-screener` (private repo recommended)
3. Click **Create repository**
4. Upload all files from this project into the repo, preserving the folder structure:
   ```
   job-screener/
   ├── .github/workflows/daily_screener.yml
   ├── src/
   │   ├── main.py
   │   ├── fetcher.py
   │   ├── filter.py
   │   ├── scorer.py
   │   ├── sheets.py
   │   └── notifier.py
   ├── config/
   │   ├── companies.json
   │   ├── filters.json
   │   └── candidate_profile.txt
   └── requirements.txt
   ```

   Easiest way: drag-and-drop folders into the GitHub web UI, or use:
   ```bash
   git init
   git remote add origin https://github.com/YOUR_USERNAME/job-screener.git
   git add .
   git commit -m "Initial setup"
   git push -u origin main
   ```

---

## Step 2 — Get your Anthropic API Key

1. Go to https://console.anthropic.com
2. Click **API Keys** in the left sidebar
3. Click **Create Key** → name it `job-screener`
4. Copy the key (starts with `sk-ant-...`) — **you only see it once**
5. Save it somewhere safe temporarily

---

## Step 3 — Set up Google Cloud (for Sheets + Gmail)

This is the most involved step. Follow carefully.

### 3a. Create a Google Cloud project

1. Go to https://console.cloud.google.com
2. Click the project dropdown at the top → **New Project**
3. Name it `job-screener` → **Create**
4. Make sure this project is selected in the dropdown

### 3b. Enable the required APIs

1. Go to **APIs & Services → Library**
2. Search for and enable each of these (click each → Enable):
   - **Google Sheets API**
   - **Gmail API**

### 3c. Create a Service Account

1. Go to **APIs & Services → Credentials**
2. Click **+ Create Credentials → Service Account**
3. Name: `job-screener-bot` → **Create and Continue**
4. Role: **Editor** → **Continue** → **Done**
5. Click the service account you just created
6. Go to the **Keys** tab → **Add Key → Create new key → JSON**
7. A `.json` file will download — this is your `GOOGLE_CREDENTIALS_JSON`

### 3d. Note the service account email

On the service account page, copy the email — it looks like:
`job-screener-bot@job-screener-XXXXX.iam.gserviceaccount.com`

You'll need this in Step 4.

### 3e. Enable Gmail domain-wide delegation

Since Gmail API requires impersonation:

1. Go to https://admin.google.com (if you use Google Workspace)
   - OR: for personal Gmail, skip domain-wide delegation and instead:
   - Go to **APIs & Services → OAuth consent screen**
   - Set to **External**, fill in app name `job-screener`, add your email as test user
   - Back in Credentials: create an **OAuth 2.0 Client ID** (Desktop app type)
   - Download the OAuth client JSON

> **Simplest option for personal Gmail:** Use **SendGrid** or **Mailgun** free tier
> instead of Gmail API (10,000 emails/month free). If you'd prefer this, let me
> know and I'll swap the notifier to use SMTP instead — much simpler setup.

---

## Step 4 — Create the Google Sheet

1. Go to https://sheets.google.com
2. Create a new blank spreadsheet
3. Name it `Job Screener — Results`
4. Create a sheet tab named **Jobs** (rename Sheet1)
5. Share the sheet with your service account email from Step 3d:
   - Click **Share** (top right)
   - Paste the service account email
   - Role: **Editor**
   - Click **Send**
6. Copy the **Sheet ID** from the URL:
   ```
   https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID_IS_HERE/edit
   ```

---

## Step 5 — Add secrets to GitHub

1. In your GitHub repo, go to **Settings → Secrets and variables → Actions**
2. Click **New repository secret** for each of the following:

| Secret Name | Value |
|---|---|
| `ANTHROPIC_API_KEY` | Your Anthropic key from Step 2 |
| `GOOGLE_CREDENTIALS_JSON` | The entire contents of the `.json` file from Step 3c |
| `GOOGLE_SHEET_ID` | The Sheet ID from Step 4 |
| `ALERT_EMAIL` | Your Gmail address (e.g. `chenyuquan297@hotmail.com`) |

For `GOOGLE_CREDENTIALS_JSON`: open the downloaded `.json` file in a text editor,
select all, copy, paste as the secret value.

---

## Step 6 — Test the workflow manually

1. In your GitHub repo, go to **Actions**
2. Click **Daily Job Screener** in the left sidebar
3. Click **Run workflow → Run workflow** (green button)
4. Watch the logs — it should take 5–15 minutes
5. Check your Google Sheet for results
6. If 90%+ matches are found, check your email

---

## Step 7 — Activate the daily schedule

The workflow is already configured to run weekdays at 8:00 AM Eastern.
It activates automatically once you push to the `main` branch.

To confirm it's scheduled: go to **Actions → Daily Job Screener** and you'll see
the next scheduled run listed.

---

## Understanding the Google Sheet columns

| Column | Description |
|---|---|
| Date Found | When the screener found this job |
| Score | AI match score 0–100 |
| Tier | P1 (AI roles), P2 (tech consulting), P3 (MBB) |
| Company | Company name |
| Title | Job title |
| Location | Location or Remote |
| Summary | One-sentence AI assessment |
| Match Reasons | Why it fits your profile |
| Concerns | Potential gaps |
| URL | Direct link to apply |
| Posted Date | When the job was posted |
| Job ID | Internal ID (used for deduplication) |
| ATS | Which platform (Greenhouse/Lever/Workday) |

---

## Customizing

**Add a new company:**
Edit `config/companies.json`. You need to know which ATS they use:
- Check if their careers URL contains `greenhouse.io`, `lever.co`, or `myworkday.com`
- Find the company ID in their careers URL (e.g. `boards.greenhouse.io/COMPANY_ID`)

**Change keyword filters:**
Edit `config/filters.json` — add/remove title keywords or locations.

**Update your profile:**
Edit `config/candidate_profile.txt` as your experience grows.

**Change email threshold:**
In `src/notifier.py`, change `HIGH_SCORE_THRESHOLD = 90` to any value.

**Run on weekends too:**
In `.github/workflows/daily_screener.yml`, change `1-5` to `*` in the cron line.

---

## Estimated costs

- **GitHub Actions:** Free (2,000 minutes/month on free tier; each run ~10 min)
- **Claude API:** ~$0.05–0.15 per run (depends on jobs found; ~30–100 scored jobs/day)
- **Google APIs:** Free (well within free tier limits)
- **Total: ~$1–5/month** starting in July when postings pick up

---

## Troubleshooting

**"No module named X"** → Check `requirements.txt` is in the root directory

**Sheet not updating** → Confirm the service account email has Editor access to the sheet

**Greenhouse returning 0 jobs** → The company ID may be wrong; check their careers URL

**Email not sending** → Gmail API setup is complex; consider switching to SMTP (let me know)

**Score always 0** → Check `ANTHROPIC_API_KEY` is set correctly in GitHub Secrets
