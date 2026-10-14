"""
test_mcp_tools.py — MCP tool layer tests.

Tests search_sources(), scrape_content(), write_report(),
save_decision_matrix(), and make_file_slug() with full mocking.

Run:
    pytest backend/tests/test_mcp_tools.py -v
"""
import csv
import os
import pytest
from unittest.mock import patch, MagicMock


# ── search_sources ─────────────────────────────────────────────────────────────

class TestSearchSources:

    MOCK_DDG_RESULTS = [
        {"title": "DORA AI Compliance 2026", "href": "https://example.com/dora",
         "body": "DORA regulation requires AI governance framework for financial institutions..."},
        {"title": "AI Banking Trends", "href": "https://example.com/trends",
         "body": "Artificial intelligence is transforming retail banking operations..."},
        {"title": "Federal Reserve AI Guidance", "href": "https://example.com/fed",
         "body": "Federal Reserve SR 11-7 guidance on model risk management applies to AI..."},
    ]

    def test_happy_path_returns_sources(self):
        with patch("backend.mcp_server.tools.DDGS") as mock_ddgs:
            mock_ddgs.return_value.__enter__.return_value.text.return_value = self.MOCK_DDG_RESULTS
            from backend.mcp_server.tools import search_sources
            results = search_sources("AI banking compliance", "Banking")

        assert len(results) == 3
        assert results[0]["title"] == "DORA AI Compliance 2026"
        assert results[0]["url"] == "https://example.com/dora"
        assert "regulatory_flag" in results[0]
        assert "relevance_score" in results[0]

    def test_regulatory_sources_flagged_correctly(self):
        with patch("backend.mcp_server.tools.DDGS") as mock_ddgs:
            mock_ddgs.return_value.__enter__.return_value.text.return_value = self.MOCK_DDG_RESULTS
            from backend.mcp_server.tools import search_sources
            results = search_sources("AI banking", "Banking")

        # DORA result should be flagged as regulatory
        dora_result = next(r for r in results if "DORA" in r["title"])
        assert dora_result["regulatory_flag"] is True
        assert dora_result["relevance_score"] == 8.5

        # General AI trends should not be flagged
        trends_result = next(r for r in results if "Trends" in r["title"])
        assert trends_result["regulatory_flag"] is False
        assert trends_result["relevance_score"] == 7.5

    def test_industry_appended_to_query(self):
        captured_queries = []

        def mock_text(query, max_results):
            captured_queries.append(query)
            return self.MOCK_DDG_RESULTS

        with patch("backend.mcp_server.tools.DDGS") as mock_ddgs:
            mock_ddgs.return_value.__enter__.return_value.text.side_effect = mock_text
            from backend.mcp_server.tools import search_sources
            search_sources("AI compliance", "Banking / Financial Services")

        assert any("Banking / Financial Services" in q for q in captured_queries)

    def test_industry_not_duplicated_if_already_in_query(self):
        captured_queries = []

        def mock_text(query, max_results):
            captured_queries.append(query)
            return self.MOCK_DDG_RESULTS

        with patch("backend.mcp_server.tools.DDGS") as mock_ddgs:
            mock_ddgs.return_value.__enter__.return_value.text.side_effect = mock_text
            from backend.mcp_server.tools import search_sources
            search_sources("AI compliance for Banking", "Banking")

        for q in captured_queries:
            assert q.count("Banking") <= 1

    def test_empty_query_raises_search_error(self):
        from backend.mcp_server.tools import search_sources
        from backend.mcp_server.exceptions import SearchError
        with pytest.raises(SearchError, match="empty"):
            search_sources("   ")

    def test_ddg_failure_raises_search_error(self):
        with patch("backend.mcp_server.tools.DDGS") as mock_ddgs:
            mock_ddgs.return_value.__enter__.return_value.text.side_effect = Exception("network error")
            from backend.mcp_server.tools import search_sources
            from backend.mcp_server.exceptions import SearchError
            with pytest.raises(SearchError, match="DuckDuckGo failed"):
                search_sources("AI banking")

    def test_zero_results_raises_search_error(self):
        with patch("backend.mcp_server.tools.DDGS") as mock_ddgs:
            mock_ddgs.return_value.__enter__.return_value.text.return_value = []
            from backend.mcp_server.tools import search_sources
            from backend.mcp_server.exceptions import SearchError
            with pytest.raises(SearchError, match="0 results"):
                search_sources("AI banking")

    def test_summary_capped_at_300_chars(self):
        long_body = "X" * 1000
        results_with_long_body = [{"title": "T", "href": "https://ex.com", "body": long_body}]
        with patch("backend.mcp_server.tools.DDGS") as mock_ddgs:
            mock_ddgs.return_value.__enter__.return_value.text.return_value = results_with_long_body
            from backend.mcp_server.tools import search_sources
            results = search_sources("test")
        assert len(results[0]["summary"]) <= 300


