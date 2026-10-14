"""
main.py — FastAPI REST API wrapper for the FDE pipeline.
Place this in: <project_root>/main.py   (NOT inside backend/)

Delete the duplicate at backend/main.py if it exists.

Endpoints:
  GET  /health                        — config + provider availability check
  POST /run-analysis                  — start pipeline, returns run_id
  GET  /status/{run_id}               — poll progress
  GET  /results/{run_id}              — full results when completed
  GET  /download/{run_id}/{filename}  — download report.md or matrix.csv

Changes vs previous version:
  - ANALYSIS_GRAPH.invoke() now passes recursion_limit=50 via config kwarg.
    This replaces the silent default of 10007 and causes runaway graphs to
    fail fast (in <50 hops) with a clear error instead of grinding for minutes.
  - /health now checks for ANTHROPIC_API_KEY in addition to GROQ/OPENROUTER
    and reports which providers are available for the day.
  - /status now surfaces provider_used and error_count from state so you can
    see mid-run whether a fallback was triggered.
  - _run_pipeline catches GraphRecursionError explicitly and sets a clear error
    message instead of surfacing the raw LangGraph traceback.
"""
import os
import re
import uuid
import logging
from fastapi import FastAPI, BackgroundTasks, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from pydantic import BaseModel, field_validator

from backend.config import cfg
from backend.state import initial_state
from backend.workflow import ANALYSIS_GRAPH
from backend.groq_fallback import get_openrouter_balance

try:
    from langgraph.errors import GraphRecursionError
except ImportError:
    # Older langgraph versions expose it differently
    GraphRecursionError = RecursionError

logging.basicConfig(level=cfg.LOG_LEVEL)
log = logging.getLogger(__name__)

app = FastAPI(
    title="Financial Analysis Automation",
    description="7-agent LangGraph pipeline: Groq → OpenRouter → Anthropic fallback",
    version="2.0.0",
)
app.add_middleware(
    CORSMiddleware, allow_origins=["*"],
    allow_methods=["*"], allow_headers=["*"],
)

run_registry: dict[str, dict] = {}

_PROGRESS = {
    "research_node":  15,
    "extractor_node": 30,
    "analysis_node":  45,
    "risk_node":      60,
    "scoring_node":   75,
    "report_node":    88,
    "qa_node":        95,
    "error_node":     100,
    "completed":      100,
    "error":          100,
}


class AnalysisRequest(BaseModel):
    topic:    str
    industry: str = "Banking / Financial Services"

    @field_validator("topic")
    @classmethod
    def not_empty(cls, v: str) -> str:
        if not v.strip():
            raise ValueError("topic must be a non-empty string")
        return v.strip()


def _run_pipeline(run_id: str, topic: str, industry: str) -> None:
    """
    Runs synchronously inside a FastAPI BackgroundTask.
    The /run-analysis caller already received its 202 before this starts.
    """
    state = initial_state(topic=topic, industry=industry, run_id=run_id)
    run_registry[run_id] = dict(state)

    try:
        result = ANALYSIS_GRAPH.invoke(
            state,
            config={"recursion_limit": 50},  # ← was default 10007; catches runaways fast
        )

        # Agents that hit error_node set status="error" themselves.
        # This is just a safety net if somehow END is hit without status being set.
        if not result.get("status") or result["status"] == "initialized":
            result["status"] = "completed"

        run_registry[run_id] = dict(result)
        log.info(
            "[pipeline] done run_id=%s qa=%.1f provider=%s fallbacks=%d",
            run_id,
            result.get("qa_score", 0.0),
            result.get("provider_used", "?"),
            result.get("error_count", 0),
        )

    except GraphRecursionError:
        # This should no longer occur with error routing in place, but kept
        # as a last-resort catch.
        msg = (
            "Pipeline exceeded recursion limit (50). This usually means an agent "
            "is looping — check groq_fallback logs for repeated quota errors."
        )
        log.error("[pipeline] GraphRecursionError run_id=%s", run_id)
        run_registry[run_id]["status"] = "error"
        run_registry[run_id]["error"]  = msg

    except Exception as e:
        log.exception("[pipeline] unexpected error run_id=%s", run_id)
        run_registry[run_id]["status"] = "error"
        run_registry[run_id]["error"]  = str(e)


