"""
extraction_agent.py — Agent 2: Content Extraction Specialist

Scrapes each source URL using BeautifulSoup (free, no API key).
Tags extracted content with its source URL for downstream traceability.
Skips blocked/unavailable URLs without stopping the pipeline.

Design decision: No LLM call in this agent. BeautifulSoup already
extracts text deterministically and for free. Adding Groq to summarise
each page before handing off would cost ~30% extra latency and remove
a useful failure point isolation. See prompts/engineering-prompts.md §7.

Pipeline position: research_node → extractor_node → analysis_node
Reads from state:  sources
Writes to state:   extracted_content, extraction_metadata, current_agent
"""
import logging
from backend.mcp_server.tools import scrape_content
from backend.mcp_server.exceptions import ScrapeError

log = logging.getLogger(__name__)


def run(state: dict) -> dict:
    """
    Execute the Content Extraction Specialist agent.

    For each source URL, calls scrape_content() and accumulates the cleaned
    text. Failed scrapes are recorded in extraction_metadata and skipped —
    the pipeline continues with however many sources could be scraped.

    Args:
        state: Shared pipeline state dict (reads: sources)

    Returns:
        Partial state dict with:
          extracted_content  — all successfully scraped text joined by double newlines
          extraction_metadata — list of per-URL status dicts for debugging
          current_agent      — "extractor_node"
        Never returns an error state: even zero successful scrapes is non-fatal;
        the analysis agent handles empty content gracefully.
    """
    sources = state.get("sources", [])

    # Short-circuit if the research agent returned no sources at all
    if not sources:
        return {"extracted_content": "", "extraction_metadata": [],
                "current_agent": "extractor_node"}

    log.info(f"[extraction] scraping {len(sources)} URLs")

    content_blocks = []   # successfully scraped text blocks (with URL headers)
    metadata       = []   # per-URL status records for /results endpoint
    success_count  = 0

    for source in sources:
        url = source.get("url", "")
        if not url:
            continue   # skip malformed source records silently

        try:
            result = scrape_content(url)
            # Tag each block with its source URL so the analysis agent can
            # reference the source in citations ("according to [Source: url]")
            content_blocks.append(f"[Source: {url}]\n{result['content']}")
            metadata.append({
                "url":        url,
                "status":     "success",
                "char_count": result["char_count"],
            })
            success_count += 1
        except ScrapeError as e:
            # Non-fatal: log, record failure, and continue with next URL
            log.warning(f"[extraction] skipped {url}: {e}")
            metadata.append({"url": url, "status": "failed", "error": str(e)})

    log.info(f"[extraction] {success_count}/{len(sources)} succeeded")

    return {
        "extracted_content":   "\n\n".join(content_blocks),
        "extraction_metadata": metadata,
        "current_agent":       "extractor_node",
    }
