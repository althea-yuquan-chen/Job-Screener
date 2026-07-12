"""
claude_code_client.py — runs prompts through the local Claude Code CLI in
headless mode (`claude -p`) instead of calling the Anthropic API directly.

This lets the screener draw on a Claude Pro/Max subscription's included
usage (via `claude setup-token` -> CLAUDE_CODE_OAUTH_TOKEN) instead of
metered pay-per-token API billing.

Each invocation of `claude -p` pays a fixed session-startup overhead even
with tools disabled (~450 input tokens observed, vs. ~9000+ if the default
system prompt / tool definitions are left enabled) -- so callers should
batch many items into one prompt rather than looping per-item. See
scorer.py / tailor.py for the batched call sites.
"""

import json
import logging
import subprocess

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "sonnet"
DEFAULT_TIMEOUT = 600  # seconds -- batched prompts covering 100+ jobs can run long
DEFAULT_MAX_BUDGET_USD = "5"


class ClaudeCodeError(RuntimeError):
    """Raised when the `claude` CLI can't be run or returns an unusable result."""


def run_prompt(prompt: str, system_prompt: str, json_schema: dict,
                model: str = DEFAULT_MODEL, timeout: int = DEFAULT_TIMEOUT):
    """
    Runs one headless Claude Code turn and returns the parsed structured
    output. The prompt is sent via stdin (not argv) so large batched
    payloads aren't subject to OS command-line length limits. Tools are
    disabled and the system prompt is fully replaced, since this call site
    never needs file/bash access -- everything it needs is in the prompt.
    """
    cmd = [
        "claude", "-p",
        "--output-format", "json",
        "--tools", "",
        "--system-prompt", system_prompt,
        "--json-schema", json.dumps(json_schema),
        "--no-session-persistence",
        "--max-budget-usd", DEFAULT_MAX_BUDGET_USD,
        "--model", model,
    ]

    try:
        proc = subprocess.run(
            cmd, input=prompt, capture_output=True, text=True, encoding="utf-8",
            timeout=timeout,
        )
    except FileNotFoundError as e:
        raise ClaudeCodeError(
            "claude CLI not found on PATH -- install @anthropic-ai/claude-code "
            "and ensure CLAUDE_CODE_OAUTH_TOKEN is set for unattended auth."
        ) from e
    except subprocess.TimeoutExpired as e:
        raise ClaudeCodeError(f"claude CLI timed out after {timeout}s") from e

    if proc.returncode != 0:
        raise ClaudeCodeError(
            f"claude CLI exited {proc.returncode}: {proc.stderr[-2000:] or proc.stdout[-2000:]}"
        )

    try:
        data = json.loads(proc.stdout)
    except json.JSONDecodeError as e:
        raise ClaudeCodeError(f"claude CLI returned non-JSON output: {proc.stdout[-2000:]}") from e

    if data.get("is_error"):
        raise ClaudeCodeError(f"claude CLI reported an error: {data.get('result')}")

    structured = data.get("structured_output")
    if structured is not None:
        return structured

    # Fallback: --json-schema should always populate structured_output, but
    # parse the raw result text too in case a future CLI version changes that.
    raw = (data.get("result") or "").strip()
    if raw.startswith("```"):
        raw = raw.split("```")[1]
        if raw.startswith("json"):
            raw = raw[4:]
    try:
        return json.loads(raw.strip())
    except json.JSONDecodeError as e:
        raise ClaudeCodeError(f"Could not parse claude CLI result as JSON: {raw[:2000]}") from e
