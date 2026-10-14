"""
risk_agent.py — Agent 4: Risk & Governance Specialist

TPD optimisations applied:
  - task="heavy"   → uses 120B (structured JSON + regulatory refs need quality)
  - max_tokens=2048 → 6-10 risk objects ≈ 1200 tokens; was 4096
  - SYSTEM_PROMPT is now static → prompt caching
  - analysis truncated to 4000 chars — saves ~500 input tokens per call
"""
import logging
from openai import OpenAI
from backend.config import cfg
from backend.groq_fallback import groq_call_with_fallback
from backend.utils.json_utils import safe_json_parse

log    = logging.getLogger(__name__)
client = OpenAI(**cfg.llm_client_kwargs())

SYSTEM_PROMPT = """You are a Chief Risk Officer with 20 years in financial services.

You will be given an analysis and asked to identify material risks.

Return ONLY a valid JSON array — no prose, no markdown fences.
Each risk object must have exactly:
  name           — short risk name (5-8 words)
  category       — one of: Regulatory | Operational | Technology | Strategic
  level          — one of: Critical | High | Medium | Low
  impact         — what goes wrong (1-2 sentences)
  mitigation     — specific actionable control (NOT generic like "implement controls")
  owner          — who owns it (e.g. CTO, CRO, CISO)
  regulatory_ref — specific regulation or "N/A" (e.g. "DORA Article 28", "SR 11-7")

Identify 6-10 risks. Base all risks on the provided analysis only.
"""

_VALID_LEVELS = {"Critical", "High", "Medium", "Low"}
_VALID_CATS   = {"Regulatory", "Operational", "Technology", "Strategic"}


def run(state: dict) -> dict:
    topic    = state.get("topic", "")
    industry = state.get("industry", "")
    analysis = state.get("analysis", "")
    sources  = state.get("sources", [])

    log.info("[risk] generating risks")

    if not analysis.strip():
        return {"risks": [], "current_agent": "risk_node"}

    source_titles = "\n".join(
        f"- {s.get('title', '')} ({s.get('url', '')})"
        for s in sources[:8]
    )

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",
         "content": (
             f"Topic: {topic}\nIndustry: {industry}\n\n"
             f"Analysis:\n{analysis[:4000]}\n\n"
             f"Sources:\n{source_titles}"
         )},
    ]

    try:
        raw_content = groq_call_with_fallback(
            client=client,
            messages=messages,
            primary_model=cfg.LLM_MODEL,
            temperature=cfg.LLM_TEMPERATURE,
            max_tokens=2048,
            task="heavy",
        )
    except RuntimeError as e:
        log.error(f"[risk] all models exhausted: {e}")
        return {"status": "error", "error": str(e), "current_agent": "risk_node"}
    except Exception as e:
        log.error(f"[risk] unexpected error: {e}")
        return {"status": "error", "error": f"Risk Agent error: {e}",
                "current_agent": "risk_node"}

    risks = safe_json_parse(raw_content, default=[], label="risk")

    # Guard: model returned a bare string (body-level error that slipped through)
    if isinstance(risks, str):
        log.error("[risk] got plain string instead of JSON array — returning empty: %r", risks[:120])
        return {"risks": [], "current_agent": "risk_node"}

    # Guard: model wrapped array in a dict {"risks": [...]}
    if isinstance(risks, dict):
        risks = risks.get("risks", list(risks.values())[0] if risks else [])

    if not isinstance(risks, list):
        log.error("[risk] unexpected top-level type %s — returning empty", type(risks))
        return {"risks": [], "current_agent": "risk_node"}

    for r in risks:
        if r.get("level")    not in _VALID_LEVELS: r["level"]    = "Medium"
        if r.get("category") not in _VALID_CATS:   r["category"] = "Operational"

    log.info(f"[risk] identified {len(risks)} risks")
    return {"risks": risks, "current_agent": "risk_node"}
