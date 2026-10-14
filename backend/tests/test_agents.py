"""
test_agents.py — Agent tests: happy path + adversarial for all 7 agents.

All LLM calls are mocked via unittest.mock.patch so tests run offline
with no API keys required.

Run:
    pytest backend/tests/test_agents.py -v
    pytest backend/tests/test_agents.py -v --tb=short
"""
import pytest
from unittest.mock import patch, MagicMock


# ── Fixtures ───────────────────────────────────────────────────────────────────

@pytest.fixture
def base_state():
    """Minimal valid AnalysisState for testing."""
    return {
        "topic":               "AI Agent Adoption for Banking",
        "industry":            "Banking / Financial Services",
        "run_id":              "test-run-001",
        "sources":             [],
        "extracted_content":   "",
        "extraction_metadata": [],
        "analysis":            "",
        "risks":               [],
        "decision_matrix":     [],
        "report":              "",
        "report_path":         "",
        "matrix_path":         "",
        "qa_score":            0.0,
        "qa_feedback":         "",
        "qa_pass":             False,
        "status":              "running",
        "current_agent":       "",
        "iteration_count":     0,
        "error":               None,
        "provider_used":       "",
        "error_count":         0,
    }


@pytest.fixture
def state_with_sources(base_state):
    base_state["sources"] = [
        {"title": "AI Banking Compliance 2026", "url": "https://example.com/ai-banking",
         "summary": "AI agents are transforming banking compliance...", "regulatory_flag": True, "relevance_score": 8.5},
        {"title": "Agentic AI Financial Services", "url": "https://example.com/agentic",
         "summary": "Financial institutions are adopting agentic AI...", "regulatory_flag": False, "relevance_score": 7.5},
    ]
    return base_state


@pytest.fixture
def state_with_content(state_with_sources):
    state_with_sources["extracted_content"] = (
        "AI agents are transforming banking. "
        "Regulatory frameworks like DORA and SR 11-7 apply. "
        "Implementation requires careful governance. " * 50
    )
    return state_with_sources


@pytest.fixture
def state_with_analysis(state_with_content):
    state_with_content["analysis"] = (
        "## Key Trends\nAI agent adoption is accelerating in banking.\n\n"
        "## Architectural Patterns\nMulti-agent systems with human oversight.\n\n"
        "## Regulatory Implications\nDORA Article 28 applies to third-party AI vendors.\n\n"
        "## Strategic Implications\nEarly movers gain competitive advantage."
    )
    return state_with_content


@pytest.fixture
def state_with_risks(state_with_analysis):
    state_with_analysis["risks"] = [
        {"name": "Regulatory Non-Compliance Risk", "category": "Regulatory",
         "level": "Critical", "impact": "Heavy fines under DORA.",
         "mitigation": "Implement DORA-compliant AI governance framework.",
         "owner": "CRO", "regulatory_ref": "DORA Article 28"},
        {"name": "Model Hallucination in Decisions", "category": "Technology",
         "level": "High", "impact": "AI makes incorrect financial decisions.",
         "mitigation": "Mandatory human review for decisions above $10k threshold.",
         "owner": "CTO", "regulatory_ref": "SR 11-7"},
    ]
    return state_with_analysis


@pytest.fixture
def state_with_matrix(state_with_risks):
    state_with_risks["decision_matrix"] = [
        {"approach": "Managed AI Platform", "regulatory_compliance": 9, "security_privacy": 8,
         "scalability": 7, "tco": 6, "implementation_risk": 7, "vendor_maturity": 8,
         "weighted_total": 7.75, "verdict": "Recommended"},
        {"approach": "Build In-House", "regulatory_compliance": 6, "security_privacy": 7,
         "scalability": 5, "tco": 3, "implementation_risk": 4, "vendor_maturity": 5,
         "weighted_total": 5.20, "verdict": "Conditional"},
    ]
    return state_with_risks


