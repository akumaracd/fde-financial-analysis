"""
analysis_agent.py — Agent 3: Principal Enterprise Architect

TPD optimisations applied:
  - task="heavy"   → uses 120B (quality matters here)
  - max_tokens=2048 → 600-word output ≈ 800 tokens; was 4096 (wasted ~3200 tokens)
  - SYSTEM_PROMPT is now static (topic/industry in user message) → prompt caching
  - extracted_content truncated to 8000 chars (was 12000) — quality doesn't improve
    meaningfully beyond ~2000 tokens of input; saves ~1000 input tokens per call
  - QA feedback injected into user message on re-entry so analyst can self-correct
"""
import logging
from openai import OpenAI
from backend.config import cfg
from backend.groq_fallback import groq_call_with_fallback

log    = logging.getLogger(__name__)
client = OpenAI(**cfg.llm_client_kwargs())

# Static system prompt — topic/industry injected in user message for cacheability
SYSTEM_PROMPT = """You are a Principal Enterprise Architect with 20 years of experience
at tier-1 financial institutions.

You will be given extracted web content and asked to synthesise a structured analysis.

CRITICAL: Base ALL findings on the provided content only.
Do not use your training knowledge. If content is insufficient for a claim,
write "Insufficient data from sources."

Structure your response with these exact headings:
## Key Trends
## Architectural Patterns
## Regulatory Implications
## Strategic Implications

Write precise professional prose, approximately 600 words.
"""


def run(state: dict) -> dict:
    topic             = state.get("topic", "")
    industry          = state.get("industry", "")
    extracted_content = state.get("extracted_content", "")
    qa_feedback       = state.get("qa_feedback", "")
    iteration         = state.get("iteration_count", 0)

    log.info(f"[analysis] analysing {len(extracted_content)} chars (iteration {iteration})")

    if not extracted_content.strip():
        return {"analysis": "Insufficient extracted content to perform analysis.",
                "current_agent": "analysis_node"}

    # Build user message — topic/industry here, not in system prompt, for caching
    user_content = f"Topic: {topic}\nIndustry: {industry}\n\nContent to analyse:\n\n{extracted_content[:8000]}"

    # On QA loop re-entry, append feedback so analyst can self-correct
    if iteration > 0 and qa_feedback:
        user_content += f"\n\nQA FEEDBACK FROM PREVIOUS ATTEMPT (address these gaps):\n{qa_feedback}"

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ]

    try:
        analysis = groq_call_with_fallback(
            client=client,
            messages=messages,
            primary_model=cfg.LLM_MODEL,
            temperature=cfg.LLM_TEMPERATURE,
            max_tokens=2048,    # 600 words ≈ 800 tokens; was 4096
            task="heavy",       # needs 120B quality
        )
    except RuntimeError as e:
        log.error(f"[analysis] all models exhausted: {e}")
        return {"status": "error", "error": str(e), "current_agent": "analysis_node"}
    except Exception as e:
        log.error(f"[analysis] unexpected error: {e}")
        return {"status": "error", "error": f"Analysis Agent error: {e}",
                "current_agent": "analysis_node"}

    log.info(f"[analysis] produced {len(analysis)} chars")
    return {"analysis": analysis, "current_agent": "analysis_node"}
