"""
test_config.py — tests for backend/config.py
"""
import pytest
from backend.config import Config, cfg


class TestConfigDefaults:
    def test_groq_base_url_set(self):
        assert cfg.GROQ_BASE_URL == "https://api.groq.com/openai/v1"

    def test_llm_temperature_is_float(self):
        assert isinstance(cfg.LLM_TEMPERATURE, float)

    def test_llm_temperature_default(self):
        assert cfg.LLM_TEMPERATURE == 0.3

    # LLM_MAX_TOKENS removed — max_tokens is set per-agent, not via config.
    # See CLAUDE.md per-agent max_tokens table.

    def test_scrape_max_chars_is_int(self):
        assert isinstance(cfg.SCRAPE_MAX_CHARS, int)

    def test_scrape_max_chars_positive(self):
        assert cfg.SCRAPE_MAX_CHARS > 0

    def test_qa_pass_threshold_is_float(self):
        assert isinstance(cfg.QA_PASS_THRESHOLD, float)

    def test_qa_pass_threshold_in_range(self):
        assert 0.0 <= cfg.QA_PASS_THRESHOLD <= 10.0

    def test_max_qa_iterations_is_int(self):
        assert isinstance(cfg.MAX_QA_ITERATIONS, int)

    def test_max_qa_iterations_non_negative(self):
        # 0 is valid (disables QA loop during dev)
        assert cfg.MAX_QA_ITERATIONS >= 0

    def test_output_dir_set(self):
        assert cfg.OUTPUT_DIR is not None
        assert len(cfg.OUTPUT_DIR) > 0

    def test_llm_model_set(self):
        assert cfg.LLM_MODEL
        assert len(cfg.LLM_MODEL) > 0


class TestConfigLLMClientKwargs:
    def test_returns_dict(self):
        kwargs = Config.llm_client_kwargs()
        assert isinstance(kwargs, dict)

    def test_contains_api_key(self):
        kwargs = Config.llm_client_kwargs()
        assert "api_key" in kwargs

    def test_contains_base_url(self):
        kwargs = Config.llm_client_kwargs()
        assert "base_url" in kwargs

    def test_base_url_value(self):
        kwargs = Config.llm_client_kwargs()
        assert kwargs["base_url"] == "https://api.groq.com/openai/v1"


class TestConfigValidate:
    def test_validate_returns_list(self):
        result = Config.validate()
        assert isinstance(result, list)

    def test_validate_reports_missing_api_key(self, monkeypatch):
        monkeypatch.setattr(Config, "GROQ_API_KEY", "")
        problems = Config.validate()
        assert any("GROQ_API_KEY" in p for p in problems)

    def test_validate_reports_wrong_api_key_format(self, monkeypatch):
        # validate() checks startswith("gsk_") — any other prefix is flagged
        monkeypatch.setattr(Config, "GROQ_API_KEY", "sk-not-a-groq-key")
        problems = Config.validate()
        assert any("gsk_" in p for p in problems)

    def test_validate_ok_with_valid_groq_key(self, monkeypatch):
        monkeypatch.setattr(Config, "GROQ_API_KEY", "gsk_testkey123")
        problems = Config.validate()
        assert problems == []

    def test_validate_ok_when_key_absent_but_not_checked_twice(self, monkeypatch):
        # Empty key → exactly one error, not two (missing + format)
        monkeypatch.setattr(Config, "GROQ_API_KEY", "")
        problems = Config.validate()
        # Only the "not set" error fires; format check skipped when key is empty
        assert len(problems) == 1
