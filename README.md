# Job Screener

An automated daily pipeline that finds new-grad job postings, scores each one against my profile with Claude, and generates a tailored resume for every strong match — built for my own US full-time job search.

Runs on a GitHub Actions schedule (every day, 3:00 AM ET) with no ongoing API cost: scoring and resume tailoring go through the Claude Code CLI against a Claude Pro/Max subscription's included usage, not metered billing.

## What it does

Each run (`src/main.py`):

1. **Discover companies** (`src/discover.py`) — pulls the public [SimplifyJobs/New-Grad-Positions](https://github.com/SimplifyJobs/New-Grad-Positions) feed fresh, auto-resolves every company currently posting on Greenhouse, Lever, or Workday (~300+), and merges in a small hand-picked list (`config/companies_supplemental.json`) of AI-native and consulting companies the feed doesn't reliably tag. No static company list to maintain.
2. **Fetch** (`src/fetcher.py`) — pulls live listings from all companies in parallel.
3. **Filter** (`src/filter.py`) — keyword pre-filter (`config/filters.json`) on title/location, then dedupe against previously-seen job IDs (`.seen_job_ids.json`).
4. **Enrich** — Workday's list endpoint doesn't include description text, so full JDs are fetched per-job, only for jobs that survive filtering.
5. **Score** (`src/scorer.py`) — each surviving job is scored 0–100 against `config/candidate_profile.txt` via Claude.
6. **Tailor** (`src/tailor.py`) — for jobs scoring 75+: Claude selects the best-fitting project entries from `resume/content_bank.json` and rewords bullets to match the JD's vocabulary, then compiles a tailored resume PDF (LaTeX). A verification step mechanically checks every number/tool/acronym from the original bullet survives the reworded version, falling back to the verbatim original on any mismatch — no fabricated content.
7. **Deliver** (`src/drive.py`, `src/sheets.py`) — tailored resumes upload to a Google Drive folder; every scored job (with a resume link, if applicable) is appended to a Google Sheet.

No email step — I check the Sheet directly and apply continuously, since for an OPT candidate this is a volume game rather than a small hand-picked shortlist.

## Project layout

```
src/
  main.py                  orchestrator — runs the full pipeline
  discover.py               dynamic company discovery
  fetcher.py                parallel ATS fetching (Greenhouse/Lever/Workday)
  filter.py                 keyword filter + dedup
  scorer.py                 Claude-based job scoring
  tailor.py                 resume selection, rewording, PDF generation
  drive.py / sheets.py      Google Drive/Sheets delivery
  claude_code_client.py     wraps the Claude Code CLI in headless mode
config/
  candidate_profile.txt        my background, used as the scoring rubric
  filters.json                  keyword/location pre-filter rules
  companies_supplemental.json   hand-picked companies outside the feed
resume/
  content_bank.json         all real experience/project entries (source of truth)
  resume_template.tex       LaTeX template with placeholder markers
.github/workflows/daily_screener.yml   the scheduled Action
```

## Setup

See [SETUP.md](SETUP.md) for the full one-time setup walkthrough (GitHub, Google Cloud, Claude Code token, GitHub Secrets, first manual run).

## Status

Schedule is enabled — runs daily at 3:00 AM ET via `.github/workflows/daily_screener.yml`. Requires a valid `CLAUDE_CODE_OAUTH_TOKEN` and the Google secrets from `SETUP.md` to be set in the repo, or scheduled runs will fail.