# ── scrape_content ─────────────────────────────────────────────────────────────

class TestScrapeContent:

    MOCK_HTML = """<!DOCTYPE html>
    <html><body>
    <nav>Navigation menu</nav>
    <article>
        <h1>AI in Banking Compliance</h1>
        <p>Artificial intelligence is transforming compliance in banking. 
        DORA Article 28 mandates oversight of third-party AI vendors. 
        Financial institutions must implement robust governance frameworks.</p>
        <p>Key regulatory considerations include model risk management under SR 11-7,
        EU AI Act classification requirements, and GDPR data protection obligations.</p>
    </article>
    <footer>Footer content</footer>
    <script>console.log('tracking');</script>
    </body></html>"""

    def _mock_response(self, html=None, status=200, content_type="text/html; charset=utf-8"):
        mock_resp = MagicMock()
        mock_resp.status_code = status
        mock_resp.text = html or self.MOCK_HTML
        mock_resp.headers = {"content-type": content_type}
        return mock_resp

    def test_happy_path_returns_content(self):
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            mock_session.get.return_value = self._mock_response()
            from backend.mcp_server.tools import scrape_content
            result = scrape_content("https://example.com/ai-banking")

        assert result["status"] == "success"
        assert "AI in Banking" in result["content"]
        assert result["char_count"] > 0
        assert result["url"] == "https://example.com/ai-banking"

    def test_nav_footer_script_stripped(self):
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            mock_session.get.return_value = self._mock_response()
            from backend.mcp_server.tools import scrape_content
            result = scrape_content("https://example.com/test")

        assert "Navigation menu" not in result["content"]
        assert "Footer content" not in result["content"]
        assert "console.log" not in result["content"]

    def test_content_capped_at_max_chars(self):
        long_html = "<html><body><article>" + "<p>" + "A" * 500 + "</p>" * 100 + "</article></body></html>"
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            mock_session.get.return_value = self._mock_response(html=long_html)
            from backend.mcp_server.tools import scrape_content
            result = scrape_content("https://example.com/long", max_chars=8000)

        assert result["char_count"] <= 8015  # 8000 + "[...truncated]"
        assert "[...truncated]" in result["content"]

    def test_blocked_domain_raises_without_network_call(self):
        from backend.mcp_server.tools import scrape_content
        from backend.mcp_server.exceptions import ScrapeError
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            with pytest.raises(ScrapeError, match="bot-blocked"):
                scrape_content("https://www.bloomberg.com/article/123")
            mock_session.get.assert_not_called()

    def test_403_raises_scrape_error(self):
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            mock_session.get.return_value = self._mock_response(status=403)
            from backend.mcp_server.tools import scrape_content
            from backend.mcp_server.exceptions import ScrapeError
            with pytest.raises(ScrapeError, match="403"):
                scrape_content("https://example.com/blocked")

    def test_404_raises_scrape_error(self):
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            mock_session.get.return_value = self._mock_response(status=404)
            from backend.mcp_server.tools import scrape_content
            from backend.mcp_server.exceptions import ScrapeError
            with pytest.raises(ScrapeError):
                scrape_content("https://example.com/notfound")

    def test_non_html_raises_scrape_error(self):
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            mock_session.get.return_value = self._mock_response(content_type="application/pdf")
            from backend.mcp_server.tools import scrape_content
            from backend.mcp_server.exceptions import ScrapeError
            with pytest.raises(ScrapeError, match="Non-HTML"):
                scrape_content("https://example.com/doc.pdf")

    def test_timeout_raises_scrape_error(self):
        import requests
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            mock_session.get.side_effect = requests.exceptions.Timeout()
            from backend.mcp_server.tools import scrape_content
            from backend.mcp_server.exceptions import ScrapeError
            with pytest.raises(ScrapeError, match="timeout"):
                scrape_content("https://example.com/slow")

    def test_content_too_short_raises_scrape_error(self):
        short_html = "<html><body><article>Hi</article></body></html>"
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            mock_session.get.return_value = self._mock_response(html=short_html)
            from backend.mcp_server.tools import scrape_content
            from backend.mcp_server.exceptions import ScrapeError
            with pytest.raises(ScrapeError, match="too short"):
                scrape_content("https://example.com/tiny")

    def test_invalid_url_raises_scrape_error(self):
        from backend.mcp_server.tools import scrape_content
        from backend.mcp_server.exceptions import ScrapeError
        with pytest.raises(ScrapeError, match="Invalid URL"):
            scrape_content("not-a-url")

    @pytest.mark.parametrize("domain", [
        "bloomberg.com", "ft.com", "wsj.com", "researchgate.net",
        "spglobal.com", "economist.com", "hbr.org"
    ])
    def test_all_blocked_domains_skipped(self, domain):
        from backend.mcp_server.tools import scrape_content
        from backend.mcp_server.exceptions import ScrapeError
        with patch("backend.mcp_server.tools._SESSION") as mock_session:
            with pytest.raises(ScrapeError):
                scrape_content(f"https://www.{domain}/article/123")
            mock_session.get.assert_not_called()


