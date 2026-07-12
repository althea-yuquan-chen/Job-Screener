"""
tailor.py — generates a per-job tailored resume PDF for high-scoring matches.

v3: Claude may now REWORD bullets (not just select/reorder them) to match a
job's vocabulary and emphasis — e.g. the same BASF work can honestly be
framed as "automated business intelligence workflow" for a data-analyst JD
or "production LLM agent pipeline" for an FDE JD. To keep this safe, every
number, percentage, quantified outcome, and named tool/technology/company in
the original bullet is mechanically verified to still appear in the
reworded version; any bullet that fails this check falls back to its
original verbatim text rather than being used as-is. Claude is also told
never to inflate scope/seniority (e.g. "supported" -> "led"), though that
part relies on the prompt rather than a mechanical check, since scope
inflation isn't reliably detectable by string matching.

Content selection: the two fixed work-experience entries and the one
leadership entry are always included (bullets reordered/reworded only);
the "Selected Projects" section is chosen by Claude from the real,
pre-written project candidates in resume/content_bank.json.
"""

import json
import logging
import re
import shutil
import subprocess
from pathlib import Path

from claude_code_client import run_prompt, ClaudeCodeError

logger = logging.getLogger(__name__)

RESUME_DIR = Path(__file__).parent.parent / "resume"
TEMPLATE_PATH = RESUME_DIR / "resume_template.tex"
CONTENT_BANK_PATH = RESUME_DIR / "content_bank.json"
TAILORED_DIR = RESUME_DIR / "tailored"
DEFAULT_TEX_PATH = RESUME_DIR / "resume.tex"

NUM_PROJECTS_TO_SELECT = 2  # matches the page budget the template was designed for
CHUNK_SIZE = 15  # jobs per Claude Code call -- see claude_code_client.py for why batching matters

SYSTEM_PROMPT = """You tailor a resume's WORDING, EMPHASIS, and CONTENT SELECTION to a job \
description — while keeping every underlying fact identical.

You will be given one shared CONTENT BANK (fixed work-experience/leadership bullets and a set of
candidate projects) and a list of JOBS. Produce one tailoring selection per job, identified by
"index", reusing the same content bank for all of them.

Rules:
- You MAY reword bullets to match each job's vocabulary/emphasis (e.g. describe the same work as
  "AI agent pipeline" vs. "automated workflow" depending on audience).
- You must NEVER change, generalize, omit, or invent any number, percentage, quantified outcome,
  named tool/technology, company name, or other concrete fact. Every such detail from the original
  bullet must still appear, unchanged, in your reworded version.
- You must NEVER inflate scope or seniority (e.g. turning "supported" into "led", or "contributed
  to" into "owned") beyond what the original states.
- For each job's Selected Projects, choose exactly 2 project ids from the ones provided, best-fit
  first.
- You may include 0 or more coursework skill ids per job ONLY if genuinely relevant to that job —
  never to pad the resume.

Every job in the input list must appear exactly once in "results", identified by its "index".
"""

_BULLET_ITEM_SCHEMA = {
    "type": "object",
    "properties": {
        "index": {"type": "integer"},
        "text": {"type": "string"},
    },
    "required": ["index", "text"],
    "additionalProperties": False,
}

_SELECTION_SCHEMA = {
    "type": "object",
    "properties": {
        "index": {"type": "integer"},
        "whelix": {"type": "array", "items": _BULLET_ITEM_SCHEMA},
        "basf": {"type": "array", "items": _BULLET_ITEM_SCHEMA},
        "media_center": {"type": "array", "items": _BULLET_ITEM_SCHEMA},
        "selected_projects": {"type": "array", "items": {"type": "string"}},
        "project_texts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "project_id": {"type": "string"},
                    "bullets": {"type": "array", "items": _BULLET_ITEM_SCHEMA},
                },
                "required": ["project_id", "bullets"],
                "additionalProperties": False,
            },
        },
        "include_coursework_skills": {"type": "array", "items": {"type": "string"}},
    },
    "required": [
        "index", "whelix", "basf", "media_center",
        "selected_projects", "project_texts", "include_coursework_skills",
    ],
    "additionalProperties": False,
}

RESULT_SCHEMA = {
    "type": "object",
    "properties": {"results": {"type": "array", "items": _SELECTION_SCHEMA}},
    "required": ["results"],
    "additionalProperties": False,
}