@pytest.fixture
def state_with_report(state_with_matrix):
    state_with_matrix["report"] = (
        "## Executive Summary\nAI agent adoption presents significant opportunity...\n\n"
        "## Key Findings\n1. DORA compliance is mandatory [1]\n\n"
        "## Risk Register\n| Risk | Level |\n|---|---|\n| Regulatory | Critical |\n\n"
        "## Decision Matrix\n| Approach | Total |\n|---|---|\n| Managed AI | 7.75 |\n\n"
        "## Recommendations\n1. Deploy managed AI platform with DORA compliance framework.\n\n"
        "## Sources\n1. [AI Banking Compliance 2026](https://example.com/ai-banking)"
    )
    return state_with_matrix


MOCK_LLM_RESPONSE = "This is a valid LLM response with sufficient content for testing purposes."


# ── Agent 1: Research Agent ────────────────────────────────────────────────────

class TestResearchAgent:

    def test_happy_path_returns_sources(self, base_state):
        mock_sources = [
            {"title": "AI Banking", "url": "https://example.com", "summary": "...",
             "regulatory_flag": True, "relevance_score": 8.5}
        ]
        with patch("backend.mcp_server.tools.search_sources", return_value=mock_sources), \
             patch("backend.groq_fallback.groq_call_with_fallback", return_value="Source 1 is highly relevant."):
            from backend.agents import research_agent
            result = research_agent.run(base_state)

        assert "sources" in result
        assert len(result["sources"]) > 0
        assert result["current_agent"] == "research_node"
        assert result.get("status") != "error"

    def test_empty_topic_returns_error(self, base_state):
        base_state["topic"] = ""
        from backend.agents import research_agent
        result = research_agent.run(base_state)
        # Should handle gracefully — either empty sources or error state
        assert "sources" in result or result.get("status") == "error"

    def test_ddg_failure_handled_gracefully(self, base_state):
        from backend.mcp_server.exceptions import SearchError
        with patch("backend.mcp_server.tools.search_sources", side_effect=SearchError("DuckDuckGo failed")):
            from backend.agents import research_agent
            result = research_agent.run(base_state)
        # Pipeline should continue with empty sources, not crash
        assert "sources" in result
        assert result.get("status") != "error" or result.get("error") is not None

    def test_llm_exhausted_returns_error(self, base_state):
        from backend.groq_fallback import ModelExhaustedError
        mock_sources = [{"title": "T", "url": "https://ex.com", "summary": "s",
                          "regulatory_flag": False, "relevance_score": 7.5}]
        with patch("backend.mcp_server.tools.search_sources", return_value=mock_sources), \
             patch("backend.groq_fallback.groq_call_with_fallback",
                   side_effect=ModelExhaustedError("All models exhausted")):
            from backend.agents import research_agent
            result = research_agent.run(base_state)
        assert result.get("status") == "error"
        assert "exhausted" in result.get("error", "").lower() or result.get("error") is not None

    def test_regulatory_sources_scored_higher(self, base_state):
        mock_sources = [
            {"title": "DORA Compliance Guide", "url": "https://ex.com/dora",
             "summary": "DORA regulation compliance framework...",
             "regulatory_flag": True, "relevance_score": 8.5},
            {"title": "General AI Article", "url": "https://ex.com/ai",
             "summary": "AI trends in technology...",
             "regulatory_flag": False, "relevance_score": 7.5},
        ]
        with patch("backend.mcp_server.tools.search_sources", return_value=mock_sources), \
             patch("backend.groq_fallback.groq_call_with_fallback", return_value="Filtered sources."):
            from backend.agents import research_agent
            result = research_agent.run(base_state)
        # Regulatory sources should be preferred
        if result.get("sources"):
            scores = [s.get("relevance_score", 0) for s in result["sources"]]
            assert max(scores) >= 8.5


# ── Agent 2: Extraction Agent ──────────────────────────────────────────────────

