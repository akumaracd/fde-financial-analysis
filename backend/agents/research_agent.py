"""
research_agent.py — Agent 1: Research Analyst

TPD optimisations applied:
  - task="light"   → starts on gpt-oss-20b, saves 120B quota
  - max_tokens=1024 → filtered source list is short; was 4096 (wasted ~3000 tokens)
  - SYSTEM_PROMPT variables moved to user message → system prompt is now static,
    enabling Groq prompt caching (50% off repeated input tokens)
"""
import json
import logging
from openai import OpenAI
from backend.config import cfg
from backend.groq_fallback import groq_call_with_fallback
from backend.mcp_server.tools import search_sources
from backend.mcp_server.exceptions import SearchError
from backend.utils.json_utils import safe_json_parse

log    = logging.getLogger(__name__)
client = OpenAI(**cfg.llm_client_kwargs())

# Static system prompt — no f-string variables here.
# Groq caches repeated prefixes at 50% cost; dynamic content goes in user message.
SYSTEM_PROMPT = """You are a Senior Research Analyst specialising in financial services.

You will be given raw DuckDuckGo search results for a topic and industry.
Score and filter them — return a JSON array of the best sources.

Rules:
- Keep sources with relevance 6/10 or higher
- Flag regulatory sources (DORA, Basel, SR 11-7, FCA, OCC, GDPR, EU AI Act)
- Remove duplicates and marketing-only content
- Return ONLY valid JSON — no prose, no markdown fences

Each object must have exactly:
  title, url, summary, regulatory_flag (boolean), relevance_score (float 1-10)
"""


def run(state: dict) -> dict:
    topic    = state.get("topic", "")
    industry = state.get("industry", "")

    if not topic.strip():
        return {"status": "error", "error": "topic is required",
                "current_agent": "research_node"}

    log.info(f"[research] searching for: {topic}")

    all_raw = []
    for query in [
        f"{topic} regulation compliance framework",
        f"{topic} implementation best practices risk",
    ]:
        try:
            results = search_sources(query, industry, max_results=6)
            all_raw.extend(results)
        except SearchError as e:
            log.warning(f"[research] search failed: {e}")

    if not all_raw:
        return {"status": "error",
                "error": "All DuckDuckGo searches returned 0 results",
                "current_agent": "research_node"}

    seen, unique = set(), []
    for s in all_raw:
        if s["url"] not in seen:
            seen.add(s["url"])
            unique.append(s)

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        # Topic/industry moved to user message so system prompt stays static (cacheable)
        {"role": "user",
         "content": (
             f"Topic: {topic}\nIndustry: {industry}\n\n"
             f"Score and filter these results:\n{json.dumps(unique, indent=2)}"
         )},
    ]

    try:
        raw_content = groq_call_with_fallback(
            client=client,
            messages=messages,
            primary_model=cfg.LLM_MODEL,
            temperature=cfg.LLM_TEMPERATURE,
            max_tokens=1024,    # source list is short; was 4096
            task="light",       # uses gpt-oss-20b; saves 120B TPD
        )
        sources = safe_json_parse(raw_content, default=unique, label="research")

        if isinstance(sources, dict):
            sources = sources.get("sources", unique)
        if not isinstance(sources, list):
            log.warning("[research] unexpected type %s — using raw results", type(sources))
            sources = unique

    except Exception as e:
        log.warning(f"[research] Groq scoring failed ({e}), using raw results")
        sources = unique

    log.info(f"[research] returning {len(sources)} sources")
    return {"sources": sources, "current_agent": "research_node"}