_DIGIT_RUN_RE = re.compile(r"\d+")
_ACRONYM_RE = re.compile(r"\b[A-Z]{2,}\b")


def _load_bank() -> dict:
    return json.loads(CONTENT_BANK_PATH.read_text(encoding="utf-8"))


def _extract_facts(bullet: str) -> tuple[set[str], set[str]]:
    """Returns (digit_runs, acronyms) that must survive any rewording."""
    no_commas = bullet.replace(",", "")
    digits = set(_DIGIT_RUN_RE.findall(no_commas))
    acronyms = {m.lower() for m in _ACRONYM_RE.findall(bullet)}
    return digits, acronyms


def _facts_preserved(original: str, reworded: str) -> bool:
    digits, acronyms = _extract_facts(original)
    reworded_no_commas = reworded.replace(",", "")
    reworded_lower = reworded.lower()
    return all(d in reworded_no_commas for d in digits) and all(a in reworded_lower for a in acronyms)


def _process_bullets(original: list[str], items, label: str) -> list[str]:
    """
    Validates Claude's proposed {index, text} list against the original
    bullets for a section: indices must be a permutation of the original
    set, and each proposed text must pass the fact-preservation check —
    otherwise that bullet (or the whole section, if the shape is invalid)
    falls back to the original verbatim text.
    """
    n = len(original)
    if not (isinstance(items, list) and len(items) == n):
        logger.warning(f"Invalid/missing bullet list for {label} — using original order/text.")
        return list(original)

    indices = [it.get("index") if isinstance(it, dict) else None for it in items]
    if sorted(i for i in indices if isinstance(i, int)) != list(range(n)):
        logger.warning(f"Invalid indices for {label} — using original order/text.")
        return list(original)

    result = []
    for it, idx in zip(items, indices):
        original_text = original[idx]
        proposed = it.get("text") if isinstance(it, dict) else None
        if isinstance(proposed, str) and proposed.strip() and _facts_preserved(original_text, proposed):
            result.append(proposed)
        else:
            if isinstance(proposed, str) and proposed.strip() and proposed.strip() != original_text.strip():
                logger.warning(f"Reworded bullet #{idx} in {label} failed fact-check — using original text.")
            result.append(original_text)
    return result


def _build_bank_payload(bank: dict) -> dict:
    exp = bank["experience"]
    projects = bank["projects"]
    leadership = bank["leadership"]["media_center"]
    return {
        "whelix_bullets": exp["whelix"]["bullets"],
        "basf_bullets": exp["basf"]["bullets"],
        "media_center_bullets": leadership["bullets"],
        "available_projects": {
            pid: {"title": p["title"], "bullets": p["bullets"], "tags": p.get("tags", [])}
            for pid, p in projects.items()
        },
        "available_coursework": bank["coursework_only_skills"],
    }


def _get_selections_batch(jobs: list[dict], bank: dict) -> dict[int, dict]:
    """
    Gets a tailoring selection for each job in one Claude Code call, reusing
    the same content-bank payload for all of them (only the job list varies).
    Returns {index: selection_dict}, with project_texts converted from the
    schema's array-of-{project_id, bullets} shape back into a dict keyed by
    project_id, matching what compose_tex() expects. Missing/failed indices
    are simply absent from the returned dict -- callers fall back per-job.
    """
    payload = {
        "content_bank": _build_bank_payload(bank),
        "jobs": [
            {
                "index": i,
                "company": job.get("company"),
                "title": job.get("title"),
                "description": job.get("description", "No description available."),
            }
            for i, job in enumerate(jobs)
        ],
    }

    try:
        data = run_prompt(json.dumps(payload, indent=2), SYSTEM_PROMPT, RESULT_SCHEMA)
    except ClaudeCodeError as e:
        logger.warning(f"Batch resume tailoring for {len(jobs)} jobs failed: {e}")
        return {}

    selections = {}
    for result in data.get("results", []):
        idx = result.get("index")
        if not isinstance(idx, int):
            continue
        result = dict(result)
        result["project_texts"] = {
            pt["project_id"]: pt["bullets"] for pt in result.get("project_texts", [])
        }
        selections[idx] = result
    return selections


def _validate_projects(selected, valid_ids: set) -> list[str]:
    if isinstance(selected, list):
        clean = [p for p in dict.fromkeys(selected) if p in valid_ids]
        if clean:
            return clean[:NUM_PROJECTS_TO_SELECT]
    logger.warning("Invalid/missing project selection — falling back to default.")
    return []


