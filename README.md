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

## China pipeline (Nowcoder, manual run)

A second, parallel pipeline for Chinese 校招/实习转正 postings, scoped to **Data and
AI应用开发 (AI application/agent dev) roles only** — PM is worked by hand since
Nowcoder's PM coverage is thin/unstructured compared to its technical categories.

Unlike the US pipeline, there's no public feed that auto-discovers which companies to
target (no open "who uses this ATS" directory in China), so `config/companies_cn.json`
is a small hand-curated list of Nowcoder company IDs — same spirit as
`config/companies_supplemental.json`. It's also **not** wired into the daily GitHub
Actions cron — trigger it manually, either:

- from GitHub: Actions tab → "China Job Screener (Nowcoder)" → "Run workflow"
  (`.github/workflows/china_screener.yml`, `workflow_dispatch`-only, no schedule —
  uses the same repo Secrets as the US pipeline), or
- locally: `python src/main_cn.py` (only useful for testing the discover/filter/score
  logic — Sheets/Drive writes will no-op locally since GitHub Secrets are only ever
  injected as env vars inside an actual Actions run, never retrievable elsewhere).

One-time prerequisite: add a **"China Jobs"** tab to the same Google Sheet the US
pipeline already writes to (`sheets.py`'s tab name is parameterized, but it errors on
a non-existent tab rather than creating one).

Flow (`src/main_cn.py`): `discover_cn.py` calls Nowcoder's open, unauthenticated
per-company job API (`nowpick.nowcoder.com/u/company/job/list/v2` — confirmed live to
require no login, unlike Nowcoder's general search page or BOSS直聘/Moka/北森, which
are all effectively unscrapable) → `filter_cn.py` keeps only Data/AI-flavored postings
(by `career_job_name`, via plain substring match — `\b` word-boundary regex like
`filter.py` uses doesn't work on Chinese text) and dedupes against
`.seen_job_ids_cn.json` → `scorer_cn.py` scores survivors against
`config/candidate_profile_cn.txt` (China-market version — no visa/citizenship logic,
re-ranked toward Data/AI应用开发) via the same Claude Code CLI mechanism as the US
scorer → jobs scoring 75+ get the existing static `resume/resume_zh.pdf` attached (no
per-job Chinese tailoring yet) and are written to the **"China Jobs"** tab of the same
Google Sheet (`sheets.py`'s tab name is parameterized; the tab must already exist).

To add more companies: visit `https://www.nowcoder.com/enterprise/{id}` for a company
and copy the numeric id into `config/companies_cn.json`.

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
  main_cn.py                China pipeline orchestrator (manual run, see below)
  discover_cn.py             Nowcoder per-company job fetching
  filter_cn.py                category filter + dedup (Chinese-text-safe matching)
  scorer_cn.py                Claude-based scoring against the China-market profile
config/
  candidate_profile.txt        my background, used as the scoring rubric
  filters.json                  keyword/location pre-filter rules
  companies_supplemental.json   hand-picked companies outside the feed
  candidate_profile_cn.txt      China-market version of the scoring rubric
  filters_cn.json                Data/AI应用开发 category filter rules
  companies_cn.json              curated Nowcoder company-id list
resume/
  content_bank.json         all real experience/project entries (source of truth)
  resume_template.tex       LaTeX template with placeholder markers
  resume_zh.tex / resume_zh.pdf   static Chinese resume (XeLaTeX + ctex)
.github/workflows/daily_screener.yml   the scheduled Action (US pipeline only)
```

## Setup

See [SETUP.md](SETUP.md) for the full one-time setup walkthrough (GitHub, Google Cloud, Claude Code token, GitHub Secrets, first manual run).

## Status

Schedule is enabled — runs daily at 3:00 AM ET via `.github/workflows/daily_screener.yml`. Requires a valid `CLAUDE_CODE_OAUTH_TOKEN` and the Google secrets from `SETUP.md` to be set in the repo, or scheduled runs will fail.
