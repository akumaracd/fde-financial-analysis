# AI Build Log — FDE Financial Analysis Pipeline

A chronological log of AI tool usage across the 5-day build. Each entry records what was built, which AI tool was used, what it produced, what was accepted or rejected, and why.

---

## Day 1 — Project Scaffold & Core Infrastructure

### Session goal
Set up the FastAPI server, LangGraph workflow skeleton, shared state, and config system.

### AI tool used
Claude (Anthropic) — claude.ai chat interface

### What was prompted
1. "Design a multi-agent financial analysis pipeline using LangGraph and FastAPI. The pipeline should take a topic and industry as input and produce an executive report. Use Groq as the LLM provider and DuckDuckGo for search — both free."
2. "Generate the AnalysisState TypedDict with all fields the pipeline will need, and an initial_state() factory function."
3. "Write the FastAPI main.py with four endpoints: POST /run-analysis, GET /status, GET /results, GET /download."

### What was accepted
- Full `AnalysisState` TypedDict schema (17 fields)
- `initial_state()` factory with explicit per-call list instantiation (avoiding mutable default bug)
- FastAPI endpoint structure including background task pattern for async pipeline execution
- In-memory `run_registry` dict with reasoning for why a real datastore wasn't needed for local dev

### What was rejected / modified
- Initial design had a `/cancel/{run_id}` endpoint — removed because background tasks in FastAPI cannot be cancelled cleanly without a task queue like Celery
- AI suggested `pydantic.BaseSettings` for config — rejected in favour of a plain class to avoid an extra dependency; `os.environ.get()` with explicit defaults is more readable for an educational project

### Time saved estimate
~3 hours of boilerplate scaffolding reduced to ~45 minutes of review and integration

---

## Day 2 — MCP Tool Layer & Research + Extraction Agents

### Session goal
Build the MCP tool layer (search, scrape, file write) and the first two agents.

### AI tool used
Claude (Anthropic)

### What was prompted
1. "Write search_sources(), scrape_content(), write_report(), and save_decision_matrix() in a tools.py file. Use DuckDuckGo for search and BeautifulSoup for scraping. Raise custom exceptions for all failure modes."
2. "Write the research_agent and extraction_agent run() functions following the state-dict pattern."

### What was accepted
- Custom exception hierarchy (`MCPToolError` → `SearchError`, `ScrapeError`, `FileWriteError`)
- `search_sources()` with regulatory keyword detection and relevance score differentiation
- `scrape_content()` with per-failure-mode `ScrapeError` messages
- `os.path.basename()` path traversal guard in `write_report()` and `save_decision_matrix()`
- Extraction agent with per-URL try/except and metadata recording

### What was rejected / modified
- Initial `tools.py` had `from ddgs import DDGS` (wrong package) alongside the correct import — caught during code review, removed
- `lxml` parser in BeautifulSoup — switched to `html.parser` to eliminate the install dependency
- AI initially didn't include a `max_chars` misconfiguration guard — added after review (`if not max_chars: raise ScrapeError(...)`)
- `save_decision_matrix()` lacked key consistency checking — added `inconsistent` pre-flight scan after identifying the silent failure risk

### Key decision logged
Decided against having the extraction agent call Groq to summarise each source. Rationale: adds latency and a failure point for a task BeautifulSoup already handles deterministically. Logged in `prompts/engineering-prompts.md` as Decision 7.

---

## Day 3 — Analysis, Risk, and Scoring Agents

### Session goal
Build the three analytical agents that form the core reasoning pipeline.

### AI tool used
Claude (Anthropic)

### What was prompted
1. "Write analysis_agent.py. It should synthesise extracted web content into four sections: Key Trends, Architectural Patterns, Regulatory Implications, Strategic Implications. The analysis must be grounded in the provided content only."
2. "Write risk_agent.py. It should identify 6-10 material risks with regulatory references and specific (not generic) mitigations. Return structured JSON."
3. "Write scoring_agent.py. Score 3-4 investment approaches against 6 weighted criteria. The server must recalculate weighted_total — never trust LLM arithmetic."