def _render_bullets(texts: list[str]) -> str:
    return "\n".join(f"  \\item {t}" for t in texts) + "\n"


def compose_tex(bank: dict, selection: dict) -> str:
    """Builds the full .tex from a content bank + a (validated) selection dict."""
    exp = bank["experience"]
    projects = bank["projects"]
    leadership = bank["leadership"]["media_center"]
    coursework = bank["coursework_only_skills"]

    whelix, basf = exp["whelix"], exp["basf"]
    whelix_texts = _process_bullets(whelix["bullets"], selection.get("whelix"), "whelix")
    basf_texts = _process_bullets(basf["bullets"], selection.get("basf"), "basf")
    media_texts = _process_bullets(leadership["bullets"], selection.get("media_center"), "media_center")

    valid_ids = set(projects.keys())
    selected_projects = _validate_projects(selection.get("selected_projects"), valid_ids)
    if not selected_projects:
        selected_projects = bank["default_selection"]["selected_projects"]

    work_experience_tex = (
        f"\\jobheader{{{whelix['company']}}}{{{whelix['title']}}}{{{whelix['dates']}}}{{{whelix['location']}}}\n"
        f"\\begin{{rbullet}}\n{_render_bullets(whelix_texts)}\\end{{rbullet}}\n\n"
        f"\\jobheader{{{basf['company']}}}{{{basf['title']}}}{{{basf['dates']}}}{{{basf['location']}}}\n"
        f"\\begin{{rbullet}}\n{_render_bullets(basf_texts)}\\end{{rbullet}}\n"
    )

    project_texts_by_id = selection.get("project_texts") or {}
    projects_tex_parts = []
    for pid in selected_projects:
        p = projects[pid]
        texts = _process_bullets(p["bullets"], project_texts_by_id.get(pid), pid)
        projects_tex_parts.append(
            f"\\projheader{{{p['title']}}}{{{p['dates']}}}\n"
            f"\\begin{{rbullet}}\n{_render_bullets(texts)}\\end{{rbullet}}\n"
        )
    projects_tex = "\n".join(projects_tex_parts)

    leadership_tex = (
        f"\\projheader{{{leadership['title']}}}{{{leadership['dates']}}}\n"
        f"\\begin{{rbullet}}\n{_render_bullets(media_texts)}\\end{{rbullet}}\n"
    )

    coursework_ids = [c for c in (selection.get("include_coursework_skills") or []) if c in coursework]
    coursework_line = ""
    if coursework_ids:
        labels = ", ".join(coursework[c] for c in coursework_ids)
        coursework_line = f"\\\\[1pt]\n\\textbf{{Coursework:}} {labels}"

    template = TEMPLATE_PATH.read_text(encoding="utf-8")
    tex = template.replace("%%WORK_EXPERIENCE%%", work_experience_tex)
    tex = tex.replace("%%PROJECTS%%", projects_tex)
    tex = tex.replace("%%LEADERSHIP%%", leadership_tex)
    tex = tex.replace("%%COURSEWORK_LINE%%", coursework_line)
    return tex


def _resolve_pdflatex() -> str:
    found = shutil.which("pdflatex")
    if found:
        return found
    # Fallback for local Windows dev boxes where MiKTeX was just installed and
    # the current process's PATH hasn't picked up the registry change yet.
    candidate = Path.home() / "AppData/Local/Programs/MiKTeX/miktex/bin/x64/pdflatex.exe"
    if candidate.exists():
        return str(candidate)
    raise RuntimeError("pdflatex not found on PATH or in the usual MiKTeX install location.")


_PAGE_COUNT_RE = re.compile(r"Output written on \S+\.pdf \((\d+) page")


def _compact_tex(tex_content: str) -> str:
    """
    Content selection + rewording varies length per job, so unlike the old
    fixed-content resume, tailored variants can occasionally run long. This
    buys back space: smaller bullet text + tighter margins, applied only as
    a retry when the normal compile overflows to a second page.
    """
    tex_content = re.sub(
        r"\\usepackage\[left=[^\]]+\]\{geometry\}",
        r"\\usepackage[left=0.5in, right=0.5in, top=0.12in, bottom=0.12in]{geometry}",
        tex_content,
    )
    tex_content = re.sub(
        r"\\begin\{rbullet\}(.*?)\\end\{rbullet\}",
        lambda m: "{\\small\\begin{rbullet}" + m.group(1) + "\\end{rbullet}}",
        tex_content,
        flags=re.DOTALL,
    )
    return tex_content


