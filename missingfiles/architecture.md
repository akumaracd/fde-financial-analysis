# Architecture — FDE Financial Analysis Pipeline

## System Overview

The system is a multi-agent AI pipeline exposed as a REST API. A single HTTP request triggers an autonomous sequence of 7 specialised agents that research, analyse, assess risk, score investment options, and write a client-ready executive report — with a quality gate that can loop back for re-analysis if output quality is insufficient.

---

## Architecture Diagram

```
Client (HTTP)
     │
     ▼
┌────────────────────────────────────────────────────────────────┐
│  FastAPI  (backend/main.py)                                    │
│                                                                │
│  POST /run-analysis ──► BackgroundTask ──► LangGraph invoke()  │
│  GET  /status/{id}  ──► run_registry lookup                    │
│  GET  /results/{id} ──► run_registry lookup                    │
│  GET  /download/... ──► FileResponse from OUTPUT_DIR           │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│  LangGraph StateGraph  (backend/workflow.py)                   │
│                                                                │
│  AnalysisState (TypedDict) flows through each node:           │
│                                                                │
│  [START]                                                       │
│     │                                                          │
│     ▼                                                          │
│  research_node ──────────────────────────────────────────────► │
│  (DuckDuckGo × 2 queries → Groq scoring/filtering)            │
│     │                                                          │
│     ▼                                                          │
│  extractor_node ─────────────────────────────────────────────► │
│  (BeautifulSoup scrape each source URL)                        │
│     │                                                          │
│     ▼                                                          │
│  analysis_node ──────────────────────────────────────────────► │
│  (Groq: synthesise into Key Trends / Arch / Regulatory /       │
│   Strategic sections)                                          │
│     │                                                          │
│     ▼                                                          │
│  risk_node ──────────────────────────────────────────────────► │
│  (Groq: 6-10 risks with regulatory refs, mitigations, owners) │
│     │                                                          │
│     ▼                                                          │
│  scoring_node ───────────────────────────────────────────────► │
│  (Groq: score 3-4 approaches; server recalculates totals)      │
│     │                                                          │
│     ▼                                                          │
│  report_node ────────────────────────────────────────────────► │
│  (Groq: compile executive report → write .md + .csv to disk)  │
│     │                                                          │
│     ▼                                                          │
│  qa_node                                                       │
│  (Groq: score report 0-10 on 5-category rubric)               │
│     │                                                          │
│     ├── score ≥ 7.0 ──► [END]                                  │
│     │                                                          │
│     └── score < 7.0 AND iterations < MAX ──► analysis_node    │
│                                                                │
└────────────────────────────────────────────────────────────────┘
                              │
                              ▼
┌────────────────────────────────────────────────────────────────┐
│  MCP Tool Layer  (backend/mcp_server/tools.py)                 │
│                                                                │
│  search_sources()      → DDGS (DuckDuckGo)                    │
│  scrape_content()      → requests + BeautifulSoup             │
│  write_report()        → disk I/O (OUTPUT_DIR)                │
│  save_decision_matrix()→ disk I/O (OUTPUT_DIR)                │
└────────────────────────────────────────────────────────────────┘
```

---

## Technology Decisions

### LLM Provider: Groq vs OpenAI

| Criterion | Groq | OpenAI |
|-----------|------|--------|
| Cost | Free tier with generous limits | Pay-per-token from first call |
| Latency | ~10× faster inference (LPU hardware) | Standard GPU inference |
| API compatibility | OpenAI-compatible (drop-in) | Native |
| Model selection | Llama, Mixtral, Gemma variants | GPT-4o, GPT-4-turbo |
| Rate limits | 6,000 RPM on free tier | 3 RPM on free tier (Tier 0) |
| Key requirement | Free account | Paid account for production use |

**Decision: Groq.** The project requirement was zero cost to evaluate. Groq's OpenAI-compatible API means the `openai` Python SDK works without modification — a single `base_url` change in `config.py` is all that separates Groq from OpenAI if the team wants to switch later.

---

### Agent Orchestration: LangGraph vs CrewAI