class TestExtractionAgent:

    def test_happy_path_scrapes_all_urls(self, state_with_sources):
        mock_content = {"url": "https://example.com/ai-banking",
                        "content": "AI agent content here " * 100,
                        "char_count": 2200, "status": "success"}
        with patch("backend.mcp_server.tools.scrape_content", return_value=mock_content):
            from backend.agents import extraction_agent
            result = extraction_agent.run(state_with_sources)

        assert "extracted_content" in result
        assert len(result["extracted_content"]) > 0
        assert result["current_agent"] == "extractor_node"

    def test_empty_sources_returns_empty_content(self, base_state):
        from backend.agents import extraction_agent
        result = extraction_agent.run(base_state)
        assert result["extracted_content"] == ""
        assert result["current_agent"] == "extractor_node"

    def test_partial_scrape_failure_continues(self, state_with_sources):
        from backend.mcp_server.exceptions import ScrapeError

        def scrape_side_effect(url, **kwargs):
            if "ai-banking" in url:
                return {"url": url, "content": "Valid content " * 200,
                        "char_count": 2800, "status": "success"}
            raise ScrapeError("403 bot-blocked")

        with patch("backend.mcp_server.tools.scrape_content", side_effect=scrape_side_effect):
            from backend.agents import extraction_agent
            result = extraction_agent.run(state_with_sources)

        assert len(result["extracted_content"]) > 0
        assert result.get("status") != "error"
        metadata = result.get("extraction_metadata", [])
        statuses = [m.get("status") for m in metadata]
        assert "success" in statuses
        assert "failed" in statuses

    def test_all_scrapes_fail_returns_empty(self, state_with_sources):
        from backend.mcp_server.exceptions import ScrapeError
        with patch("backend.mcp_server.tools.scrape_content", side_effect=ScrapeError("blocked")):
            from backend.agents import extraction_agent
            result = extraction_agent.run(state_with_sources)
        assert result["extracted_content"] == ""
        assert result.get("status") != "error"  # extraction failure is non-fatal

    def test_metadata_tracks_success_and_failure(self, state_with_sources):
        from backend.mcp_server.exceptions import ScrapeError

        def mixed_scrape(url, **kwargs):
            if "ai-banking" in url:
                return {"url": url, "content": "X" * 500, "char_count": 500, "status": "success"}
            raise ScrapeError("failed")

        with patch("backend.mcp_server.tools.scrape_content", side_effect=mixed_scrape):
            from backend.agents import extraction_agent
            result = extraction_agent.run(state_with_sources)

        metadata = result.get("extraction_metadata", [])
        assert len(metadata) == len(state_with_sources["sources"])


# ── Agent 3: Analysis Agent ────────────────────────────────────────────────────

class TestAnalysisAgent:

    GOOD_ANALYSIS = (
        "## Key Trends\nAI adoption accelerating in tier-1 banks.\n\n"
        "## Architectural Patterns\nMulti-agent orchestration with LangGraph.\n\n"
        "## Regulatory Implications\nDORA Article 28 mandates third-party AI oversight.\n\n"
        "## Strategic Implications\nFirst movers gain 18-month competitive advantage."
    )

    def test_happy_path_returns_analysis(self, state_with_content):
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=self.GOOD_ANALYSIS):
            from backend.agents import analysis_agent
            result = analysis_agent.run(state_with_content)

        assert "analysis" in result
        assert len(result["analysis"]) > 100
        assert result["current_agent"] == "analysis_node"
        assert result.get("status") != "error"

    def test_empty_content_returns_insufficient(self, base_state):
        from backend.agents import analysis_agent
        result = analysis_agent.run(base_state)
        assert "Insufficient" in result["analysis"]
        assert result.get("status") != "error"

    def test_qa_feedback_injected_on_iteration(self, state_with_content):
        state_with_content["iteration_count"] = 1
        state_with_content["qa_feedback"] = "Missing regulatory citations. Add DORA references."
        captured = []

        def capture_messages(client, messages, **kwargs):
            captured.append(messages)
            return self.GOOD_ANALYSIS

        with patch("backend.groq_fallback.groq_call_with_fallback", side_effect=capture_messages):
            from backend.agents import analysis_agent
            analysis_agent.run(state_with_content)

        assert len(captured) > 0
        user_msg = captured[0][-1]["content"]
        assert "QA FEEDBACK" in user_msg
        assert "DORA references" in user_msg

    def test_llm_exhausted_sets_error_status(self, state_with_content):
        from backend.groq_fallback import ModelExhaustedError
        with patch("backend.groq_fallback.groq_call_with_fallback",
                   side_effect=ModelExhaustedError("All models exhausted")):
            from backend.agents import analysis_agent
            result = analysis_agent.run(state_with_content)
        assert result["status"] == "error"
        assert result["current_agent"] == "analysis_node"
        assert result["error"] is not None

    def test_content_truncated_to_8000_chars(self, state_with_content):
        state_with_content["extracted_content"] = "X" * 20000
        captured = []

        def capture(client, messages, **kwargs):
            captured.append(messages)
            return self.GOOD_ANALYSIS

        with patch("backend.groq_fallback.groq_call_with_fallback", side_effect=capture):
            from backend.agents import analysis_agent
            analysis_agent.run(state_with_content)

        user_content = captured[0][-1]["content"]
        # Content in message should not exceed 8000 chars (plus topic/industry prefix)
        assert len(user_content) < 9000