def _run_pdflatex(pdflatex: str, tex_path: Path, out_dir: Path) -> str:
    stdout = ""
    for _ in range(2):  # 2 passes — hyperref/rerunfilecheck wants it for stable output
        result = subprocess.run(
            [pdflatex, "-interaction=nonstopmode", "-halt-on-error", tex_path.name],
            cwd=str(out_dir),
            capture_output=True,
            text=True,
            timeout=60,
        )
        if result.returncode != 0:
            raise RuntimeError(f"pdflatex failed for {tex_path.stem}: {result.stdout[-2000:]}")
        stdout = result.stdout
    return stdout


def _compile_pdf(tex_content: str, out_dir: Path, stem: str) -> Path:
    out_dir.mkdir(parents=True, exist_ok=True)
    tex_path = out_dir / f"{stem}.tex"
    pdf_path = out_dir / f"{stem}.pdf"
    pdflatex = _resolve_pdflatex()

    tex_path.write_text(tex_content, encoding="utf-8")
    stdout = _run_pdflatex(pdflatex, tex_path, out_dir)

    page_match = _PAGE_COUNT_RE.search(stdout)
    pages = int(page_match.group(1)) if page_match else 1
    if pages > 1:
        logger.info(f"{stem} compiled to {pages} pages — retrying with compacted spacing.")
        tex_path.write_text(_compact_tex(tex_content), encoding="utf-8")
        stdout = _run_pdflatex(pdflatex, tex_path, out_dir)
        page_match = _PAGE_COUNT_RE.search(stdout)
        pages = int(page_match.group(1)) if page_match else pages
        if pages > 1:
            logger.warning(f"{stem} still {pages} pages after compacting — using it anyway.")

    if not pdf_path.exists():
        raise RuntimeError(f"pdflatex reported success but no PDF was produced for {stem}")
    return pdf_path


def _safe_stem(job: dict) -> str:
    raw = f"{job.get('company', 'company')}_{job.get('job_id', 'job')}"
    return re.sub(r"[^A-Za-z0-9_-]+", "_", raw)[:80]


def tailor_resumes(jobs: list[dict], chunk_size: int = CHUNK_SIZE) -> dict[int, Path | None]:
    """
    Generates tailored resume PDFs for a list of jobs, batching the Claude
    Code selection calls in chunks of `chunk_size` (PDF composition/
    compilation is local and still happens once per job). Returns
    {index into `jobs`: PDF path or None}; None means tailoring/compiling
    failed for that job (caller should treat as "no resume available"
    rather than fail the whole run).
    """
    bank = _load_bank()
    results: dict[int, Path | None] = {}

    for start in range(0, len(jobs), chunk_size):
        chunk = jobs[start:start + chunk_size]
        logger.info(
            f"Tailoring resumes {start + 1}-{start + len(chunk)} of {len(jobs)} "
            f"(chunk of {len(chunk)})..."
        )
        selections = _get_selections_batch(chunk, bank)
        for i, job in enumerate(chunk):
            global_idx = start + i
            selection = selections.get(i)
            if selection is None:
                logger.warning(
                    f"No tailoring selection for {job.get('company')} — {job.get('title')}"
                )
                results[global_idx] = None
                continue
            try:
                tex = compose_tex(bank, selection)
                stem = _safe_stem(job)
                results[global_idx] = _compile_pdf(tex, TAILORED_DIR, stem)
            except Exception as e:
                logger.warning(
                    f"Resume compile failed for {job.get('company')} — {job.get('title')}: {e}"
                )
                results[global_idx] = None

    return results


def render_default_resume() -> Path:
    """
    Regenerates the general-purpose base resume.tex/.pdf from the content
    bank's default selection. Run this (python src/tailor.py) after editing
    content_bank.json so the base resume stays in sync.
    """
    bank = _load_bank()
    tex = compose_tex(bank, bank["default_selection"])
    DEFAULT_TEX_PATH.write_text(tex, encoding="utf-8")
    return _compile_pdf(tex, RESUME_DIR, "resume")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    path = render_default_resume()
    print(f"Default resume regenerated: {path}")
