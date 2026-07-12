"""
scorer.py — uses Claude (via the local Claude Code CLI, headless mode) to
score each job against Yuquan's profile. Returns a match score (0-100) and
a brief reasoning string per job.

Runs through claude_code_client instead of the anthropic API SDK, so scoring
draws on a Claude subscription's included usage rather than metered API
billing. Jobs are scored in chunks (not one Claude Code invocation per job)
because every invocation pays a fixed session-startup overhead -- batching
amortizes that cost across many jobs instead of paying it per job.
"""

import logging
from pathlib import Path

from claude_code_client import run_prompt, ClaudeCodeError

logger = logging.getLogger(__name__)

_profile_path = Path(__file__).parent.parent / "config" / "candidate_profile.txt"
CANDIDATE_PROFILE = _profile_path.read_text()

CHUNK_SIZE = 40  # jobs per Claude Code call -- keeps prompt/response size manageable
MAX_DESCRIPTION_CHARS = 3000  # per-job description truncation for the batch prompt only

SYSTEM_PROMPT = """You are a recruiter evaluating job-candidate fit.
You will be given a candidate profile and a list of job postings.
Score every job in the list and return a JSON object matching the given schema.

Scoring guide:
90-100: Exceptional match — role aligns with candidate's exact skills and experience level
75-89:  Strong match — most requirements met, minor gaps
60-74:  Moderate match — relevant background but notable gaps
40-59:  Weak match — some overlap but significant misalignment
0-39:   Poor match — fundamentally wrong role type or seniority level

Key factors for THIS candidate:
- Penalize heavily for: senior/manager/lead roles, roles requiring 3+ years experience
- Penalize heavily for: roles requiring US citizenship, active security clearance, or stating
  "no visa sponsorship" — candidate is on F-1/STEM OPT and will need H-1B sponsorship
- Reward highest (90+): Forward Deployed Engineer, Agent Engineer, AI Implementation Engineer,
  Solutions Engineer roles at AI-native companies — building/deploying agentic or LLM systems
  directly with customers
- Reward strongly (75-89): AI/tech-forward consulting (e.g. BCG X, Slalom-type technical
  consulting) and Technical Consultant roles that combine engineering with client-facing work
- Reward moderately (60-74): Data Scientist/Analyst and general Decision Analytics roles —
  candidate has the technical background but recent experience is more applied-engineering
  than research-focused
- Reward moderately: life sciences domain, bilingual (Chinese/English)
- Reward lightly, do not over-index: generic Product Manager or Business Analyst roles with
  no clear technical/AI component — candidate has no traditional PM experience
- Neutral: general software engineering without AI/ML component

Every job in the input list must appear exactly once in "results", identified by its "index".
"""

RESULT_SCHEMA = {
    "type": "object",
    "properties": {
        "results": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "index": {"type": "integer"},
                    "score": {"type": "integer"},
                    "match_reasons": {"type": "array", "items": {"type": "string"}},
                    "concerns": {"type": "array", "items": {"type": "string"}},
                    "summary": {"type": "string"},
                },
                "required": ["index", "score", "match_reasons", "concerns", "summary"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["results"],
    "additionalProperties": False,
}


def _build_prompt(jobs: list[dict]) -> str:
    postings = []
    for i, job in enumerate(jobs):
        description = job.get("description", "No description available.")
        if len(description) > MAX_DESCRIPTION_CHARS:
            description = description[:MAX_DESCRIPTION_CHARS] + "... [truncated]"
        postings.append(
            f"[{i}] Company: {job['company']} (Tier {job['tier']})\n"
            f"Title: {job['title']}\n"
            f"Location: {job['location']}\n"
            f"Posted: {job['posted_at']}\n"
            f"Description: {description}"
        )
    jobs_block = "\n\n---\n\n".join(postings)
    return (
        f"CANDIDATE PROFILE:\n{CANDIDATE_PROFILE}\n\n"
        f"===\n\nJOB POSTINGS ({len(jobs)} total):\n\n{jobs_block}"
    )


def _score_chunk(jobs: list[dict]) -> None:
    """Scores one chunk of jobs, writing score/match_reasons/concerns/summary
    onto each job dict in place. Falls back to score=0 for the whole chunk
    on any failure (missing CLI, timeout, malformed response, etc.)."""
    try:
        data = run_prompt(_build_prompt(jobs), SYSTEM_PROMPT, RESULT_SCHEMA)
        results_by_index = {r["index"]: r for r in data.get("results", [])}
    except ClaudeCodeError as e:
        logger.warning(f"Scoring chunk of {len(jobs)} jobs failed: {e}")
        results_by_index = {}

    for i, job in enumerate(jobs):
        result = results_by_index.get(i)
        if result is None:
            job["score"] = 0
            job["match_reasons"] = []
            job["concerns"] = ["Scoring failed — review manually"]
            job["summary"] = "Could not score this posting."
        else:
            job["score"] = int(result.get("score", 0))
            job["match_reasons"] = result.get("match_reasons", [])
            job["concerns"] = result.get("concerns", [])
            job["summary"] = result.get("summary", "")


def score_jobs_batch(jobs: list[dict], chunk_size: int = CHUNK_SIZE) -> list[dict]:
    """Scores a list of jobs against the candidate profile, chunking into
    groups of `chunk_size` per Claude Code invocation."""
    for start in range(0, len(jobs), chunk_size):
        chunk = jobs[start:start + chunk_size]
        logger.info(
            f"Scoring jobs {start + 1}-{start + len(chunk)} of {len(jobs)} "
            f"(chunk of {len(chunk)})..."
        )
        _score_chunk(chunk)
    return jobs
