"""
backend/utils/json_utils.py — Shared JSON parsing utility for all agents.

WHY THIS EXISTS:
  Every agent that calls an LLM and expects JSON back needs the same
  defensive parsing logic. The old pattern — copy-pasting _strip_fences()
  into each agent — had two bugs:
    1. raw.startswith("```") misses fences preceded by a newline or space
       (happens on ~15% of Groq calls)
    2. No fallback: if stripping fails, json.loads() raises on raw markdown

  This module fixes both with three strategies tried in order.

IMPORT IN AGENTS:
  from backend.utils.json_utils import safe_json_parse

  # Replace:
  #   risks = json.loads(_strip_fences(raw_content))
  # With:
  #   risks = safe_json_parse(raw_content, default=[], label="risk")
"""

import json
import logging
import re
from typing import Any, TypeVar

log = logging.getLogger(__name__)

T = TypeVar("T")

# Matches ```json...``` or ```...``` with optional whitespace.
# DOTALL so . matches newlines inside the fence.
_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL | re.IGNORECASE)


def _extract_from_fence(text: str) -> str | None:
    """Return content inside the first ```...``` fence, or None."""
    m = _FENCE_RE.search(text)
    return m.group(1).strip() if m else None


def _extract_first_block(text: str) -> str | None:
    """
    Find the first complete {...} or [...] block in text.
    Handles nesting and quoted strings correctly.
    Used when the model wraps JSON in explanatory prose.
    """
    for open_ch, close_ch in [('{', '}'), ('[', ']')]:
        start = text.find(open_ch)
        if start == -1:
            continue
        depth, in_str, escape = 0, False, False
        for i, ch in enumerate(text[start:], start):
            if escape:
                escape = False
                continue
            if ch == '\\' and in_str:
                escape = True
                continue
            if ch == '"':
                in_str = not in_str
                continue
            if in_str:
                continue
            if ch == open_ch:
                depth += 1
            elif ch == close_ch:
                depth -= 1
                if depth == 0:
                    return text[start:i + 1]
    return None


def safe_json_parse(raw: str, default: T, label: str = "llm") -> T | Any:
    """
    Parse JSON from an LLM response robustly.

    Strategies tried in order:
      1. Direct json.loads()           — model behaved, no fences
      2. Extract from markdown fence   — ```json...``` anywhere in string
      3. Extract first {...} or [...]  — model added prose around JSON
      4. Return `default` and log      — all strategies failed

    Args:
        raw:     Raw string returned by the model.
        default: Value to return if all strategies fail.
        label:   Agent name used in log messages (e.g. "risk", "qa").

    Returns:
        Parsed Python object, or `default` on failure.

    Examples:
        risks = safe_json_parse(raw, default=[], label="risk")
        score = safe_json_parse(raw, default=None, label="qa")
    """
    if not raw or not raw.strip():
        log.warning("[%s] empty LLM response — returning default", label)
        return default

    # Strategy 1: direct parse
    try:
        return json.loads(raw)
    except json.JSONDecodeError:
        pass

    # Strategy 2: strip markdown fence
    fenced = _extract_from_fence(raw)
    if fenced:
        try:
            return json.loads(fenced)
        except json.JSONDecodeError:
            pass

    # Strategy 3: extract first JSON block from surrounding prose
    block = _extract_first_block(raw)
    if block:
        try:
            return json.loads(block)
        except json.JSONDecodeError:
            pass

    log.error(
        "[%s] JSON parse failed after all strategies — returning default. "
        "First 300 chars: %r",
        label, raw[:300],
    )
    return default
