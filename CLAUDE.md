# CLAUDE.md — AI Coding Governance

This file governs how AI coding assistants (Claude, Copilot, Cursor) should interact with this codebase. Read this before making any changes.

---

## Project Overview

FDE Financial Analysis Automation — a 7-agent LangGraph pipeline that produces executive-grade financial analysis reports from a topic string. Built for 8 daily runs in a Banking/Financial Services context.

---

## Architecture Rules

### 1. State flows through LangGraph only

`AnalysisState` in `backend/state.py` is the single source of truth. Every agent receives state as input and returns a partial state dict as output. LangGraph merges the partial dict into the full state automatically.

**Never:**
- Pass data between agents via module-level globals
- Mutate `state` directly inside conditional edge functions (use agent return dicts instead)
- Add fields to `AnalysisState` without updating `initial_state()` with a default value

### 2. All I/O goes through MCP tools

`backend/mcp_server/tools.py` is the only file that may:
- Call `DDGS()` (DuckDuckGo search)
- Call `requests.Session.get()` (HTTP scraping)
- Call `open()` for file writes

Agents must call `search_sources()`, `scrape_content()`, `write_report()`, `save_decision_matrix()` — never make network or filesystem calls directly. This keeps agents testable offline via mocking.

### 3. LLM calls go through groq_fallback only

All LLM calls must go through `groq_call_with_fallback()` in `backend/groq_fallback.py`. Never call `openai.OpenAI().chat.completions.create()` directly in agent code. The fallback chain (OpenRouter → Groq → Anthropic) is managed there.

### 4. Errors set status, not raise exceptions

Agents must catch `ModelExhaustedError` and general `Exception` and return:
```python
{"status": "error", "error": str(e), "current_agent": "<node_name>"}
```
Never let an exception propagate out of an agent's `run()` function. The graph's `route_or_error()` logic depends on `status == "error"` being set in state.

### 5. Config comes from cfg only

All environment variables are read in `backend/config.py` via the `cfg` singleton. Never call `os.environ.get()` in agent code. Import `cfg` and use `cfg.LLM_MODEL`, `cfg.OUTPUT_DIR`, etc.

---

## Groq/OpenRouter Pattern

### Client construction
```python
from backend.config import cfg
from openai import OpenAI
client = OpenAI(**cfg.llm_client_kwargs())
```

This creates a module-level client. Agents construct their client once at import time.

### LLM call pattern
```python
from backend.groq_fallback import groq_call_with_fallback, ModelExhaustedError

try:
    result = groq_call_with_fallback(
        client=client,
        messages=messages,
        primary_model=cfg.LLM_MODEL,
        temperature=cfg.LLM_TEMPERATURE,
        max_tokens=2048,      # set per-agent, not from cfg
        task="heavy",         # heavy | medium | light
    )
except ModelExhaustedError as e:
    return {"status": "error", "error": str(e), "current_agent": "agent_node"}
except Exception as e:
    return {"status": "error", "error": f"Agent error: {e}", "current_agent": "agent_node"}
```

### Task tiers
| Tier | Models | Use for |
|---|---|---|
| `heavy` | gpt-4o, gpt-oss-120b | analysis, risk, report — quality matters |
| `medium` | gpt-4o-mini, gpt-oss-20b | scoring — structured JSON, not creative |
| `light` | gpt-4o-mini, gpt-oss-20b | research filter, QA — short outputs |

### max_tokens per agent
Set `max_tokens` in each agent file, not in config. Current values:
- research: 1024
- analysis: 2048
- risk: 2048
- scoring: 1024
- report_writer: 4096
- qa: 300

---

## Agent Structure

Every agent file must follow this structure:

```python
"""
agent_name.py — Agent N: Role Description
"""
import logging
from openai import OpenAI
from backend.config import cfg
from backend.groq_fallback import groq_call_with_fallback, ModelExhaustedError

log    = logging.getLogger(__name__)
client = OpenAI(**cfg.llm_client_kwargs())

SYSTEM_PROMPT = """Static system prompt — topic/industry go in user message for caching."""


def run(state: dict) -> dict:
    # 1. Extract inputs from state
    # 2. Guard on empty inputs (return gracefully, not error)
    # 3. Build messages
    # 4. Call groq_call_with_fallback
    # 5. Parse/validate output
    # 6. Return partial state dict
    ...
```

**Static system prompts:** Keep `SYSTEM_PROMPT` as a module-level constant. Put topic/industry in the user message. This enables prompt caching on providers that support it.

---

## Testing Rules

### Every agent must have tests for:
1. Happy path — valid input, valid LLM response
2. Empty input guard — empty `extracted_content`, empty `analysis`, etc.
3. `ModelExhaustedError` — must set `status="error"`
4. Unexpected LLM response format — malformed JSON, plain string instead of array
5. Adversarial LLM output — wrong field names, invalid enum values

### Mock pattern for LLM calls:
```python
with patch("backend.groq_fallback.groq_call_with_fallback", return_value="..."):
    result = agent.run(state)
```

### Mock pattern for tool calls:
```python
with patch("backend.mcp_server.tools.scrape_content", return_value={...}):
    result = extraction_agent.run(state)
```

### Never in tests:
- Make real HTTP calls
- Make real LLM API calls
- Write to real `OUTPUT_DIR`
- Use `sleep()` or time-dependent logic

---

## Adding a New Agent

1. Create `backend/agents/new_agent.py` following the agent structure above
2. Add `new_node` to `workflow.py`:
   - `graph.add_node("new_node", new_agent.run)`
   - Add conditional edge using `_route_or_error("new_node")`
3. Add state fields needed by the new agent to `AnalysisState` and `initial_state()`
4. Add tests to `backend/tests/test_agents.py` — all 5 test categories above
5. Update `docs/SDD.md` with the new agent spec

---

## File Naming Convention

Output files use topic slugs, not UUIDs:
- `"AI Agent Adoption for Banking"` → `ai_agent_adoption_for_banking_report.md`
- Generated by `make_file_slug(topic, run_id)` in `tools.py`
- Falls back to `run_id` if topic is empty

Never hardcode `f"{run_id}_report.md"` in agent code. Always pass `topic` to `write_report()` and `save_decision_matrix()`.

---

## Common Mistakes to Avoid

| Mistake | Correct approach |
|---|---|
| `state["iteration_count"] += 1` in routing function | Return `"iteration_count": state.get("iteration_count",0)+1` from agent |
| `raise RuntimeError(...)` in agent | `return {"status": "error", "error": str(e)}` |
| `os.environ.get("GROQ_API_KEY")` in agent | `from backend.config import cfg` |
| Hardcoded `max_tokens=4096` everywhere | Set appropriate value per agent (see table above) |
| Direct OpenAI client in agent | Use `groq_call_with_fallback()` |
| `graph.add_edge("a", "b")` for all edges | Use `graph.add_conditional_edges` with `_route_or_error` |
| `ANALYSIS_GRAPH.invoke(state)` without config | `ANALYSIS_GRAPH.invoke(state, config={"recursion_limit": 50})` |

---

## Environment Setup for AI Assistants

When making changes, ensure:
1. `.env` is never committed (check `.gitignore`)
2. New env vars are added to both `config.py` and `.env.template`
3. `validate()` in `config.py` is updated to check critical new keys
4. All new I/O operations go through `tools.py`
5. Tests run offline: `pytest backend/tests/ -v` with no internet required
