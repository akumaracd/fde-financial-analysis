"""
tools.py — MCP (Model Context Protocol) Tool Layer

All external I/O is isolated here. This is the ONLY file that may call:
  - DDGS()           (DuckDuckGo web search)
  - requests.Session (HTTP scraping via module-level _SESSION)
  - open()           (file writes to OUTPUT_DIR)

Agents MUST NOT make network or file-system calls directly; they call
these functions instead. This design allows every agent to be tested
offline by mocking only these four functions.

Exception hierarchy (imported from exceptions.py):
  MCPToolError   ← base; catch for any tool failure
  ├── SearchError   ← search_sources()
  ├── ScrapeError   ← scrape_content()
  └── FileWriteError ← write_report(), save_decision_matrix()

scrape_content() improvements over the original:
  - Module-level _SESSION with browser-realistic headers (reduces 403s)
  - _HARD_BLOCKED_DOMAINS: researchgate.net, spglobal.com etc. skipped
    immediately before any network call — no wasted retry budget
  - Retry adapter with backoff on 429/5xx
  - SSL: certifi bundle by default; CUSTOM_CA_BUNDLE and DISABLE_SSL_VERIFY
    env vars for corporate proxy environments
  - Timeout raised to explicit 15s (was implicit)
"""
import csv
import logging
import os
import re
from urllib.parse import urlparse

import certifi
import requests
from bs4 import BeautifulSoup
from ddgs import DDGS
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from backend.config import cfg
from backend.mcp_server.exceptions import FileWriteError, SearchError, ScrapeError

log = logging.getLogger(__name__)


# ── search_sources ─────────────────────────────────────────────────────────

REGULATORY_KEYWORDS = [
    "regulation", "regulatory", "compliance", "basel", "dora",
    "sr 11-7", "federal reserve", "fca", "occ", "ffiec",
    "eu ai act", "gdpr", "psd2", "government", "guidance",
]


def search_sources(query: str, industry: str = "", max_results: int = 10) -> list[dict]:
    """
    Run a single DuckDuckGo text search and normalise results into the
    `sources` shape expected by research_agent and downstream agents.

    Args:
        query:       The raw search query string. Must not be empty.
        industry:    Optional industry context appended to the query
                     (only if not already present, to avoid duplication).
        max_results: Maximum number of results to request from DDGS.

    Returns:
        List of source dicts, each with:
            title           (str)   — page title from DDGS
            url             (str)   — canonical page URL
            summary         (str)   — first 300 chars of DDGS body snippet
            regulatory_flag (bool)  — True if any REGULATORY_KEYWORD found
            relevance_score (float) — 8.5 if regulatory, else 7.5

    Raises:
        SearchError: blank query / DDGS network error / zero results
    """
    if not query or not query.strip():
        raise SearchError("Query cannot be empty")

    if industry and industry.lower() not in query.lower():
        full_query = f"{query} {industry}".strip()
    else:
        full_query = query.strip()

    try:
        with DDGS() as ddgs:
            raw = list(ddgs.text(full_query, max_results=max_results))
    except Exception as e:
        raise SearchError(f"DuckDuckGo failed: {e}") from e

    if not raw:
        raise SearchError(f"0 results for: '{full_query}'")

    sources = []
    for r in raw:
        combined = (r.get("title", "") + " " + r.get("body", "")).lower()
        regulatory = any(kw in combined for kw in REGULATORY_KEYWORDS)
        sources.append({
            "title":           r.get("title", ""),
            "url":             r.get("href", ""),
            "summary":         r.get("body", "")[:300],
            "regulatory_flag": regulatory,
            "relevance_score": 8.5 if regulatory else 7.5,
        })
    return sources


# ── scrape_content ─────────────────────────────────────────────────────────

# Domains that block all scrapers regardless of headers.
# Skipped immediately before any network call — saves retry budget.
_HARD_BLOCKED_DOMAINS = {
    "researchgate.net",
    "spglobal.com",
    "market-intelligence.spglobal.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "economist.com",
    "hbr.org",
}