| Criterion | LangGraph | CrewAI |
|-----------|-----------|--------|
| Graph model | Explicit DAG with typed state | Role-based crew with implicit flow |
| State management | Single `TypedDict` shared across all nodes | Per-agent memory, harder to inspect |
| Conditional routing | First-class (`add_conditional_edges`) | Workarounds via task callbacks |
| Debugging | Full state visible at every node | Opaque between agent handoffs |
| Testing | Pure functions (`run(state) -> dict`) | Agents coupled to CrewAI runtime |
| Maturity | LangChain ecosystem, production-proven | Newer, higher abstraction |

**Decision: LangGraph.** The QA feedback loop (re-routing from `qa_node` back to `analysis_node` based on score) requires explicit conditional edges. LangGraph makes this a 3-line `add_conditional_edges` call. In CrewAI, this would require task-level callbacks and shared mutable state that is difficult to test. The pure-function agent design also means every agent can be unit-tested in isolation without instantiating the full graph.

---

### Web Search: DuckDuckGo vs SerpAPI

| Criterion | DuckDuckGo (DDGS) | SerpAPI |
|-----------|-------------------|---------|
| Cost | Free, no API key | $50/month minimum |
| Rate limits | Unofficial; aggressive use risks blocks | Generous paid quotas |
| Result quality | Comparable for research queries | Slightly richer metadata |
| Setup friction | `pip install duckduckgo-search` | Account + billing setup |
| Reliability | Best-effort; occasional blocks | SLA-backed |

**Decision: DuckDuckGo.** The project requirement was free-tier only. DDGS provides sufficient result quality for financial research queries. The `search_sources()` function is isolated in the MCP tool layer — switching to SerpAPI later requires changing only that function, not any agent code.

---

### HTML Parsing: BeautifulSoup vs Playwright

| Criterion | BeautifulSoup + requests | Playwright |
|-----------|--------------------------|------------|
| Setup | `pip install beautifulsoup4 requests` | Browser binaries (~300MB) |
| JavaScript rendering | No | Yes |
| Speed | Fast (HTTP only) | Slow (full browser launch per page) |
| Resource usage | Minimal | High (memory + CPU per browser) |
| Bot detection bypass | Partial (User-Agent spoofing) | Better (real browser fingerprint) |

**Decision: BeautifulSoup.** Most financial news and regulatory sources serve readable HTML without JavaScript rendering. The `html.parser` stdlib backend requires no additional installs. For sources that block scrapers, `ScrapeError` is raised and the pipeline continues with the remaining sources — bot-blocked pages are logged, not fatal.

---

## Data Flow

```
topic + industry
       │
       ▼
   [sources]          list[dict] — title, url, summary, regulatory_flag, relevance_score
       │
       ▼
[extracted_content]   str — concatenated plain text from all successfully scraped URLs
       │
       ▼
   [analysis]         str — structured markdown (Key Trends / Arch / Regulatory / Strategic)
       │
       ▼
    [risks]           list[dict] — name, category, level, impact, mitigation, owner, regulatory_ref
       │
       ▼
[decision_matrix]     list[dict] — approach, 6 scores, weighted_total, verdict
       │
       ▼
    [report]          str — full executive markdown report
       │
       ▼
  [qa_score]          float — 0-10 rubric score
  [qa_pass]           bool — score >= QA_PASS_THRESHOLD
```

All fields live in a single `AnalysisState` TypedDict (see `backend/state.py`). LangGraph merges each agent's partial return dict into the running state — agents only need to return the keys they modify.

---

## Security Considerations

- **Path traversal** — `run_id` values are sanitised with `os.path.basename()` before use in file paths. A malicious `run_id` like `../../etc/passwd` is reduced to `passwd`, which resolves inside `OUTPUT_DIR`.
- **Content limits** — scraped content is capped at `SCRAPE_MAX_CHARS` (default 8,000) before being passed to the LLM, preventing context-window exhaustion from large pages.
- **LLM prompt injection** — user-supplied `topic` and `industry` strings are interpolated into system prompts via `.format()`. Inputs are not sanitised beyond Pydantic's `str` validation, which is acceptable for an internal tool but should be hardened before public deployment.
- **CORS** — `allow_origins=["*"]` is intentional for local development. Restrict to specific origins before deploying.