# ── write_report ───────────────────────────────────────────────────────────────

class TestWriteReport:

    def test_happy_path_writes_file(self, tmp_path):
        with patch("backend.config.cfg.OUTPUT_DIR", str(tmp_path)):
            from backend.mcp_server.tools import write_report
            path = write_report("# Test Report\n\nContent here.", "run-001", "AI Agent Banking")

        assert os.path.exists(path)
        with open(path) as f:
            content = f.read()
        assert "# Test Report" in content

    def test_filename_uses_topic_slug(self, tmp_path):
        with patch("backend.config.cfg.OUTPUT_DIR", str(tmp_path)):
            from backend.mcp_server.tools import write_report
            path = write_report("# Report", "run-001", "AI Agent Adoption for Banking")

        assert "ai_agent_adoption_for_banking" in os.path.basename(path)
        assert path.endswith("_report.md")

    def test_filename_falls_back_to_run_id(self, tmp_path):
        with patch("backend.config.cfg.OUTPUT_DIR", str(tmp_path)):
            from backend.mcp_server.tools import write_report
            path = write_report("# Report", "run-abc-123", "")

        assert "run-abc-123" in os.path.basename(path)

    def test_empty_content_raises_file_write_error(self, tmp_path):
        with patch("backend.config.cfg.OUTPUT_DIR", str(tmp_path)):
            from backend.mcp_server.tools import write_report
            from backend.mcp_server.exceptions import FileWriteError
            with pytest.raises(FileWriteError, match="Empty content"):
                write_report("", "run-001", "test topic")

    def test_whitespace_only_content_raises(self, tmp_path):
        with patch("backend.config.cfg.OUTPUT_DIR", str(tmp_path)):
            from backend.mcp_server.tools import write_report
            from backend.mcp_server.exceptions import FileWriteError
            with pytest.raises(FileWriteError):
                write_report("   \n\n   ", "run-001", "test")

    def test_output_dir_created_if_missing(self, tmp_path):
        new_dir = str(tmp_path / "new_outputs")
        with patch("backend.config.cfg.OUTPUT_DIR", new_dir):
            from backend.mcp_server.tools import write_report
            write_report("# Report", "run-001", "test")
        assert os.path.isdir(new_dir)

    def test_returns_absolute_path(self, tmp_path):
        with patch("backend.config.cfg.OUTPUT_DIR", str(tmp_path)):
            from backend.mcp_server.tools import write_report
            path = write_report("# Report", "run-001", "test topic")
        assert os.path.isabs(path)


# ── save_decision_matrix ───────────────────────────────────────────────────────