# ── Agent 4: Risk Agent ────────────────────────────────────────────────────────

class TestRiskAgent:

    VALID_RISKS_JSON = '''[
        {"name": "Regulatory Non-Compliance Risk", "category": "Regulatory",
         "level": "Critical", "impact": "Heavy fines under DORA.",
         "mitigation": "Implement DORA Article 28 compliant AI governance.",
         "owner": "CRO", "regulatory_ref": "DORA Article 28"},
        {"name": "Model Hallucination in Decisions", "category": "Technology",
         "level": "High", "impact": "Incorrect financial decisions.",
         "mitigation": "Human review for decisions above $10k.",
         "owner": "CTO", "regulatory_ref": "SR 11-7"}
    ]'''

    def test_happy_path_returns_risks(self, state_with_analysis):
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=self.VALID_RISKS_JSON):
            from backend.agents import risk_agent
            result = risk_agent.run(state_with_analysis)

        assert "risks" in result
        assert len(result["risks"]) == 2
        assert result["current_agent"] == "risk_node"
        assert result["risks"][0]["name"] == "Regulatory Non-Compliance Risk"

    def test_empty_analysis_returns_empty_risks(self, base_state):
        from backend.agents import risk_agent
        result = risk_agent.run(base_state)
        assert result["risks"] == []
        assert result["current_agent"] == "risk_node"

    def test_invalid_level_defaults_to_medium(self, state_with_analysis):
        bad_json = '[{"name": "Test Risk", "category": "Regulatory", "level": "EXTREME", "impact": "x", "mitigation": "y", "owner": "CRO", "regulatory_ref": "N/A"}]'
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=bad_json):
            from backend.agents import risk_agent
            result = risk_agent.run(state_with_analysis)
        assert result["risks"][0]["level"] == "Medium"

    def test_invalid_category_defaults_to_operational(self, state_with_analysis):
        bad_json = '[{"name": "Test Risk", "category": "UNKNOWN", "level": "High", "impact": "x", "mitigation": "y", "owner": "CRO", "regulatory_ref": "N/A"}]'
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=bad_json):
            from backend.agents import risk_agent
            result = risk_agent.run(state_with_analysis)
        assert result["risks"][0]["category"] == "Operational"

    def test_dict_wrapped_response_unwrapped(self, state_with_analysis):
        wrapped = '{"risks": [{"name": "Wrapped Risk", "category": "Technology", "level": "High", "impact": "x", "mitigation": "y", "owner": "CTO", "regulatory_ref": "N/A"}]}'
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=wrapped):
            from backend.agents import risk_agent
            result = risk_agent.run(state_with_analysis)
        assert len(result["risks"]) == 1
        assert result["risks"][0]["name"] == "Wrapped Risk"

    def test_llm_exhausted_sets_error(self, state_with_analysis):
        from backend.groq_fallback import ModelExhaustedError
        with patch("backend.groq_fallback.groq_call_with_fallback",
                   side_effect=ModelExhaustedError("exhausted")):
            from backend.agents import risk_agent
            result = risk_agent.run(state_with_analysis)
        assert result["status"] == "error"

    def test_plain_string_response_returns_empty(self, state_with_analysis):
        with patch("backend.groq_fallback.groq_call_with_fallback",
                   return_value="I cannot generate risks for this topic."):
            from backend.agents import risk_agent
            result = risk_agent.run(state_with_analysis)
        assert result["risks"] == []


