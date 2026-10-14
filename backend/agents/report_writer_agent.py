"""
report_writer_agent.py — Agent 6: Senior Management Consultant

TPD optimisations applied:
  - task="heavy"   → uses 120B (full report needs best quality)
  - max_tokens=4096 → report is the longest output; kept at 4096
  - SYSTEM_PROMPT is now static → prompt caching
  - source_list capped at 15 sources (was all) — LLM ignores anything beyond
    ~10 anyway; saves input tokens on large source lists
"""
import logging
from openai import OpenAI
from backend.config import cfg
from backend.groq_fallback import groq_call_with_fallback
from backend.mcp_server.tools import write_report, save_decision_matrix
from backend.mcp_server.exceptions import FileWriteError

log    = logging.getLogger(__name__)
client = OpenAI(**cfg.llm_client_kwargs())

# Static system prompt — topic/industry in user message for caching
SYSTEM_PROMPT = """You are a Senior Management Consultant writing for C-suite audiences
at financial institutions.

You will be given an analysis, risk register, decision matrix, and sources.
Write a client-ready executive report using ONLY the provided content.

Use these exact Markdown headings:
## Executive Summary
(≤200 words — state top recommendation, expected outcome, biggest risk)
## Key Findings
(3-5 findings, each with source reference)
## Risk Register
(use the pre-formatted table provided — do not reformat it)
## Decision Matrix
(use the pre-formatted table provided — do not reformat it)
## Recommendations
(3-5 numbered, each with: action, rationale, owner, 90-day first step)
## Sources
(numbered list)

Every factual claim must reference a source. No vague language.
"""


def _risk_table(risks: list) -> str:
    if not risks:
        return "_No risks identified._"
    rows = [
        "| Risk | Category | Level | Mitigation | Owner | Regulatory Ref |",
        "|------|----------|-------|-----------|-------|----------------|",
    ]
    for r in risks:
        rows.append(
            f"| {r.get('name', '')} | {r.get('category', '')} | {r.get('level', '')} "
            f"| {r.get('mitigation', '')} | {r.get('owner', '')} | {r.get('regulatory_ref', '')} |"
        )
    return "\n".join(rows)


def _matrix_table(matrix: list) -> str:
    if not matrix:
        return "_No approaches scored._"
    rows = [
        "| Approach | Compliance | Security | Scale | TCO | Risk | Vendor | Total | Verdict |",
        "|---------|-----------|---------|-------|-----|------|--------|-------|---------|",
    ]
    for r in matrix:
        rows.append(
            f"| {r.get('approach', '')} | {r.get('regulatory_compliance', 0)} "
            f"| {r.get('security_privacy', 0)} | {r.get('scalability', 0)} "
            f"| {r.get('tco', 0)} | {r.get('implementation_risk', 0)} "
            f"| {r.get('vendor_maturity', 0)} | **{r.get('weighted_total', 0)}** "
            f"| {r.get('verdict', '')} |"
        )
    return "\n".join(rows)


def run(state: dict) -> dict:
    topic    = state.get("topic", "")
    industry = state.get("industry", "")
    run_id   = state.get("run_id", "unknown")
    sources  = state.get("sources", [])

    log.info(f"[report] compiling report for run {run_id}")

    # Cap at 15 sources — LLM ignores beyond ~10; saves input tokens
    source_list = "\n".join(
        f"{i + 1}. [{s.get('title', '')}]({s.get('url', '')})"
        for i, s in enumerate(sources[:15])
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        # Topic/industry + full content in user message
        {"role": "user", "content": (
            f"Topic: {topic}\nIndustry: {industry}\n\n"
            f"Analysis:\n{state.get('analysis', '')[:4000]}\n\n"
            f"Risk Register:\n{_risk_table(state.get('risks', []))}\n\n"
            f"Decision Matrix:\n{_matrix_table(state.get('decision_matrix', []))}\n\n"
            f"Sources:\n{source_list}"
        )},
    ]

    try:
        report = groq_call_with_fallback(
            client=client,
            messages=messages,
            primary_model=cfg.LLM_MODEL,
            temperature=cfg.LLM_TEMPERATURE,
            max_tokens=4096,    # full report; kept at 4096
            task="heavy",       # needs 120B quality for client-facing output
        )
    except RuntimeError as e:
        log.error(f"[report] all models exhausted: {e}")
        return {"status": "error", "error": str(e), "current_agent": "report_node"}
    except Exception as e:
        log.error(f"[report] unexpected error: {e}")
        return {"status": "error", "error": f"Report Writer error: {e}",
                "current_agent": "report_node"}

    try:
        report_path = write_report(report, run_id, topic)
    except FileWriteError as e:
        return {"status": "error", "error": str(e), "current_agent": "report_node"}

    matrix_path = ""
    if state.get("decision_matrix"):
        try:
            matrix_path = save_decision_matrix(state["decision_matrix"], run_id, topic)
        except FileWriteError as e:
            log.warning(f"[report] matrix save failed (non-fatal): {e}")

    log.info(f"[report] written to {report_path}")
    return {
        "report":        report,
        "report_path":   report_path,
        "matrix_path":   matrix_path,
        "current_agent": "report_node",
    }