class TestSaveDecisionMatrix:

    VALID_MATRIX = [
        {"approach": "Managed AI Platform", "regulatory_compliance": 9,
         "security_privacy": 8, "scalability": 7, "tco": 6,
         "implementation_risk": 7, "vendor_maturity": 8,
         "weighted_total": 7.75, "verdict": "Recommended"},
        {"approach": "Build In-House", "regulatory_compliance": 5,
         "security_privacy": 6, "scalability": 5, "tco": 4,
         "implementation_risk": 4, "vendor_maturity": 4,
         "weighted_total": 5.20, "verdict": "Conditional"},
    ]

    def test_happy_path_writes_csv(self, tmp_path):
        with patch("backend.config.cfg.OUTPUT_DIR", str(tmp_path)):
            from backend.mcp_server.tools import save_decision_matrix
            path = save_decision_matrix(self.VALID_MATRIX, "run-001", "AI Agent Banking")

        assert os.path.exists(path)
        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            rows = list(reader)
        assert len(rows) == 2
        assert rows[0]["approach"] == "Managed AI Platform"

    def test_filename_uses_topic_slug(self, tmp_path):
        with patch("backend.config.cfg.OUTPUT_DIR", str(tmp_path)):
            from backend.mcp_server.tools import save_decision_matrix
            path = save_decision_matrix(self.VALID_MATRIX, "run-001", "AI Agent Adoption for Banking")

        assert "ai_agent_adoption_for_banking" in os.path.basename(path)
        assert path.endswith("_matrix.csv")

    def test_empty_matrix_raises_file_write_error(self, tmp_path):
        with patch("backend.config.cfg.OUTPUT_DIR", str(tmp_path)):
            from backend.mcp_server.tools import save_decision_matrix
            from backend.mcp_server.exceptions import FileWriteError
            with pytest.raises(FileWriteError, match="Empty matrix"):
                save_decision_matrix([], "run-001", "test")

    def test_inconsistent_keys_raises_file_write_error(self, tmp_path):
        bad_matrix = [
            {"approach": "A", "score": 7},
            {"approach": "B", "rating": 8},  # different key
        ]
        with patch("backend.config.cfg.OUTPUT_DIR", str(tmp_path)):
            from backend.mcp_server.tools import save_decision_matrix
            from backend.mcp_server.exceptions import FileWriteError
            with pytest.raises(FileWriteError, match="inconsistent keys"):
                save_decision_matrix(bad_matrix, "run-001", "test")

    def test_all_columns_written(self, tmp_path):
        with patch("backend.config.cfg.OUTPUT_DIR", str(tmp_path)):
            from backend.mcp_server.tools import save_decision_matrix
            path = save_decision_matrix(self.VALID_MATRIX, "run-001", "test")

        with open(path, newline="") as f:
            reader = csv.DictReader(f)
            fieldnames = reader.fieldnames

        expected_fields = {"approach", "regulatory_compliance", "security_privacy",
                           "scalability", "tco", "implementation_risk", "vendor_maturity",
                           "weighted_total", "verdict"}
        assert expected_fields.issubset(set(fieldnames))


# ── make_file_slug ─────────────────────────────────────────────────────────────

class TestMakeFileSlug:

    @pytest.mark.parametrize("topic,expected", [
        ("AI Agent Adoption for Banking",    "ai_agent_adoption_for_banking"),
        ("GLP-1 Weight Loss Market 2026",    "glp_1_weight_loss_market_2026"),
        ("Green Hydrogen EU Investment",     "green_hydrogen_eu_investment"),
        ("  spaces  around  ",              "spaces_around"),
        ("Special!@#$%Characters",          "specialcharacters"),
        ("UPPER CASE TOPIC",                "upper_case_topic"),
    ])
    def test_slug_generation(self, topic, expected):
        from backend.mcp_server.tools import make_file_slug
        assert make_file_slug(topic, "fallback-id") == expected

    def test_empty_topic_falls_back_to_run_id(self):
        from backend.mcp_server.tools import make_file_slug
        assert make_file_slug("", "run-abc-123") == "run-abc-123"

    def test_none_like_topic_falls_back(self):
        from backend.mcp_server.tools import make_file_slug
        assert make_file_slug("   ", "run-abc-123") == "run-abc-123"

    def test_slug_max_length_50(self):
        from backend.mcp_server.tools import make_file_slug
        long_topic = "A Very Long Topic Name That Goes Way Beyond Fifty Characters In Total Length"
        slug = make_file_slug(long_topic, "fallback")
        assert len(slug) <= 50

    def test_slug_no_trailing_underscores(self):
        from backend.mcp_server.tools import make_file_slug
        slug = make_file_slug("Topic!", "fallback")
        assert not slug.startswith("_")
        assert not slug.endswith("_")