### What was accepted
- Analysis agent content truncation to 12,000 chars before sending to Groq
- Risk agent enum validation with fallback defaults (`Critical|High|Medium|Low`, `Regulatory|Operational|Technology|Strategic`)
- Scoring agent server-side `_WEIGHTS` dict and recalculation
- `_strip_fences()` pattern across all three agents

### What was rejected / modified
- First risk prompt produced generic mitigations — iterated through two prompt versions before landing on the negative example + owner + regulatory_ref combination (see `engineering-prompts.md` Decision 2)
- Scoring agent initially asked Groq to calculate `weighted_total` — switched to sentinel `0` + server recalculation after observing arithmetic errors (see Decision 3)
- AI suggested using `dataclasses` for the risk and matrix row schemas — rejected to keep everything as plain dicts consistent with the LangGraph state pattern

---

## Day 4 — Report Writer, QA Agent & Workflow Wiring

### Session goal
Build the final two agents, wire the LangGraph graph, and implement the QA feedback loop.

### AI tool used
Claude (Anthropic)

### What was prompted
1. "Write report_writer_agent.py. It should compile all agent outputs into a client-ready executive report using pre-formatted markdown tables for risks and the decision matrix."
2. "Write qa_agent.py with a 5-category rubric scoring system. If score < 7.0, route back to analysis_node. Cap re-analysis at MAX_QA_ITERATIONS."
3. "Wire the LangGraph StateGraph with all 7 nodes and conditional routing from qa_node."

### What was accepted
- Pre-formatted `_risk_table()` and `_matrix_table()` helpers (server-side table generation, not LLM-generated)
- QA rubric with explicit 0/1/2 band criteria per category (see Decision 4 in engineering-prompts.md)
- QA agent temperature set to `0.1` (vs `0.3` for all others) — scoring task, not creative
- `iteration_count` increment in workflow `qa_routing()` to enforce the re-analysis cap

### What was rejected / modified
- First QA prompt was too simple ("score this 1-10") — produced inconsistently high scores; rebuilt as structured rubric
- AI initially placed `iteration_count` increment inside `qa_agent.run()` — moved to the workflow layer to keep the agent stateless and independently testable
- Report writer first draft asked Groq to format the risk and matrix tables from JSON — switched to server-side pre-formatting (Decision 5 in engineering-prompts.md)

---

## Day 5 — Testing, Documentation & Debugging

### Session goal
Write the full test suite, fix bugs found during testing, and produce all documentation.

### AI tool used
Claude (Anthropic)

### What was prompted
1. "Review tools.py and identify all bugs."
2. "Rewrite tools.py with fixes applied."
3. "Create all missing test files for backend/tests/. Cover all 7 agents, all MCP tools, config, state, and FastAPI endpoints. All tests must run offline."
4. "Create README.md, CLAUDE.md, docs/SDD.md, docs/architecture.md, prompts/engineering-prompts.md, docs/ai-build-log.md."

### Bugs found and fixed
| Bug | Location | Fix |
|-----|----------|-----|
| `from ddgs import DDGS` (wrong package) | tools.py line 1 | Removed; `duckduckgo-search` installed |
| `lxml` parser hard dependency | tools.py | Switched to `html.parser` |
| No `SCRAPE_MAX_CHARS` misconfiguration guard | tools.py | Added explicit check |
| No key consistency check in `save_decision_matrix` | tools.py | Added pre-flight scan |

### Test suite outcome
- 163 tests collected, 163 passed (after fixing 1 test data bug where both mock results matched regulatory keywords)
- Fully offline — all LLM and network calls mocked
- Coverage: all agent modules, MCP tool layer, config, state, FastAPI endpoints

### Documentation produced
All docs generated with Claude and reviewed for accuracy against the actual codebase before committing. No doc content was accepted without cross-checking against the source files.