# ── Agent 5: Scoring Agent ─────────────────────────────────────────────────────

class TestScoringAgent:

    VALID_MATRIX_JSON = '''[
        {"approach": "Managed AI Platform", "regulatory_compliance": 9,
         "security_privacy": 8, "scalability": 7, "tco": 6,
         "implementation_risk": 7, "vendor_maturity": 8,
         "weighted_total": 0, "verdict": "Recommended"},
        {"approach": "Build In-House", "regulatory_compliance": 5,
         "security_privacy": 6, "scalability": 5, "tco": 4,
         "implementation_risk": 4, "vendor_maturity": 4,
         "weighted_total": 0, "verdict": "Conditional"}
    ]'''

    def test_happy_path_returns_matrix(self, state_with_risks):
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=self.VALID_MATRIX_JSON):
            from backend.agents import scoring_agent
            result = scoring_agent.run(state_with_risks)

        assert "decision_matrix" in result
        assert len(result["decision_matrix"]) == 2
        assert result["current_agent"] == "scoring_node"

    def test_weighted_total_recalculated_server_side(self, state_with_risks):
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=self.VALID_MATRIX_JSON):
            from backend.agents import scoring_agent
            result = scoring_agent.run(state_with_risks)

        # weighted_total should be recalculated, not 0
        totals = [r["weighted_total"] for r in result["decision_matrix"]]
        assert all(t > 0 for t in totals)

    def test_matrix_sorted_by_weighted_total_descending(self, state_with_risks):
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=self.VALID_MATRIX_JSON):
            from backend.agents import scoring_agent
            result = scoring_agent.run(state_with_risks)

        totals = [r["weighted_total"] for r in result["decision_matrix"]]
        assert totals == sorted(totals, reverse=True)

    def test_invalid_verdict_defaults_to_conditional(self, state_with_risks):
        bad_json = '[{"approach": "Test", "regulatory_compliance": 7, "security_privacy": 7, "scalability": 7, "tco": 7, "implementation_risk": 7, "vendor_maturity": 7, "weighted_total": 0, "verdict": "INVALID"}]'
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=bad_json):
            from backend.agents import scoring_agent
            result = scoring_agent.run(state_with_risks)
        assert result["decision_matrix"][0]["verdict"] == "Conditional"

    def test_llm_exhausted_sets_error(self, state_with_risks):
        from backend.groq_fallback import ModelExhaustedError
        with patch("backend.groq_fallback.groq_call_with_fallback",
                   side_effect=ModelExhaustedError("exhausted")):
            from backend.agents import scoring_agent
            result = scoring_agent.run(state_with_risks)
        assert result["status"] == "error"

    def test_dict_wrapped_response_unwrapped(self, state_with_risks):
        wrapped = '{"approaches": [{"approach": "Test", "regulatory_compliance": 8, "security_privacy": 7, "scalability": 6, "tco": 5, "implementation_risk": 6, "vendor_maturity": 7, "weighted_total": 0, "verdict": "Recommended"}]}'
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=wrapped):
            from backend.agents import scoring_agent
            result = scoring_agent.run(state_with_risks)
        assert len(result["decision_matrix"]) == 1


# ── Agent 6: Report Writer ─────────────────────────────────────────────────────

