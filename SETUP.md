# Job Screener — Setup Guide

This runs every weekday morning, discovers new-grad job postings across ~350+
companies (auto-discovered daily from a public new-grad job feed, plus a
small curated list of AI-native and consulting companies), scores each one
against your profile with Claude AI, generates a tailored resume PDF for
every strong match, and writes everything to Google Sheets.

**Time to set up: ~30 minutes (one-time)**
**Ongoing cost: $0 in API credits — scoring/tailoring runs through the Claude Code CLI against your existing Claude Pro/Max subscription's included usage, not metered API billing**

---

## What you need before starting

- A GitHub account (free)
- A Google account (your existing Gmail)
- A Claude Pro or Max subscription, and the Claude Code CLI installed locally
  (`npm install -g @anthropic-ai/claude-code`) — used only once, to generate
  the token in Step 2

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
   │   ├── discover.py
   │   ├── fetcher.py
   │   ├── filter.py
   │   ├── scorer.py
   │   ├── tailor.py
   │   ├── drive.py
   │   └── sheets.py
   ├── config/
   │   ├── companies_supplemental.json
   │   ├── filters.json
   │   └── candidate_profile.txt
   ├── resume/
   │   └── resume.tex
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

## Step 2 — Get a Claude Code subscription token

The screener scores jobs and tailors resumes by running the Claude Code CLI
in headless mode (`src/claude_code_client.py`), authenticated against your
Claude subscription instead of a metered API key — so there's no per-token
bill.

