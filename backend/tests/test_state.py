"""
test_state.py — tests for backend/state.py (AnalysisState + initial_state)
"""
from backend.state import initial_state, AnalysisState


class TestInitialState:
    def test_returns_dict(self):
        state = initial_state()
        assert isinstance(state, dict)

    def test_topic_stored(self):
        state = initial_state(topic="AI Banking")
        assert state["topic"] == "AI Banking"

    def test_industry_stored(self):
        state = initial_state(industry="Insurance")
        assert state["industry"] == "Insurance"

    def test_run_id_stored(self):
        state = initial_state(run_id="abc-123")
        assert state["run_id"] == "abc-123"

    def test_defaults_empty_strings(self):
        state = initial_state()
        assert state["topic"] == ""
        assert state["industry"] == ""
        assert state["run_id"] == ""

    def test_sources_empty_list(self):
        state = initial_state()
        assert state["sources"] == []

    def test_risks_empty_list(self):
        state = initial_state()
        assert state["risks"] == []

    def test_decision_matrix_empty_list(self):
        state = initial_state()
        assert state["decision_matrix"] == []

    def test_extraction_metadata_empty_list(self):
        state = initial_state()
        assert state["extraction_metadata"] == []

    def test_qa_score_zero(self):
        state = initial_state()
        assert state["qa_score"] == 0.0

    def test_qa_pass_false(self):
        state = initial_state()
        assert state["qa_pass"] is False

    def test_status_initialized(self):
        state = initial_state()
        assert state["status"] == "initialized"

    def test_iteration_count_zero(self):
        state = initial_state()
        assert state["iteration_count"] == 0

    def test_error_none(self):
        state = initial_state()
        assert state["error"] is None

    def test_all_required_keys_present(self):
        state = initial_state()
        expected_keys = {
            "topic", "industry", "run_id", "sources", "extracted_content",
            "extraction_metadata", "analysis", "risks", "decision_matrix",
            "report", "report_path", "matrix_path", "qa_score", "qa_feedback",
            "qa_pass", "status", "current_agent", "iteration_count", "error",
        }
        assert expected_keys.issubset(state.keys())

    def test_independent_lists_not_shared(self):
        # Mutable defaults must not be shared across calls
        s1 = initial_state()
        s2 = initial_state()
        s1["sources"].append({"url": "https://example.com"})
        assert s2["sources"] == []
