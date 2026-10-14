"""
test_main.py — integration tests for the FastAPI app in backend/main.py.

Uses TestClient (synchronous) so no real uvicorn is required.
The LangGraph pipeline (_run_pipeline) is always mocked — these tests
cover HTTP routing, validation, and registry logic, not agent behaviour.
"""
import os
import pytest
from unittest.mock import patch, MagicMock
from fastapi.testclient import TestClient

from backend.main import app, run_registry
from backend.config import cfg

client = TestClient(app)


@pytest.fixture(autouse=True)
def clear_registry():
    """Reset the in-memory run registry before every test."""
    run_registry.clear()
    yield
    run_registry.clear()


# ---------------------------------------------------------------------------
# GET /health
# ---------------------------------------------------------------------------

class TestHealth:
    def test_returns_200(self):
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_contains_llm_key(self):
        resp = client.get("/health")
        assert "llm" in resp.json()

    def test_contains_search_key(self):
        resp = client.get("/health")
        assert "search" in resp.json()

    def test_llm_value_contains_groq(self):
        resp = client.get("/health")
        assert "Groq" in resp.json()["llm"]


# ---------------------------------------------------------------------------
# POST /run-analysis
# ---------------------------------------------------------------------------

class TestRunAnalysis:
    def _post(self, body):
        return client.post("/run-analysis", json=body)

    def test_returns_202(self):
        with patch("backend.main._run_pipeline"):
            resp = self._post({"topic": "AI Banking"})
        assert resp.status_code == 202

    def test_returns_run_id(self):
        with patch("backend.main._run_pipeline"):
            resp = self._post({"topic": "AI Banking"})
        assert "run_id" in resp.json()

    def test_returns_poll_url(self):
        with patch("backend.main._run_pipeline"):
            resp = self._post({"topic": "AI Banking"})
        assert "poll_url" in resp.json()

    def test_poll_url_contains_run_id(self):
        with patch("backend.main._run_pipeline"):
            resp = self._post({"topic": "AI Banking"})
        data = resp.json()
        assert data["run_id"] in data["poll_url"]

    def test_empty_topic_returns_422(self):
        resp = self._post({"topic": ""})
        assert resp.status_code == 422

    def test_whitespace_topic_returns_422(self):
        resp = self._post({"topic": "   "})
        assert resp.status_code == 422

    def test_missing_topic_returns_422(self):
        resp = self._post({})
        assert resp.status_code == 422

    def test_default_industry_used(self):
        captured = {}
        def fake_pipeline(run_id, topic, industry):
            captured["industry"] = industry
        with patch("backend.main._run_pipeline", side_effect=fake_pipeline):
            self._post({"topic": "AI Banking"})
        # BackgroundTask runs synchronously in TestClient
        assert "Banking" in captured.get("industry", "")

    def test_custom_industry_passed_through(self):
        captured = {}
        def fake_pipeline(run_id, topic, industry):
            captured["industry"] = industry
        with patch("backend.main._run_pipeline", side_effect=fake_pipeline):
            self._post({"topic": "AI", "industry": "Insurance"})
        assert captured.get("industry") == "Insurance"


# ---------------------------------------------------------------------------
# GET /status/{run_id}
# ---------------------------------------------------------------------------

class TestStatus:
    def test_unknown_run_id_returns_404(self):
        resp = client.get("/status/nonexistent-id")
        assert resp.status_code == 404

    def test_known_run_id_returns_200(self):
        run_registry["abc"] = {"status": "running", "current_agent": "research_node"}
        resp = client.get("/status/abc")
        assert resp.status_code == 200

    def test_response_contains_status(self):
        run_registry["abc"] = {"status": "running", "current_agent": "research_node"}
        resp = client.get("/status/abc")
        assert "status" in resp.json()

    def test_response_contains_current_agent(self):
        run_registry["abc"] = {"status": "running", "current_agent": "research_node"}
        resp = client.get("/status/abc")
        assert "current_agent" in resp.json()

    def test_response_contains_run_id(self):
        run_registry["abc"] = {"status": "running", "current_agent": "research_node"}
        resp = client.get("/status/abc")
        assert resp.json()["run_id"] == "abc"


# ---------------------------------------------------------------------------
# GET /results/{run_id}
# ---------------------------------------------------------------------------

class TestResults:
    def test_unknown_run_id_returns_404(self):
        resp = client.get("/results/nonexistent")
        assert resp.status_code == 404

    def test_still_running_returns_409(self):
        run_registry["abc"] = {"status": "running", "current_agent": "analysis_node"}
        resp = client.get("/results/abc")
        assert resp.status_code == 409

    def test_completed_run_returns_200(self):
        run_registry["abc"] = {"status": "completed", "report": "# Done", "current_agent": "qa_node"}
        resp = client.get("/results/abc")
        assert resp.status_code == 200

    def test_completed_run_returns_full_state(self):
        state = {"status": "completed", "report": "# Done", "current_agent": "qa_node"}
        run_registry["abc"] = state
        resp = client.get("/results/abc")
        assert resp.json()["report"] == "# Done"

    def test_error_run_still_returns_200_with_state(self):
        run_registry["abc"] = {"status": "error", "error": "LLM down", "current_agent": ""}
        resp = client.get("/results/abc")
        assert resp.status_code == 200
        assert resp.json()["status"] == "error"


# ---------------------------------------------------------------------------
# GET /download/{run_id}/{filename}
# ---------------------------------------------------------------------------

class TestDownload:
    def test_invalid_filename_returns_400(self, tmp_path):
        resp = client.get("/download/abc/../../etc/passwd")
        assert resp.status_code in (400, 404)

    def test_unknown_filename_returns_400(self):
        resp = client.get("/download/abc/abc_other.txt")
        assert resp.status_code == 400

    def test_valid_report_filename_not_found_returns_404(self, monkeypatch):
        monkeypatch.setattr(cfg, "OUTPUT_DIR", "/tmp/nonexistent_xyz")
        resp = client.get("/download/abc/abc_report.md")
        assert resp.status_code == 404

    def test_valid_matrix_filename_not_found_returns_404(self, monkeypatch):
        monkeypatch.setattr(cfg, "OUTPUT_DIR", "/tmp/nonexistent_xyz")
        resp = client.get("/download/abc/abc_matrix.csv")
        assert resp.status_code == 404

    def test_valid_report_file_served(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "OUTPUT_DIR", str(tmp_path))
        report_file = tmp_path / "abc_report.md"
        report_file.write_text("# Report content")
        resp = client.get("/download/abc/abc_report.md")
        assert resp.status_code == 200

    def test_valid_matrix_file_served(self, tmp_path, monkeypatch):
        monkeypatch.setattr(cfg, "OUTPUT_DIR", str(tmp_path))
        matrix_file = tmp_path / "abc_matrix.csv"
        matrix_file.write_text("approach,score\nOption A,8.0")
        resp = client.get("/download/abc/abc_matrix.csv")
        assert resp.status_code == 200