# Full browser-realistic headers — modern bot detectors check all of these
_SCRAPE_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0.0.0 Safari/537.36"
    ),
    "Accept":          "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate, br",
    "Connection":      "keep-alive",
    "DNT":             "1",
}

# Tags to strip before text extraction — noise, not content
STRIP_TAGS = ["script", "style", "nav", "footer", "header", "aside",
              "noscript", "iframe", "form"]


def _build_scrape_session() -> requests.Session:
    """
    Build a requests.Session with:
      - Browser-realistic headers
      - Retry with exponential backoff (429, 5xx)
      - SSL: certifi bundle by default; env vars for corporate proxies:
          CUSTOM_CA_BUNDLE=/path/to/ca.crt
          DISABLE_SSL_VERIFY=true  (dev only)

    Called once at module load — reused for all scrape_content() calls.
    """
    session = requests.Session()
    session.headers.update(_SCRAPE_HEADERS)

    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )
    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    custom_ca = os.environ.get("CUSTOM_CA_BUNDLE")
    disable_verify = os.environ.get("DISABLE_SSL_VERIFY", "").lower() == "true"

    if disable_verify:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        session.verify = False
        log.warning("[scraper] SSL verification disabled via DISABLE_SSL_VERIFY=true")
    elif custom_ca:
        session.verify = custom_ca
        log.info("[scraper] using custom CA bundle: %s", custom_ca)
    else:
        session.verify = certifi.where()

    return session


# Module-level session — created once, reused for all scrape_content() calls.
# Tests patch "backend.mcp_server.tools._SESSION" to intercept HTTP calls.
_SESSION = _build_scrape_session()


def scrape_content(url: str, max_chars: int = None) -> dict:
    """
    Fetch a URL and return its cleaned, plain-text body content.

    Args:
        url:       Must start with "http". Invalid URLs raise immediately.
        max_chars: Character cap. Defaults to cfg.SCRAPE_MAX_CHARS (8000).

    Returns:
        Dict with keys: url, content (str), char_count (int), status ("success")

    Raises:
        ScrapeError: blocked domain / invalid URL / timeout / HTTP error /
                     non-HTML / content too short
    """
    if not url or not url.startswith("http"):
        raise ScrapeError(f"Invalid URL: '{url}'")

    # Skip hard-blocked domains immediately — no network call
    domain = urlparse(url).netloc.lower().lstrip("www.")
    for blocked in _HARD_BLOCKED_DOMAINS:
        if blocked in domain:
            raise ScrapeError(
                f"403 bot-blocked: {url} — {blocked} requires login or API access"
            )

    max_chars = max_chars or cfg.SCRAPE_MAX_CHARS
    if not max_chars:
        raise ScrapeError("SCRAPE_MAX_CHARS is not configured")

    try:
        resp = _SESSION.get(url, timeout=15)
    except requests.exceptions.SSLError as e:
        raise ScrapeError(
            f"SSL error for {url}: {e}. "
            "Set CUSTOM_CA_BUNDLE=/path/to/ca.crt or DISABLE_SSL_VERIFY=true in .env"
        ) from e
    except requests.exceptions.Timeout:
        raise ScrapeError(f"Scrape timeout (15s): {url}")
    except requests.exceptions.RequestException as e:
        raise ScrapeError(f"Request failed: {url} ({e})")

    if resp.status_code == 403:
        raise ScrapeError(f"403 bot-blocked: {url}")
    if resp.status_code == 429:
        raise ScrapeError(f"429 rate-limited: {url}")
    if resp.status_code != 200:
        raise ScrapeError(f"HTTP {resp.status_code}: {url}")
    if "text/html" not in resp.headers.get("content-type", ""):
        raise ScrapeError(f"Non-HTML: {url}")

    soup = BeautifulSoup(resp.text, "html.parser")
    for tag in soup(STRIP_TAGS):
        tag.decompose()

    # Prefer article/main for content-heavy pages; fall back to full body
    main = soup.find("article") or soup.find("main") or soup.find("body")
    raw_text = (main or soup).get_text(separator="\n", strip=True)

    # Collapse runs of blank lines introduced by removed tags
    text = re.sub(r"\n{3,}", "\n\n", raw_text).strip()

    if len(text) < 50:
        raise ScrapeError(f"Content too short ({len(text)} chars): {url}")

    if len(text) > max_chars:
        text = text[:max_chars] + "\n[...truncated]"

    log.info("[scraper] OK %s — %d chars", url, len(text))
    return {"url": url, "content": text, "char_count": len(text), "status": "success"}


