"""
backend/mcp_server/tools_scraper.py — Robust scrape_content() replacement.

Drop this file into backend/mcp_server/ and update tools.py to import
scrape_content from here instead of defining it inline.

Fixes:
  1. 403 bot-blocks (ResearchGate, S&P, Bloomberg etc.)
     → browser-realistic headers + session reuse
  2. SSL certificate verify failed (corporate proxy / self-signed chain)
     → certifi bundle + optional CA override via env var CUSTOM_CA_BUNDLE
  3. Bare requests.get() with no timeout
     → explicit timeout + retry with backoff

Known hard-blocked domains (no headers will help — they require login/API):
  researchgate.net, spglobal.com, bloomberg.com, ft.com, wsj.com
These are skipped immediately with a clear ScrapeError so the pipeline
doesn't burn retry budget on them.
"""

import logging
import os
import time
from urllib.parse import urlparse

import certifi
import requests
from bs4 import BeautifulSoup
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from backend.mcp_server.exceptions import ScrapeError

log = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Domains that block all scrapers regardless of headers.
# Skipped immediately — saves time and avoids polluting retry budgets.
# ---------------------------------------------------------------------------
_HARD_BLOCKED = {
    "researchgate.net",
    "spglobal.com",
    "market-intelligence.spglobal.com",
    "bloomberg.com",
    "ft.com",
    "wsj.com",
    "economist.com",
}

# ---------------------------------------------------------------------------
# Browser-realistic headers — reduces 403s from basic bot detection
# ---------------------------------------------------------------------------
_HEADERS = {
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

# ---------------------------------------------------------------------------
# Shared session — reused across calls for connection pooling
# ---------------------------------------------------------------------------
def _build_session() -> requests.Session:
    session = requests.Session()
    session.headers.update(_HEADERS)

    retry = Retry(
        total=3,
        backoff_factor=1.0,
        status_forcelist=[429, 500, 502, 503, 504],
        allowed_methods=["GET"],
        raise_on_status=False,
    )

    # Use custom CA bundle from env if set (corporate proxy fix),
    # otherwise fall back to certifi's trusted bundle
    ca_bundle = os.environ.get("CUSTOM_CA_BUNDLE") or certifi.where()
    disable_verify = os.environ.get("DISABLE_SSL_VERIFY", "").lower() == "true"

    adapter = HTTPAdapter(max_retries=retry)
    session.mount("https://", adapter)
    session.mount("http://", adapter)

    session.verify = False if disable_verify else ca_bundle
    if disable_verify:
        import urllib3
        urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
        log.warning("[scraper] SSL verification disabled — set only for dev/corp proxy")

    return session


_SESSION: requests.Session | None = None


def _get_session() -> requests.Session:
    global _SESSION
    if _SESSION is None:
        _SESSION = _build_session()
    return _SESSION


# ---------------------------------------------------------------------------
# Public API — drop-in replacement for the old scrape_content()
# ---------------------------------------------------------------------------
def scrape_content(url: str, timeout: int = 15, min_chars: int = 200) -> dict:
    """
    Fetch and extract clean text from a URL.

    Args:
        url:       Target URL.
        timeout:   Request timeout in seconds.
        min_chars: Minimum extracted text length to consider a success.

    Returns:
        {"content": str, "char_count": int, "url": str}

    Raises:
        ScrapeError: on any failure (403, SSL, timeout, too-short content).
                     extraction_agent.py catches this and continues.
    """
    domain = urlparse(url).netloc.lower().lstrip("www.")

    # Hard-blocked domains: skip immediately, no network call
    for blocked in _HARD_BLOCKED:
        if blocked in domain:
            raise ScrapeError(
                f"403 bot-blocked: {url} — domain {blocked} requires login or API access"
            )

    session = _get_session()

    try:
        resp = session.get(url, timeout=timeout)
    except requests.exceptions.SSLError as e:
        raise ScrapeError(
            f"SSL error for {url}: {e}. "
            "Set CUSTOM_CA_BUNDLE=/path/to/ca.crt or DISABLE_SSL_VERIFY=true in .env"
        ) from e
    except requests.exceptions.Timeout:
        raise ScrapeError(f"Timeout fetching {url} after {timeout}s") from None
    except requests.exceptions.ConnectionError as e:
        raise ScrapeError(f"Connection error for {url}: {e}") from e
    except requests.exceptions.RequestException as e:
        raise ScrapeError(f"Request failed for {url}: {e}") from e

    if resp.status_code == 403:
        raise ScrapeError(f"403 bot-blocked: {url}")
    if resp.status_code == 429:
        raise ScrapeError(f"429 rate-limited: {url}")
    if resp.status_code != 200:
        raise ScrapeError(f"HTTP {resp.status_code} for {url}")

    # Extract clean text via BeautifulSoup
    soup = BeautifulSoup(resp.text, "html.parser")

    # Remove noise elements before extracting text
    for tag in soup(["script", "style", "nav", "footer", "header",
                     "aside", "form", "noscript", "iframe"]):
        tag.decompose()

    # Prefer article/main content; fall back to full body
    main = soup.find("article") or soup.find("main") or soup.find("body")
    text = (main or soup).get_text(separator="\n", strip=True)

    # Collapse excessive blank lines
    import re
    text = re.sub(r"\n{3,}", "\n\n", text).strip()

    if len(text) < min_chars:
        raise ScrapeError(
            f"Extracted only {len(text)} chars from {url} "
            f"(min {min_chars}) — likely a login wall or empty page"
        )

    log.info("[scraper] OK %s — %d chars", url, len(text))
    return {"content": text, "char_count": len(text), "url": url}