@app.get("/health")
def health():
    problems = cfg.validate()

    # Which providers are configured
    providers = {
        "openrouter": bool(os.environ.get("OPENROUTER_API_KEY", "").strip()),
        "groq":       bool(os.environ.get("GROQ_API_KEY", "").strip()),
        "anthropic":  bool(os.environ.get("ANTHROPIC_API_KEY", "").strip()),
    }
    if not providers["anthropic"]:
        problems.append(
            "ANTHROPIC_API_KEY not set — final fallback disabled. "
            "Add key at console.anthropic.com."
        )

    # Live OpenRouter credit balance (force_refresh=True so /health always shows fresh data)
    balance = get_openrouter_balance(force_refresh=True)
    openrouter_credits = {
        "remaining_usd": balance["remaining_usd"],
        "used_usd":      balance["used_usd"],
        "limit_usd":     balance["limit_usd"],
        "status": (
            "error"       if balance["error"] else
            "unknown"     if balance["remaining_usd"] is None else
            "critical"    if balance["remaining_usd"] < 0.10 else
            "low"         if balance["remaining_usd"] < 0.50 else
            "ok"
        ),
        "error": balance["error"],
    }
    if openrouter_credits["status"] == "critical":
        problems.append(
            f"OpenRouter credits critically low (${balance['remaining_usd']:.4f} remaining). "
            "Pipeline will skip OpenRouter and use Groq. Top up at openrouter.ai/credits."
        )
    elif openrouter_credits["status"] == "low":
        problems.append(
            f"OpenRouter credits low (${balance['remaining_usd']:.4f} remaining). "
            "Top up soon at openrouter.ai/credits."
        )

    return {
        "status":             "ok" if not problems else "config_warning",
        "problems":           problems,
        "providers":          providers,
        "openrouter_credits": openrouter_credits,
        "llm":                "OpenRouter gpt-4o → Groq → Anthropic claude-sonnet-4-6",
        "search":             "DuckDuckGo (no API key)",
        "scraping":           "BeautifulSoup (no API key)",
    }


@app.get("/credits")
def credits():
    """Live OpenRouter credit balance. Useful to check before kicking off 8 daily runs."""
    balance = get_openrouter_balance(force_refresh=True)
    status = (
        "error"    if balance["error"] else
        "unknown"  if balance["remaining_usd"] is None else
        "critical" if balance["remaining_usd"] < 0.10 else
        "low"      if balance["remaining_usd"] < 0.50 else
        "ok"
    )
    return {
        "remaining_usd": balance["remaining_usd"],
        "used_usd":      balance["used_usd"],
        "limit_usd":     balance["limit_usd"],
        "status":        status,
        "top_up_url":    "https://openrouter.ai/credits",
        "error":         balance["error"],
    }


@app.post("/run-analysis", status_code=202)
def start(req: AnalysisRequest, bg: BackgroundTasks):
    run_id = str(uuid.uuid4())
    bg.add_task(_run_pipeline, run_id, req.topic, req.industry)
    return {
        "run_id":    run_id,
        "status":    "started",
        "poll_url":  f"/status/{run_id}",
        "result_url": f"/results/{run_id}",
    }


@app.get("/status/{run_id}")
def status(run_id: str):
    if run_id not in run_registry:
        raise HTTPException(404, f"run_id '{run_id}' not found")
    s   = run_registry[run_id]
    key = s["status"] if s["status"] in ("completed", "error") else s.get("current_agent", "")
    return {
        "run_id":          run_id,
        "status":          s["status"],
        "current_agent":   s.get("current_agent", ""),
        "iteration_count": s.get("iteration_count", 0),
        "progress_pct":    _PROGRESS.get(key, 5),
        "provider_used":   s.get("provider_used", ""),   # ← which LLM provider last responded
        "error_count":     s.get("error_count", 0),       # ← number of fallback triggers
        "error":           s.get("error"),
    }


@app.get("/results/{run_id}")
def results(run_id: str):
    if run_id not in run_registry:
        raise HTTPException(404, f"run_id '{run_id}' not found")
    s = run_registry[run_id]
    if s["status"] == "running":
        raise HTTPException(409, {"error": "still running — poll /status first"})
    # Add friendly download filenames to the response
    topic = s.get("topic", "")
    slug  = _topic_slug(topic, run_id)
    result = dict(run_registry[run_id])
    result["download_files"] = {
        "report": f"{slug}_report.md",
        "matrix": f"{slug}_matrix.csv",
    }
    return result


def _topic_slug(topic: str, run_id: str, max_len: int = 50) -> str:
    """Mirror of tools.make_file_slug — used in main.py without importing tools."""
    if topic and topic.strip():
        slug = topic.lower().strip()
        slug = re.sub(r"[^\w\s-]", "", slug)
        slug = re.sub(r"[\s-]+", "_", slug)
        return slug[:max_len].strip("_")
    return run_id


@app.get("/download/{run_id}/{filename}")
def download(run_id: str, filename: str):
    if run_id not in run_registry:
        raise HTTPException(404, f"run_id '{run_id}' not found")
    topic = run_registry[run_id].get("topic", "")
    slug  = _topic_slug(topic, run_id)
    allowed = {f"{slug}_report.md", f"{slug}_matrix.csv"}
    if filename not in allowed:
        raise HTTPException(
            400,
            f"Not available. Expected one of: {sorted(allowed)}"
        )
    path = os.path.join(cfg.OUTPUT_DIR, filename)
    if not os.path.exists(path):
        raise HTTPException(404, "file not found — run may have errored before writing output")
    mt = "text/markdown" if filename.endswith(".md") else "text/csv"
    return FileResponse(path, filename=filename, media_type=mt)