1. On your own machine, make sure you're logged into Claude Code with your
   Pro/Max account (`claude` → `/login` if you haven't already)
2. Run:
   ```bash
   claude setup-token
   ```
3. This opens a browser to confirm, then prints a long-lived token (valid
   ~1 year) — copy it. **You only see it once.**
4. Save it somewhere safe temporarily

---

## Step 3 — Set up Google Cloud (for Sheets + Drive)

Two different auth mechanisms are used here, for a specific reason: a
service account can read/write an existing Google Sheet just fine, but it
**cannot create new files** (like a tailored resume PDF) in a personal
Google Drive — service accounts have no storage quota of their own, and
Google requires either a paid Workspace "Shared Drive" or real OAuth user
delegation instead (this was hit as a live 403 `storageQuotaExceeded` error
during setup, not a theoretical concern). So: **Sheets uses a service
account** (Step 3c), and **Drive uses OAuth as yourself** (Step 3e).

### 3a. Create a Google Cloud project

1. Go to https://console.cloud.google.com
2. Click the project dropdown at the top → **New Project**
3. Name it `job-screener` → **Create**
4. Make sure this project is selected in the dropdown

### 3b. Enable the required APIs

1. Go to **APIs & Services → Library**
2. Search for and enable each of these (click each → Enable):
   - **Google Sheets API**
   - **Google Drive API**

### 3c. Create a Service Account (for Sheets)

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

You'll need this in Step 4a (for the Sheet only — not the Drive folder).

### 3e. Create an OAuth Client (for Drive)

1. Still on **APIs & Services → Credentials**, click **+ Create Credentials → OAuth client ID**
2. If prompted, configure the **OAuth consent screen** first:
   - User type: **External**
   - Add the `drive.file` scope
   - **Publishing status: "In Production"** — not "Testing". Testing-status refresh tokens expire after 7 days, which would silently break the daily automation about a week in.
   - You (and anyone else who uses it) will see an "unverified app" warning when logging in — that's expected for a personal single-user tool; click **Advanced → Go to [app name] (unsafe)** to proceed. This doesn't require Google's formal app review since it's just for your own use.
3. Back on Create Credentials → OAuth client ID: Application type **Desktop app**, name it anything → **Create**
4. Copy the **Client ID** and **Client Secret** it shows you
5. Run the one-time local script to turn those into a refresh token:
   ```bash
   pip install google-auth-oauthlib
   python scripts/get_drive_refresh_token.py
   ```
   It'll ask for the Client ID/Secret, open a browser for you to log in and grant access, then print three values — `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, `GOOGLE_OAUTH_REFRESH_TOKEN` — save all three for Step 5.

---

## Step 4 — Create the Google Sheet and Drive folder

### 4a. Google Sheet (job results)

1. Go to https://sheets.google.com and create a new blank spreadsheet
2. Name it `Job Screener — Results`
3. Create a sheet tab named **Jobs** (rename Sheet1)
4. Share the sheet with your service account email from Step 3d (**Share** → paste email → **Editor** → **Send**)
5. Copy the **Sheet ID** from the URL:
   ```
   https://docs.google.com/spreadsheets/d/YOUR_SHEET_ID_IS_HERE/edit
   ```

### 4b. Drive folder (tailored resume PDFs)

1. Go to https://drive.google.com and create a new folder, e.g. `Job Screener — Tailored Resumes`
2. No sharing needed — the OAuth setup in Step 3e authenticates as you, so anything created in your own Drive is already yours.
3. Open the folder and copy its **Folder ID** from the URL:
   ```
   https://drive.google.com/drive/folders/YOUR_FOLDER_ID_IS_HERE
   ```

---

## Step 5 — Add secrets to GitHub

1. In your GitHub repo, go to **Settings → Secrets and variables → Actions**
2. Click **New repository secret** for each of the following:

| Secret Name | Value |
|---|---|
| `CLAUDE_CODE_OAUTH_TOKEN` | Your token from Step 2 |
| `GOOGLE_CREDENTIALS_JSON` | The entire contents of the `.json` file from Step 3c |
| `GOOGLE_SHEET_ID` | The Sheet ID from Step 4a |
| `GOOGLE_DRIVE_FOLDER_ID` | The Drive folder ID from Step 4b |
| `GOOGLE_OAUTH_CLIENT_ID` | From Step 3e |
| `GOOGLE_OAUTH_CLIENT_SECRET` | From Step 3e |
| `GOOGLE_OAUTH_REFRESH_TOKEN` | From Step 3e |

For `GOOGLE_CREDENTIALS_JSON`: open the downloaded `.json` file in a text editor,
select all, copy, paste as the secret value.

---

## Step 6 — Test the workflow manually

1. In your GitHub repo, go to **Actions**
2. Click **Daily Job Screener** in the left sidebar
3. Click **Run workflow → Run workflow** (green button)
4. Watch the logs — with ~350+ companies this takes longer than a small run; give it up to an hour
5. Check your Google Sheet for results, and your Drive folder for tailored resume PDFs on strong matches

---

## Step 7 — Activate the daily schedule

The workflow is already configured to run weekdays at 8:00 AM Eastern.
It activates automatically once you push to the `main` branch.

To confirm it's scheduled: go to **Actions → Daily Job Screener** and you'll see
the next scheduled run listed.

---

## How company discovery works

Unlike a hand-picked company list, `src/discover.py` pulls a fresh copy of the
public [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions)
dataset every run, and automatically finds every currently-active listing
hosted on Greenhouse, Lever, or Workday (the three ATS platforms this project
can pull full job descriptions from) — typically 300+ companies. This is
merged with a small hand-picked list in `config/companies_supplemental.json`
(AI-native and consulting companies the public feed doesn't reliably tag).

**You don't need to maintain a company list.** If you want to permanently
add a specific company regardless of what the feed picks up, add it to
`config/companies_supplemental.json` in the same `{"name", "ats", "id", "tier"}`
format (Workday entries also need a `"workday_url"` — the fully resolved CXS
API URL for that tenant).

---

## Understanding the Google Sheet columns

| Column | Description |
|---|---|
| Date Found | When the screener found this job |
| Score | AI match score 0–100 |
| Tier | Set only for curated supplemental companies (P1 AI/Agent, P2 Tech Consulting, P3 MBB Stretch) — blank for feed-discovered companies |
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
| Tailored Resume | Drive link to a tailored resume PDF — only populated for jobs scoring 65+ |
| Source | "New-Grad Feed" (auto-discovered) or "Curated" (from the supplemental list) |

There's no email alert step — check the Sheet directly whenever you want to apply.

---

## Customizing

**Add a permanent company regardless of the feed:**
Edit `config/companies_supplemental.json` (see "How company discovery works" above).

**Change which feed categories count:**
Edit `RELEVANT_CATEGORIES` in `src/discover.py`.

**Change keyword filters:**
Edit `config/filters.json` — add/remove title keywords or locations.

**Update your profile:**
Edit `config/candidate_profile.txt` as your experience grows.

**Update your resume template:**
Edit `resume/resume.tex` — every tailored resume is generated from this file, so keep it current.

**Change the tailored-resume score threshold:**
In `src/main.py`, change `TAILOR_SCORE_THRESHOLD = 65` to any value.

**Run on weekends too:**
In `.github/workflows/daily_screener.yml`, change `1-5` to `*` in the cron line.

---

## Estimated costs

- **GitHub Actions:** Free tier is 2,000 minutes/month; each run can now take significantly longer than before (~350+ companies vs. the original 26) — monitor your usage in **Settings → Billing**.
- **Claude API:** scales with daily volume — roughly $10–50/month depending on how many jobs clear the keyword filter and how many get tailored resumes (each tailored resume is a second Claude call).
- **Google APIs:** Free (well within free tier limits)

---

## Troubleshooting

**"No module named X"** → Check `requirements.txt` is in the root directory

**Sheet not updating** → Confirm the service account email has Editor access to the sheet

**No tailored resume PDFs / Drive links** → Confirm `GOOGLE_DRIVE_FOLDER_ID`, `GOOGLE_OAUTH_CLIENT_ID`, `GOOGLE_OAUTH_CLIENT_SECRET`, and `GOOGLE_OAUTH_REFRESH_TOKEN` are all set; check the Actions log for `pdflatex`/Drive upload errors. A sudden `invalid_grant` error usually means the OAuth consent screen was left in "Testing" status and the refresh token expired after 7 days — switch it to "In Production" and re-run `scripts/get_drive_refresh_token.py`.

**Very few or zero jobs found** → `src/discover.py` depends on the public SimplifyJobs feed being reachable; check the Actions log for a fetch warning

**Score always 0** → Check `CLAUDE_CODE_OAUTH_TOKEN` is set correctly in GitHub Secrets and hasn't expired (~1 year lifetime — re-run `claude setup-token` and update the secret if it has); also check the Actions log for a `claude CLI not found` error, which means the "Install Claude Code CLI" step failed

**Workflow times out** → With ~350+ companies this run is much bigger than before; the timeout is already raised to 90 minutes, but if you add many more companies to the supplemental list you may need to raise it further