# ── filename helpers ──────────────────────────────────────────────────────

def _safe_slug(topic: str, max_len: int = 50) -> str:
    """
    Convert a topic string into a safe, readable filename slug.
    'AI Agent Adoption for Banking' → 'ai_agent_adoption_for_banking'
    Falls back to run_id if topic is empty.
    """
    slug = topic.lower().strip()
    slug = re.sub(r"[^\w\s-]", "", slug)   # remove special chars
    slug = re.sub(r"[\s-]+",    "_", slug)   # spaces/hyphens → underscore
    return slug[:max_len].strip("_")


def make_file_slug(topic: str, run_id: str) -> str:
    """
    Return a slug for file naming: topic-based if topic is set,
    otherwise fall back to the run_id (original behaviour).
    """
    if topic and topic.strip():
        return _safe_slug(topic)
    return os.path.basename(run_id)


# ── write_report ───────────────────────────────────────────────────────────

def write_report(content: str, run_id: str, topic: str = "") -> str:
    """
    Write the final markdown executive report to OUTPUT_DIR.

    Filename: <topic-slug>_report.md  e.g. ai_agent_adoption_for_banking_report.md
    Falls back to <run_id>_report.md if topic is not provided.

    Security: slug is sanitised to prevent path traversal.

    Args:
        content: Non-empty markdown string to write.
        run_id:  Pipeline run identifier (fallback filename prefix).
        topic:   Human-readable topic → used as filename if provided.

    Returns:
        Absolute path to the written .md file.

    Raises:
        FileWriteError: Empty content.
    """
    if not content or not content.strip():
        raise FileWriteError("Empty content")

    slug = make_file_slug(topic, run_id)
    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)
    path = os.path.join(cfg.OUTPUT_DIR, f"{slug}_report.md")
    with open(path, "w", encoding="utf-8") as f:
        f.write(content)
    log.info("[tools] report written: %s", path)
    return os.path.abspath(path)


# ── save_decision_matrix ───────────────────────────────────────────────────

def save_decision_matrix(matrix: list[dict], run_id: str, topic: str = "") -> str:
    """
    Write the scoring agent's decision matrix to a CSV file.

    Filename: <topic-slug>_matrix.csv  e.g. ai_agent_adoption_for_banking_matrix.csv
    Falls back to <run_id>_matrix.csv if topic is not provided.

    Validates that all rows have identical keys before writing —
    csv.DictWriter raises an unhelpful ValueError on key mismatch.

    Args:
        matrix: Non-empty list of dicts with consistent keys.
        run_id: Pipeline run identifier (fallback filename prefix).
        topic:  Human-readable topic → used as filename if provided.

    Returns:
        Absolute path to the written .csv file.

    Raises:
        FileWriteError: Empty matrix or inconsistent row keys.
    """
    if not matrix:
        raise FileWriteError("Empty matrix")

    expected_keys = matrix[0].keys()
    inconsistent = [
        i for i, row in enumerate(matrix[1:], start=1)
        if row.keys() != expected_keys
    ]
    if inconsistent:
        raise FileWriteError(
            f"Matrix rows have inconsistent keys at index(es): {inconsistent}. "
            f"Expected keys: {list(expected_keys)}"
        )

    slug = make_file_slug(topic, run_id)
    os.makedirs(cfg.OUTPUT_DIR, exist_ok=True)
    path = os.path.join(cfg.OUTPUT_DIR, f"{slug}_matrix.csv")
    with open(path, "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=matrix[0].keys())
        w.writeheader()
        w.writerows(matrix)
    log.info("[tools] matrix written: %s", path)
    return os.path.abspath(path)