class TestReportWriterAgent:

    GOOD_REPORT = (
        "## Executive Summary\n"
        "AI agent adoption in banking presents significant opportunities balanced by regulatory risk. "
        "Recommended approach: Managed AI Platform with DORA-compliant governance. "
        "Key risk: regulatory non-compliance. Expected outcome: 20% operational cost reduction.\n\n"
        "## Key Findings\n"
        "1. DORA Article 28 mandates AI vendor oversight [1]\n"
        "2. Multi-agent systems require human-in-the-loop for high-value decisions [2]\n\n"
        "## Risk Register\n"
        "| Risk | Category | Level | Mitigation | Owner | Regulatory Ref |\n"
        "|------|----------|-------|-----------|-------|----------------|\n"
        "| Regulatory Non-Compliance | Regulatory | Critical | DORA framework | CRO | DORA Art 28 |\n\n"
        "## Decision Matrix\n"
        "| Approach | Total | Verdict |\n"
        "|---------|-------|--------|\n"
        "| Managed AI Platform | 7.75 | Recommended |\n\n"
        "## Recommendations\n"
        "1. Deploy managed AI platform. Owner: CTO. 90-day step: vendor RFP.\n\n"
        "## Sources\n"
        "1. [AI Banking Compliance 2026](https://example.com/ai-banking)\n"
    )

    def test_happy_path_returns_report(self, state_with_matrix):
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=self.GOOD_REPORT), \
             patch("backend.mcp_server.tools.write_report", return_value="/outputs/test_report.md"), \
             patch("backend.mcp_server.tools.save_decision_matrix", return_value="/outputs/test_matrix.csv"):
            from backend.agents import report_writer_agent
            result = report_writer_agent.run(state_with_matrix)

        assert "report" in result
        assert len(result["report"]) > 100
        assert result["current_agent"] == "report_node"
        assert result.get("status") != "error"

    def test_report_written_to_file(self, state_with_matrix):
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=self.GOOD_REPORT) as mock_llm, \
             patch("backend.mcp_server.tools.write_report", return_value="/outputs/report.md") as mock_write, \
             patch("backend.mcp_server.tools.save_decision_matrix", return_value="/outputs/matrix.csv"):
            from backend.agents import report_writer_agent
            result = report_writer_agent.run(state_with_matrix)

        mock_write.assert_called_once()
        call_args = mock_write.call_args
        assert call_args[0][1] == state_with_matrix["run_id"]       # run_id passed
        assert call_args[0][2] == state_with_matrix["topic"]        # topic passed

    def test_source_list_capped_at_15(self, state_with_matrix):
        state_with_matrix["sources"] = [
            {"title": f"Source {i}", "url": f"https://ex.com/{i}", "summary": "..."}
            for i in range(25)
        ]
        captured = []

        def capture(client, messages, **kwargs):
            captured.append(messages)
            return self.GOOD_REPORT

        with patch("backend.groq_fallback.groq_call_with_fallback", side_effect=capture), \
             patch("backend.mcp_server.tools.write_report", return_value="/out/r.md"), \
             patch("backend.mcp_server.tools.save_decision_matrix", return_value="/out/m.csv"):
            from backend.agents import report_writer_agent
            report_writer_agent.run(state_with_matrix)

        user_msg = captured[0][-1]["content"]
        # Should have at most 15 numbered source lines
        source_lines = [l for l in user_msg.split("\n") if l.strip().startswith(tuple("123456789"))]
        assert len(source_lines) <= 15

    def test_file_write_error_sets_error_status(self, state_with_matrix):
        from backend.mcp_server.exceptions import FileWriteError
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=self.GOOD_REPORT), \
             patch("backend.mcp_server.tools.write_report", side_effect=FileWriteError("disk full")):
            from backend.agents import report_writer_agent
            result = report_writer_agent.run(state_with_matrix)
        assert result["status"] == "error"

    def test_llm_exhausted_sets_error(self, state_with_matrix):
        from backend.groq_fallback import ModelExhaustedError
        with patch("backend.groq_fallback.groq_call_with_fallback",
                   side_effect=ModelExhaustedError("exhausted")):
            from backend.agents import report_writer_agent
            result = report_writer_agent.run(state_with_matrix)
        assert result["status"] == "error"


