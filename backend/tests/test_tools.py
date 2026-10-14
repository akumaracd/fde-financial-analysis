"""
test_tools.py — Unit tests for backend/mcp_server/tools.py

Covers all four MCP tool functions with both happy-path and adversarial
(error) scenarios. All network and filesystem calls are mocked so tests
run fully offline.

Test organisation:
  TestSearchSources       — search_sources()
  TestScrapeContent       — scrape_content()
  TestWriteReport         — write_report()
  TestSaveDecisionMatrix  — save_decision_matrix()
"""
import pytest
import os
from unittest.mock import patch, MagicMock
from backend.mcp_server.exceptions import SearchError, ScrapeError, FileWriteError


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture
def sample_matrix():
    """Minimal valid decision matrix for write tests."""
    return [
        {"approach": "Build", "regulatory_compliance": 8, "weighted_total": 7.2,
         "verdict": "Conditional"},
        {"approach": "Buy",   "regulatory_compliance": 7, "weighted_total": 7.5,
         "verdict": "Recommended"},
    ]


# ── search_sources ────────────────────────────────────────────────────────────

class TestSearchSources:
    def test_empty_query_raises_search_error(self):
        from backend.mcp_server.tools import search_sources
        with pytest.raises(SearchError, match="empty"):
            search_sources("")
        with pytest.raises(SearchError, match="empty"):
            search_sources("   ")

    def test_returns_normalised_source_list(self):
        mock_results = [{"title": "DORA Guide", "href": "https://ec.europa.eu/dora",
                         "body": "DORA regulation compliance guidance"}]
        with patch("backend.mcp_server.tools.DDGS") as mock_ddgs:
            mock_ddgs.return_value.__enter__.return_value.text.return_value = mock_results
            from backend.mcp_server.tools import search_sources
            results = search_sources("DORA compliance", "Banking")

        assert len(results) == 1
        assert results[0]["title"] == "DORA Guide"
        assert results[0]["url"]   == "https://ec.europa.eu/dora"
        assert "regulatory_flag" in results[0]
        assert "relevance_score" in results[0]

    def test_regulatory_keyword_sets_flag_and_higher_score(self):
        mock_results = [{"title": "Basel III Framework", "href": "https://bis.org/basel",
                         "body": "Basel compliance requirements for tier-1 capital"}]
        with patch("backend.mcp_server.tools.DDGS") as mock_ddgs:
            mock_ddgs.return_value.__enter__.return_value.text.return_value = mock_results
            from backend.mcp_server.tools import search_sources
            results = search_sources("Basel capital requirements")

        assert results[0]["regulatory_flag"] is True
        assert results[0]["relevance_score"] == 8.5

    def test_non_regulatory_source_gets_lower_score(self):
        mock_results = [{"title": "AI Vendor Product Page", "href": "https://vendor.com",
                         "body": "Buy our enterprise AI platform today"}]
        with patch("backend.mcp_server.tools.DDGS") as mock_ddgs:
            mock_ddgs.return_value.__enter__.return_value.text.return_value = mock_results
            from backend.mcp_server.tools import search_sources
            results = search_sources("AI banking platform")

        assert results[0]["regulatory_flag"] is False
        assert results[0]["relevance_score"] == 7.5

    def test_zero_results_raises_search_error(self):
        with patch("backend.mcp_server.tools.DDGS") as mock_ddgs:
            mock_ddgs.return_value.__enter__.return_value.text.return_value = []
            from backend.mcp_server.tools import search_sources
            with pytest.raises(SearchError, match="0 results"):
                search_sources("obscure topic nobody writes about")

    def test_ddgs_exception_raises_search_error(self):
        with patch("backend.mcp_server.tools.DDGS") as mock_ddgs:
            mock_ddgs.return_value.__enter__.return_value.text.side_effect = \
                Exception("Connection refused")
            from backend.mcp_server.tools import search_sources
            with pytest.raises(SearchError, match="DuckDuckGo failed"):
                search_sources("AI adoption banking")

    def test_industry_appended_to_query_when_not_present(self):
        with patch("backend.mcp_server.tools.DDGS") as mock_ddgs:
            mock_ddgs.return_value.__enter__.return_value.text.return_value = [
                {"title": "T", "href": "https://x.com", "body": "regulation compliance"}
            ]
            from backend.mcp_server.tools import search_sources
            search_sources("AI governance", "Banking / Financial Services")
            call_args = mock_ddgs.return_value.__enter__.return_value.text.call_args
            assert "Banking / Financial Services" in call_args[0][0]

    def test_summary_capped_at_300_chars(self):
        long_body = "x" * 500
        with patch("backend.mcp_server.tools.DDGS") as mock_ddgs:
            mock_ddgs.return_value.__enter__.return_value.text.return_value = [
                {"title": "T", "href": "https://x.com", "body": long_body}
            ]
            from backend.mcp_server.tools import search_sources
            results = search_sources("AI governance")
        assert len(results[0]["summary"]) <= 300


