"""
scorer.py — uses Claude API to score each job against Yuquan's profile.
Returns a match score (0-100) and a brief reasoning string.
"""

import os
import json
import logging
import time
from pathlib import Path
import anthropic

logger = logging.getLogger(__name__)

_profile_path = Path(__file__).parent.parent / "config" / "candidate_profile.txt"
CANDIDATE_PROFILE = _profile_path.read_text()

client = anthropic.Anthropic(api_key=os.environ["ANTHROPIC_API_KEY"])

SYSTEM_PROMPT = """You are a recruiter evaluating job-candidate fit. 
You will be given a candidate profile and a job posting.
Respond ONLY with a valid JSON object — no markdown, no explanation outside the JSON.

Return exactly this structure:
{
  "score": <integer 0-100>,
  "match_reasons": ["<reason 1>", "<reason 2>", "<reason 3>"],
  "concerns": ["<concern 1>"],
  "summary": "<one sentence why this is or isn't a strong match>"
}

Scoring guide:
90-100: Exceptional match — role aligns with candidate's exact skills and experience level
75-89:  Strong match — most requirements met, minor gaps
60-74:  Moderate match — relevant background but notable gaps
40-59:  Weak match — some overlap but significant misalignment
0-39:   Poor match — fundamentally wrong role type or seniority level

Key factors for THIS candidate:
- Penalize heavily for: senior/manager/lead roles, roles requiring 3+ years experience
- Reward heavily for: agentic AI / LLM systems, enterprise software, Solutions Engineer, Forward Deploy
- Reward moderately for: life sciences domain, bilingual (Chinese/English), data analytics
- Neutral: general software engineering without AI/ML component
"""

def score_job(job: dict) -> dict:
    """
    Score a single job against the candidate profile.
    Returns the job dict enriched with score, match_reasons, concerns, summary.
    """
    user_message = f"""
CANDIDATE PROFILE:
{CANDIDATE_PROFILE}

---

JOB POSTING:
Company: {job['company']} (Tier {job['tier']})
Title: {job['title']}
Location: {job['location']}
Posted: {job['posted_at']}

Description:
{job.get('description', 'No description available.')}

---

Score this candidate-job fit. Remember: respond ONLY with the JSON object.
"""

    for attempt in range(3):
        try:
            response = client.messages.create(
                model="claude-sonnet-4-20250514",
                max_tokens=400,
                system=SYSTEM_PROMPT,
                messages=[{"role": "user", "content": user_message}]
            )
            raw = response.content[0].text.strip()

            # Strip accidental markdown fences
            if raw.startswith("```"):
                raw = raw.split("```")[1]
                if raw.startswith("json"):
                    raw = raw[4:]
            raw = raw.strip()

            result = json.loads(raw)
            job["score"]         = int(result.get("score", 0))
            job["match_reasons"] = result.get("match_reasons", [])
            job["concerns"]      = result.get("concerns", [])
            job["summary"]       = result.get("summary", "")
            return job

        except json.JSONDecodeError as e:
            logger.warning(f"JSON parse error on attempt {attempt+1} for {job['title']}: {e}")
            time.sleep(2)
        except anthropic.RateLimitError:
            logger.warning("Rate limited — sleeping 30s")
            time.sleep(30)
        except Exception as e:
            logger.warning(f"Scoring error on attempt {attempt+1} for {job['title']}: {e}")
            time.sleep(5)

    # Fallback if all attempts fail
    job["score"]         = 0
    job["match_reasons"] = []
    job["concerns"]      = ["Scoring failed — review manually"]
    job["summary"]       = "Could not score this posting."
    return job


def score_jobs_batch(jobs: list[dict], delay: float = 0.5) -> list[dict]:
    """Score a list of jobs. Adds a small delay between calls."""
    scored = []
    for i, job in enumerate(jobs):
        logger.info(f"Scoring {i+1}/{len(jobs)}: {job['company']} — {job['title']}")
        scored.append(score_job(job))
        time.sleep(delay)
    return scored