# ── Agent 7: QA Agent ──────────────────────────────────────────────────────────

class TestQAAgent:

    PASS_RESPONSE = "Score: 8.5\nFeedback: Report meets all quality criteria. Well-sourced with clear regulatory references."
    FAIL_RESPONSE = "Score: 5.0\nFeedback: Missing regulatory citations in Key Findings. Executive Summary exceeds 200 words."

    def test_happy_path_pass(self, state_with_report):
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=self.PASS_RESPONSE):
            from backend.agents import qa_agent
            result = qa_agent.run(state_with_report)

        assert "qa_score" in result
        assert result["qa_score"] >= 7.0
        assert result["qa_pass"] is True
        assert result["current_agent"] == "qa_node"

    def test_failing_score_sets_qa_pass_false(self, state_with_report):
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=self.FAIL_RESPONSE):
            from backend.agents import qa_agent
            result = qa_agent.run(state_with_report)

        assert result["qa_score"] < 7.0
        assert result["qa_pass"] is False

    def test_iteration_count_incremented_in_return(self, state_with_report):
        state_with_report["iteration_count"] = 0
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=self.FAIL_RESPONSE):
            from backend.agents import qa_agent
            result = qa_agent.run(state_with_report)
        assert result["iteration_count"] == 1

    def test_feedback_returned_on_fail(self, state_with_report):
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value=self.FAIL_RESPONSE):
            from backend.agents import qa_agent
            result = qa_agent.run(state_with_report)
        assert len(result.get("qa_feedback", "")) > 0

    def test_llm_exhausted_sets_error(self, state_with_report):
        from backend.groq_fallback import ModelExhaustedError
        with patch("backend.groq_fallback.groq_call_with_fallback",
                   side_effect=ModelExhaustedError("exhausted")):
            from backend.agents import qa_agent
            result = qa_agent.run(state_with_report)
        assert result["status"] == "error"
        assert result["current_agent"] == "qa_node"

    def test_empty_report_handled(self, base_state):
        with patch("backend.groq_fallback.groq_call_with_fallback", return_value="Score: 1.0\nFeedback: Empty report."):
            from backend.agents import qa_agent
            result = qa_agent.run(base_state)
        assert result["qa_score"] <= 3.0


# ── Workflow / Graph Integration ───────────────────────────────────────────────

class TestWorkflowRouting:

    def test_route_or_error_continues_on_success(self):
        from backend.workflow import _route_or_error
        route_fn = _route_or_error("next_node")
        assert route_fn({"status": "running"}) == "next_node"

    def test_route_or_error_diverts_on_error(self):
        from backend.workflow import _route_or_error
        route_fn = _route_or_error("next_node")
        assert route_fn({"status": "error"}) == "error_node"

    def test_qa_routing_passes_above_threshold(self):
        from backend.workflow import qa_routing
        state = {"qa_score": 8.0, "iteration_count": 0, "status": "running"}
        assert qa_routing(state) == "END"

    def test_qa_routing_loops_below_threshold(self):
        from backend.workflow import qa_routing
        state = {"qa_score": 5.0, "iteration_count": 0, "status": "running"}
        assert qa_routing(state) == "analysis_node"

    def test_qa_routing_stops_at_max_iterations(self):
        from backend.workflow import qa_routing
        from backend.config import cfg
        state = {"qa_score": 5.0, "iteration_count": cfg.MAX_QA_ITERATIONS, "status": "running"}
        assert qa_routing(state) == "END"

    def test_qa_routing_error_state_to_error_node(self):
        from backend.workflow import qa_routing
        state = {"qa_score": 0.0, "iteration_count": 0, "status": "error"}
        assert qa_routing(state) == "error_node"
