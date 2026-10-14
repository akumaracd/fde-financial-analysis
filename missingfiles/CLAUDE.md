# CLAUDE.md — AI Coding Governance

This file defines the rules, patterns, and constraints that govern how AI-assisted code is written and reviewed in this project. Any contributor (human or AI) must follow these conventions.

---

## LLM Integration Pattern (Groq)

All LLM calls follow a single, consistent pattern. **No agent may deviate from this.**

```python
from openai import OpenAI
from backend.config import cfg

client = OpenAI(**cfg.llm_client_kwargs())

resp = client.chat.completions.create(
    model=cfg.LLM_MODEL,
    temperature=cfg.LLM_TEMPERATURE,
    max_tokens=cfg.LLM_MAX_TOKENS,
    messages=[
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user",   "content": user_content},
    ],
)
result = resp.choices[0].message.content
```

**Rules:**
- Always import `cfg` from `backend.config` — never hardcode API keys, model names, or temperatures.
- Always call `cfg.llm_client_kwargs()` for the client constructor — this is the single source of truth for the Groq base URL and API key.
- Always wrap LLM calls in `try/except Exception` and return a structured error state rather than letting exceptions propagate to the LangGraph runner.
- Always strip markdown fences from LLM responses before JSON parsing (Groq wraps JSON in ` ```json ``` ` despite being instructed not to):

```python
def _strip_fences(raw: str) -> str:
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()
```

---

## Agent Structure

Every agent is a Python module in `backend/agents/` with a single `run(state: dict) -> dict` function.

**Mandatory conventions:**

1. **Input via state dict** — agents read from `state`, never from direct arguments.
2. **Output via state dict** — agents return a partial state dict; LangGraph merges it.
3. **Always set `current_agent`** — every return must include `"current_agent": "<node_name>"`.
4. **Error returns, not exceptions** — on failure, return `{"status": "error", "error": "<message>", "current_agent": "..."}` rather than raising.
5. **Graceful degradation** — if a non-critical step fails (e.g. one scrape out of ten), log and continue; don't abort the pipeline.
6. **No direct I/O** — agents must not call `open()`, `requests.get()`, or `DDGS()` directly. All I/O goes through `backend/mcp_server/tools.py`.

**Template:**

```python
import logging
from openai import OpenAI
from backend.config import cfg

log    = logging.getLogger(__name__)
client = OpenAI(**cfg.llm_client_kwargs())

SYSTEM_PROMPT = """..."""


def run(state: dict) -> dict:
    topic = state.get("topic", "")

    try:
        resp = client.chat.completions.create(...)
        result = resp.choices[0].message.content
    except Exception as e:
        log.error(f"[agent_name] LLM call failed: {e}")
        return {"status": "error", "error": str(e), "current_agent": "node_name"}

    return {"output_key": result, "current_agent": "node_name"}
```

---

## MCP Tool Layer

All external I/O (search, scrape, file write) is isolated in `backend/mcp_server/tools.py`. This is the **only** file that may call:
- `DDGS()` (DuckDuckGo search)
- `requests.get()` (HTTP scraping)
- `open()` for writing output files

**Exception hierarchy** (from `backend/mcp_server/exceptions.py`):
- `MCPToolError` — base; catch this to handle any tool failure generically
- `SearchError` — raised by `search_sources()`
- `ScrapeError` — raised by `scrape_content()`
- `FileWriteError` — raised by `write_report()` and `save_decision_matrix()`

Agents catch specific exception types, not bare `Exception`, for tool calls.

---

## Testing Rules

1. **All tests run offline** — every LLM call and network call must be mocked with `unittest.mock.patch`. No test may make a real API or HTTP request.
2. **Every agent needs both** — a happy-path test and at least one adversarial test (LLM failure, JSON parse failure, empty input).
3. **Use `conftest.py` fixtures** — shared state dicts and sample data live in `backend/tests/conftest.py`. Don't duplicate them across test files.
4. **Test file naming** — agent tests in `test_agents.py`, MCP tool tests in `test_mcp_tools.py`, FastAPI tests in `test_main.py`.
5. **No hardcoded paths** — use `tmp_path` (pytest fixture) for any test that writes files; patch `cfg.OUTPUT_DIR` with `monkeypatch`.
6. **Coverage target** — maintain ≥ 80% line coverage across `backend/`.

Run the full suite:
```bash
pytest backend/tests/ -v --cov=backend --cov-report=term-missing
```

---

## Configuration Governance

- `backend/config.py` is the **only** file that calls `os.environ.get()`.
- Every new setting must be added to `Config` with a sensible default and a `validate()` check if it's required.
- Never instantiate `Config()` directly in agent code — always import the shared `cfg` instance:
  ```python
  from backend.config import cfg
  ```

---

## What AI Tools Were Used

This project was built with **Claude (Anthropic)** as the primary AI coding assistant. Claude was used for:

- Initial agent scaffolding and system prompt engineering
- MCP tool layer design and exception hierarchy
- Test suite generation (163 tests across 6 test modules)
- Documentation (SDD, architecture rationale, this file)

All AI-generated code was reviewed for correctness, security (path traversal guards, input validation), and adherence to the conventions in this file before being committed.

---

## Security Constraints

- `run_id` values passed to file-writing functions are sanitised with `os.path.basename()` to prevent path traversal attacks.
- CORS is wide-open (`allow_origins=["*"]`) for local development only — tighten before any deployment.
- The `.env` file must never be committed. It is listed in `.gitignore`.
- `GROQ_API_KEY` must never appear in source code, logs, or test output.
