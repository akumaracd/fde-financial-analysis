"""
exceptions.py — MCP Tool Error Hierarchy

All errors raised by the MCP tool layer (tools.py) inherit from MCPToolError.
Agents catch the most-specific subclass they need and let others propagate:

    from backend.mcp_server.exceptions import SearchError, ScrapeError, FileWriteError

    try:
        results = search_sources(query, industry)
    except SearchError as e:
        log.warning(f"search failed: {e}")   # non-fatal: skip query

Usage pattern:
  - Catch SearchError/ScrapeError individually per call (non-fatal, skip and continue)
  - Catch FileWriteError on write_report() (fatal: abort agent and return error state)
  - Catch MCPToolError to handle any tool failure generically
"""


class MCPToolError(Exception):
    """
    Base class for all MCP tool failures.
    Catch this when you want to handle any tool error with a single handler.
    """
    pass


class SearchError(MCPToolError):
    """
    Raised by search_sources() when:
      - The query string is empty or blank
      - DuckDuckGo returns a network/parse error
      - The search returns 0 results

    Agents treat this as non-fatal per query: one failed query is logged
    and skipped; if ALL queries fail, the research agent returns an error state.
    """
    pass


class ScrapeError(MCPToolError):
    """
    Raised by scrape_content() when:
      - URL is invalid or doesn't start with 'http'
      - SCRAPE_MAX_CHARS is not configured
      - HTTP request times out
      - HTTP request fails (connection error, etc.)
      - Server returns 403 (bot-blocked)
      - Server returns any non-200 status code
      - Response content-type is not text/html
      - Extracted text is shorter than 50 characters

    Agents treat this as non-fatal per URL: failed scrapes are logged,
    recorded in extraction_metadata, and skipped. The pipeline continues
    with whichever sources were successfully scraped.
    """
    pass


class FileWriteError(MCPToolError):
    """
    Raised by write_report() and save_decision_matrix() when:
      - Content/matrix is empty
      - Matrix rows have inconsistent keys

    write_report() failure is FATAL — the report_writer_agent returns an
    error state and halts the pipeline (a report that wasn't written is
    a pipeline failure).

    save_decision_matrix() failure is NON-FATAL — the agent logs a warning
    and continues; the report itself is still written and readable.
    """
    pass