# ── scrape_content ────────────────────────────────────────────────────────────

class TestScrapeContent:
    """
    tools.py uses a module-level _SESSION (requests.Session) rather than
    calling requests.get() directly. Tests patch _SESSION.get to intercept
    HTTP calls without rebuilding the session.
    """

    def _mock_response(self, text="<html><body><p>Article content here and more text for length.</p></body></html>",
                       status_code=200, content_type="text/html"):
        resp = MagicMock()
        resp.text = text
        resp.status_code = status_code
        resp.headers = {"content-type": content_type}
        return resp

    def test_invalid_url_raises_scrape_error(self):
        from backend.mcp_server.tools import scrape_content
        with pytest.raises(ScrapeError, match="Invalid URL"):
            scrape_content("")
        with pytest.raises(ScrapeError, match="Invalid URL"):
            scrape_content("ftp://example.com")

    def test_hard_blocked_domain_raises_scrape_error(self):
        """Domains in _HARD_BLOCKED_DOMAINS skip immediately before any network call."""
        from backend.mcp_server.tools import scrape_content
        with pytest.raises(ScrapeError, match="bot-blocked"):
            scrape_content("https://www.researchgate.net/publication/123")

    def test_successful_scrape_returns_dict(self):
        html = "<html><body><p>" + "Article content. " * 20 + "</p></body></html>"
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            mock_session.get.return_value = self._mock_response(html)
            from backend.mcp_server.tools import scrape_content
            result = scrape_content("https://example.com")

        assert result["status"]    == "success"
        assert result["url"]       == "https://example.com"
        assert result["char_count"] > 0
        assert isinstance(result["content"], str)

    def test_403_raises_scrape_error(self):
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            mock_session.get.return_value = self._mock_response(status_code=403)
            from backend.mcp_server.tools import scrape_content
            with pytest.raises(ScrapeError, match="403"):
                scrape_content("https://bot-blocking-site.com")

    def test_non_200_status_raises_scrape_error(self):
        for code in [404, 500, 503]:
            with patch("backend.mcp_server.tools._SESSION") as mock_session:
                mock_session.get.return_value = self._mock_response(status_code=code)
                from backend.mcp_server.tools import scrape_content
                with pytest.raises(ScrapeError, match=str(code)):
                    scrape_content(f"https://broken-site-{code}.com")

    def test_non_html_content_type_raises_scrape_error(self):
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            mock_session.get.return_value = self._mock_response(content_type="application/pdf")
            from backend.mcp_server.tools import scrape_content
            with pytest.raises(ScrapeError, match="Non-HTML"):
                scrape_content("https://site.com/document.pdf")

    def test_content_too_short_raises_scrape_error(self):
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            mock_session.get.return_value = self._mock_response("<html><body>Hi</body></html>")
            from backend.mcp_server.tools import scrape_content
            with pytest.raises(ScrapeError, match="too short"):
                scrape_content("https://example.com")

    def test_content_truncated_at_max_chars(self):
        long_content = "word " * 5000
        html = f"<html><body><p>{long_content}</p></body></html>"
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            mock_session.get.return_value = self._mock_response(html)
            from backend.mcp_server.tools import scrape_content
            result = scrape_content("https://example.com", max_chars=500)

        assert len(result["content"]) <= 520
        assert "[...truncated]" in result["content"]

    def test_timeout_raises_scrape_error(self):
        import requests as req
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            mock_session.get.side_effect = req.exceptions.Timeout("timed out")
            from backend.mcp_server.tools import scrape_content
            with pytest.raises(ScrapeError, match="[Tt]imeout"):
                scrape_content("https://slow-site.com")

    def test_noise_tags_stripped(self):
        html = """<html><body>
            <nav>Skip to main content</nav>
            <p>Real article body text that matters for analysis.</p>
            <footer>Copyright 2024</footer>
            <script>analytics.track()</script>
        </body></html>"""
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            mock_session.get.return_value = self._mock_response(html)
            from backend.mcp_server.tools import scrape_content
            result = scrape_content("https://example.com")

        assert "Skip to main content" not in result["content"]
        assert "Copyright 2024" not in result["content"]
        assert "Real article body text" in result["content"]


