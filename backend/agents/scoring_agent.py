"""
scoring_agent.py — Agent 5: Technology Investment Strategist

TPD optimisations applied:
  - task="medium"  → starts on gpt-oss-20b (scoring is structured, not creative)
  - max_tokens=1024 → 3-4 rows of scores ≈ 400 tokens; was 4096
  - SYSTEM_PROMPT is now static → prompt caching
  - weighted_total recalculated server-side (unchanged)
"""
import logging
from openai import OpenAI
from backend.config import cfg
from backend.groq_fallback import groq_call_with_fallback
from backend.utils.json_utils import safe_json_parse

log    = logging.getLogger(__name__)
client = OpenAI(**cfg.llm_client_kwargs())

# Static system prompt — topic/industry in user message for caching
SYSTEM_PROMPT = """You are a Technology Investment Strategist for financial institutions.

You will be given an analysis and key risks, and asked to score investment approaches.

Return ONLY a valid JSON array — no prose, no markdown fences.
Score 3-4 investment/architecture approaches. Each must have exactly (all scores 0-10):
  approach              — short approach name
  regulatory_compliance — score 0-10
  security_privacy      — score 0-10
  scalability           — score 0-10
  tco                   — score 0-10 (10 = lowest cost)
  implementation_risk   — score 0-10 (10 = lowest risk)
  vendor_maturity       — score 0-10
  weighted_total        — set to 0 (server recalculates this)
  verdict               — one of: Recommended | Conditional | Not Recommended

Regulatory compliance must be the deciding factor.
Do not recommend any approach scoring below 5 on regulatory_compliance.
"""

_WEIGHTS = {
    "regulatory_compliance": 0.25,
    "security_privacy":      0.20,
    "scalability":           0.15,
    "tco":                   0.15,
    "implementation_risk":   0.15,
    "vendor_maturity":       0.10,
}
_VALID_VERDICTS = {"Recommended", "Conditional", "Not Recommended"}


def run(state: dict) -> dict:
    topic    = state.get("topic", "")
    industry = state.get("industry", "")
    analysis = state.get("analysis", "")
    risks    = state.get("risks", [])

    log.info("[scoring] building decision matrix")

    top_risks = "\n".join(
        f"- [{r.get('level')}] {r.get('name')}: {r.get('mitigation', '')}"
        for r in risks[:5]
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        # Topic/industry in user message for caching
        {"role": "user",
         "content": (
             f"Topic: {topic}\nIndustry: {industry}\n\n"
             f"Analysis:\n{analysis[:3000]}\n\n"
             f"Key risks:\n{top_risks}"
         )},
    ]

    try:
        raw_content = groq_call_with_fallback(
            client=client,
            messages=messages,
            primary_model=cfg.LLM_MODEL,
            temperature=cfg.LLM_TEMPERATURE,
            max_tokens=1024,    # 3-4 score rows ≈ 400 tokens; was 4096
            task="medium",      # gpt-oss-20b sufficient for structured scoring
        )
    except RuntimeError as e:
        log.error(f"[scoring] all models exhausted: {e}")
        return {"status": "error", "error": str(e), "current_agent": "scoring_node"}
    except Exception as e:
        log.error(f"[scoring] unexpected error: {e}")
        return {"status": "error", "error": f"Scoring Agent error: {e}",
                "current_agent": "scoring_node"}

    matrix = safe_json_parse(raw_content, default=[], label="scoring")

    if isinstance(matrix, dict):
        for key in ("approaches", "matrix", "data", "results"):
            if key in matrix:
                matrix = matrix[key]
                break
        else:
            log.error("[scoring] unexpected dict response — returning empty matrix")
            matrix = []

    if not isinstance(matrix, list):
        log.error("[scoring] unexpected type %s — returning empty matrix", type(matrix))
        matrix = []

    for row in matrix:
        try:
            total = sum(row.get(k, 0) * w for k, w in _WEIGHTS.items())
            row["weighted_total"] = round(total, 2)
        except (TypeError, KeyError):
            row["weighted_total"] = 0.0

        if row.get("verdict") not in _VALID_VERDICTS:
            row["verdict"] = "Conditional"

    matrix.sort(key=lambda r: r.get("weighted_total", 0), reverse=True)

    log.info(f"[scoring] scored {len(matrix)} approaches")
    return {"decision_matrix": matrix, "current_agent": "scoring_node"}
