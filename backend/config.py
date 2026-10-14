"""
config.py — Central Configuration for the FDE Financial Analysis Pipeline

All environment-driven settings live here. This is the ONLY file that calls
os.environ.get(). Agents must import the shared `cfg` singleton:

    from backend.config import cfg
    client = OpenAI(**cfg.llm_client_kwargs())

Never instantiate Config() directly in agent code.
"""
import os
from dotenv import load_dotenv

load_dotenv()


class Config:
    # ── Groq API ───────────────────────────────────────────────────────────────
    GROQ_API_KEY: str  = os.environ.get("GROQ_API_KEY", "")
    GROQ_BASE_URL: str = "https://api.groq.com/openai/v1"

    # ── Model settings ─────────────────────────────────────────────────────────
    # Primary model — groq_fallback.py routes task tiers automatically:
    #   heavy  → openai/gpt-oss-120b   (analysis, risk, report_writer)
    #   medium → openai/gpt-oss-20b    (scoring)
    #   light  → openai/gpt-oss-20b    (research, qa)
    # On TPD exhaustion the chain falls through:
    #   gpt-oss-120b → gpt-oss-20b → gpt-oss-safeguard-20b → qwen/qwen3.8-27b
    LLM_MODEL: str       = os.environ.get("LLM_MODEL", "openai/gpt-oss-120b")
    LLM_TEMPERATURE: float = float(os.environ.get("LLM_TEMPERATURE", "0.3"))

    # NOTE: LLM_MAX_TOKENS is intentionally NOT read from env.
    # max_tokens is set per-agent in each agent file to avoid all agents
    # defaulting to a single large value regardless of output size.
    # See CLAUDE.md per-agent max_tokens table.

    # ── Output ─────────────────────────────────────────────────────────────────
    OUTPUT_DIR: str = os.environ.get("OUTPUT_DIR", "./outputs")
    LOG_LEVEL: str  = os.environ.get("LOG_LEVEL", "INFO")

    # ── Scraping ───────────────────────────────────────────────────────────────
    SCRAPE_MAX_CHARS: int = int(os.environ.get("SCRAPE_MAX_CHARS", "8000"))

    # ── QA pipeline quality gate ──────────────────────────────────────────────
    # Set MAX_QA_ITERATIONS=0 in .env during development to skip the QA loop
    # (each loop iteration burns ~5 LLM calls).
    QA_PASS_THRESHOLD: float = float(os.environ.get("QA_PASS_THRESHOLD", "7.0"))
    MAX_QA_ITERATIONS: int   = int(os.environ.get("MAX_QA_ITERATIONS", "1"))

    @classmethod
    def llm_client_kwargs(cls) -> dict:
        """
        Returns the constructor kwargs for OpenAI() pointed at Groq.
        Single source of truth for the Groq base URL and API key.

        Usage in every agent:
            client = OpenAI(**cfg.llm_client_kwargs())
        """
        return {
            "api_key":  cls.GROQ_API_KEY,
            "base_url": cls.GROQ_BASE_URL,
        }

    @classmethod
    def validate(cls) -> list[str]:
        """
        Validates config. Returns list of problems (empty = valid).
        Called by main.py /health endpoint.
        """
        problems = []
        if not cls.GROQ_API_KEY:
            problems.append(
                "GROQ_API_KEY is not set. Get a free key at console.groq.com"
            )
        if cls.GROQ_API_KEY and not cls.GROQ_API_KEY.startswith("gsk_"):
            problems.append(
                "GROQ_API_KEY looks wrong — Groq keys start with 'gsk_'."
            )
        return problems


# Module-level singleton — import this in every agent:
#   from backend.config import cfg
cfg = Config()