# ── write_report ──────────────────────────────────────────────────────────────

class TestWriteReport:
    def test_empty_content_raises_file_write_error(self):
        from backend.mcp_server.tools import write_report
        with pytest.raises(FileWriteError, match="Empty"):
            write_report("", "run-123")
        with pytest.raises(FileWriteError, match="Empty"):
            write_report("   \n   ", "run-123")

    def test_successful_write_returns_absolute_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.mcp_server.tools.cfg.OUTPUT_DIR", str(tmp_path))
        from backend.mcp_server.tools import write_report
        path = write_report("# My Report\n\nContent.", "run-abc")

        assert os.path.isabs(path)
        assert path.endswith("run-abc_report.md")
        assert os.path.exists(path)
        assert "# My Report" in open(path).read()

    def test_run_id_sanitised_against_path_traversal(self, tmp_path, monkeypatch):
        monkeypatch.setattr("backend.mcp_server.tools.cfg.OUTPUT_DIR", str(tmp_path))
        from backend.mcp_server.tools import write_report
        path = write_report("# Report\n\nContent.", "../../evil-id")
        assert os.path.dirname(path) == str(tmp_path)
        assert "evil-id_report.md" in path


# ── save_decision_matrix ──────────────────────────────────────────────────────

class TestSaveDecisionMatrix:
    def test_empty_matrix_raises_file_write_error(self):
        from backend.mcp_server.tools import save_decision_matrix
        with pytest.raises(FileWriteError, match="Empty"):
            save_decision_matrix([], "run-123")

    def test_inconsistent_keys_raises_file_write_error(self):
        bad_matrix = [
            {"approach": "A", "score": 7},
            {"approach": "B", "total": 8},
        ]
        from backend.mcp_server.tools import save_decision_matrix
        with pytest.raises(FileWriteError, match="inconsistent"):
            save_decision_matrix(bad_matrix, "run-123")

    def test_successful_write_returns_absolute_path(self, tmp_path, monkeypatch, sample_matrix):
        monkeypatch.setattr("backend.mcp_server.tools.cfg.OUTPUT_DIR", str(tmp_path))
        from backend.mcp_server.tools import save_decision_matrix
        path = save_decision_matrix(sample_matrix, "run-xyz")

        assert os.path.isabs(path)
        assert path.endswith("run-xyz_matrix.csv")
        assert os.path.exists(path)

    def test_csv_contains_correct_headers_and_rows(self, tmp_path, monkeypatch, sample_matrix):
        monkeypatch.setattr("backend.mcp_server.tools.cfg.OUTPUT_DIR", str(tmp_path))
        from backend.mcp_server.tools import save_decision_matrix
        import csv
        path = save_decision_matrix(sample_matrix, "run-csv-check")
        with open(path, newline="") as f:
            rows = list(csv.DictReader(f))
        assert len(rows) == len(sample_matrix)
        assert rows[0]["approach"] == sample_matrix[0]["approach"]
