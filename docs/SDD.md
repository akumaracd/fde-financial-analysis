# Software Design Document — FDE Financial Analysis Automation

**Version:** 1.0  
**Date:** September 2026  
**Status:** Draft — Approved for Development  
**Authors:** FDE Engineering Team  
**Reviewers:** [Reviewer Name(s)]

---

## Revision History

| Version | Date | Author | Description |
|---|---|---|---|
| 0.1 | TBD | FDE Team | Initial draft |
| 1.0 | September 2026 | FDE Team | Approved for development |

---

## Table of Contents

1. [Purpose and Scope](#1-purpose-and-scope)
2. [Business Requirements](#2-business-requirements)
3. [Functional Requirements](#3-functional-requirements)
4. [Non-Functional Requirements](#4-non-functional-requirements)
5. [System Architecture](#5-system-architecture)
6. [Agent Specifications](#6-agent-specifications)
7. [MCP Tool Layer Contract](#7-mcp-tool-layer-contract)
8. [LLM Fallback Chain](#8-llm-fallback-chain)
9. [API Specification](#9-api-specification)
10. [State Schema](#10-state-schema)
11. [Error Handling Strategy](#11-error-handling-strategy)
12. [Configuration Reference](#12-configuration-reference)
13. [Open Questions and Assumptions](#13-open-questions-and-assumptions)
14. [Out of Scope](#14-out-of-scope)

---

## 1. Purpose and Scope

### 1.1 Purpose

This document specifies the design of the **FDE Financial Analysis Automation** system — a multi-agent pipeline that researches a financial topic, synthesises structured analysis, identifies regulatory risks, scores investment approaches, and produces a client-ready report.

This SDD is the authoritative specification from which the system shall be built. Code must trace back to requirements defined herein. Any deviation from this specification requires a documented change request and version increment.

### 1.2 Scope

This document covers:

- System architecture and technology selection rationale
- Agent interface contracts (inputs, outputs, behaviour constraints)
- External tool layer contracts (search, scraping, file I/O)
- LLM provider strategy and fallback logic
- REST API contract
- Shared state schema
- Error handling strategy
- Configuration surface

### 1.3 Definitions

| Term | Definition |
|---|---|
| Agent | A discrete processing node in the LangGraph pipeline responsible for a single analytical function |
| MCP Tool | A sandboxed external I/O function in `tools.py`; the only permitted path for network or filesystem access |
| Run | A single end-to-end pipeline execution identified by a UUID (`run_id`) |
| Task tier | A classification (`heavy`, `medium`, `light`) used to select the appropriate LLM model |
| QA loop | An optional re-execution of Agent 3 through Agent 7 when QA score falls below threshold |

---

## 2. Business Requirements

| ID | Requirement |
|---|---|
| BR-01 | The system SHALL enable non-technical analysts to submit a financial topic and receive a structured, client-ready report without manual research. |
| BR-02 | The system SHALL cite sources for every material claim in the output report. |
| BR-03 | The system SHALL identify regulatory risks relevant to the submitted topic and industry. |
| BR-04 | The system SHALL score competing investment/architecture approaches against a consistent, auditable decision matrix. |
| BR-05 | The system SHALL operate within defined LLM cost budgets by routing to free-tier providers before incurring paid API costs where possible. |
| BR-06 | The system SHALL produce output files (Markdown report, CSV decision matrix) that can be downloaded programmatically or via browser. |
| BR-07 | The system SHALL allow callers to monitor pipeline progress without polling blocking the server. |

---

## 3. Functional Requirements

### 3.1 Pipeline

| ID | Requirement |
|---|---|
| FR-01 | The system SHALL execute a sequential 7-agent pipeline: Research → Extraction → Analysis → Risk → Scoring → Report → QA. |
| FR-02 | The pipeline SHALL share a single typed state object across all agents. |
| FR-03 | Any agent failure SHALL halt further processing and route the run to a terminal error state. |
| FR-04 | The QA agent SHALL be capable of routing back to Agent 3 (Analysis) for at most `MAX_QA_ITERATIONS` additional passes. |
| FR-05 | The pipeline SHALL be invoked asynchronously; the caller SHALL receive a `run_id` immediately and poll for status. |

### 3.2 Research

| ID | Requirement |
|---|---|
| FR-06 | The research agent SHALL perform at least two searches per topic: one targeting regulatory/compliance content and one targeting implementation/best practices. |
| FR-07 | The research agent SHALL deduplicate results by URL. |
| FR-08 | The research agent SHALL return a maximum of 10 sources ranked by relevance. |
| FR-09 | Sources SHALL be assigned a relevance score: regulatory sources score higher than general sources. |

### 3.3 Content Extraction

| ID | Requirement |
|---|---|
| FR-10 | The extraction agent SHALL scrape all source URLs and strip non-content elements (navigation, footers, scripts, styles). |
| FR-11 | Scraping SHALL prefer semantic content containers (`<article>`, `<main>`) over full page body. |
| FR-12 | Each scraped page SHALL be truncated to a configurable character limit. |
| FR-13 | Known paywalled or blocked domains SHALL be skipped without consuming retry budget. |
| FR-14 | The extraction agent SHALL NOT make any LLM calls. |

### 3.4 Analysis

| ID | Requirement |
|---|---|
| FR-15 | The analysis agent SHALL synthesise extracted content into a structured output covering: Key Trends, Architectural Patterns, Regulatory Implications, and Strategic Implications. |
| FR-16 | On QA loop re-entry, the analysis agent SHALL incorporate QA feedback into the revised output. |

### 3.5 Risk Identification

| ID | Requirement |
|---|---|
| FR-17 | The risk agent SHALL identify between 6 and 10 material risks. |
| FR-18 | Each risk SHALL include: name, category, severity level, impact statement, mitigation control, responsible owner, and regulatory reference. |
| FR-19 | Valid risk levels SHALL be: Critical, High, Medium, Low. |
| FR-20 | Valid risk categories SHALL be: Regulatory, Operational, Technology, Strategic. |
| FR-21 | Invalid level or category values SHALL be normalised to defaults rather than causing pipeline failure. |

### 3.6 Investment Scoring

| ID | Requirement |
|---|---|
| FR-22 | The scoring agent SHALL evaluate between 3 and 4 named approaches. |
| FR-23 | Each approach SHALL be scored 0–10 on six criteria: regulatory compliance, security/privacy, scalability, TCO, implementation risk, and vendor maturity. |
| FR-24 | Weighted totals SHALL be recalculated server-side; model-supplied weighted totals SHALL be overridden. |
| FR-25 | Any approach scoring below 5 on regulatory compliance SHALL receive a verdict of "Not Recommended". |
| FR-26 | The scoring weight distribution SHALL be: regulatory_compliance 25%, security_privacy 20%, scalability 15%, tco 15%, implementation_risk 15%, vendor_maturity 10%. |

### 3.7 Report Generation

| ID | Requirement |
|---|---|
| FR-27 | The report agent SHALL produce a Markdown report containing: Executive Summary, Key Findings, Risk Register, Decision Matrix, Recommendations, and Sources. |
| FR-28 | The report SHALL be written to disk under a deterministic filename derived from topic and `run_id`. |
| FR-29 | The decision matrix SHALL be written to disk as a CSV file. |
| FR-30 | Risk register and decision matrix SHALL be pre-formatted as Markdown tables before being passed to the LLM, to ensure consistent output. |

### 3.8 QA Review

| ID | Requirement |
|---|---|
| FR-31 | The QA agent SHALL score the report against a 5-category rubric with a maximum score of 10. |
| FR-32 | A score at or above `QA_PASS_THRESHOLD` SHALL route the run to END. |
| FR-33 | A score below threshold with iterations remaining SHALL route back to Agent 3 with structured feedback. |
| FR-34 | The QA rubric categories SHALL be: Source Grounding, Risk Completeness, Regulatory Coverage, Recommendation Quality, Executive Clarity. |

---

## 4. Non-Functional Requirements

| ID | Category | Requirement |
|---|---|---|
| NFR-01 | Cost | The system SHALL prefer zero-cost providers (DuckDuckGo for search, Groq free tier for LLM) before invoking paid providers. |
| NFR-02 | Cost | The system SHALL skip OpenRouter when remaining credit falls below `OR_SKIP_CREDIT_USD`. |
| NFR-03 | Reliability | The system SHALL implement a three-provider LLM fallback chain so that no single provider outage causes total pipeline failure. |
| NFR-04 | Reliability | The pipeline SHALL enforce a recursion limit to prevent runaway graph execution. |
| NFR-05 | Performance | Web scraping SHALL execute concurrently across source URLs. |
| NFR-06 | Performance | Individual scrape requests SHALL time out after 15 seconds. |
| NFR-07 | Maintainability | All external I/O (network, filesystem) SHALL be isolated in the MCP tool layer. Agents SHALL NOT make direct network or filesystem calls. |
| NFR-08 | Maintainability | All configuration SHALL be driven by environment variables with documented defaults. |
| NFR-09 | Security | SSL verification behaviour SHALL be configurable via environment variable to support corporate proxy environments. |
| NFR-10 | Observability | Every pipeline run SHALL expose real-time status including: current agent, iteration count, progress percentage, active provider, and error count. |

---

## 5. System Architecture

### 5.1 Layer Diagram

```
┌─────────────────────────────────────────────┐
│              Client (HTTP)                  │
│    PowerShell · curl · Browser · Scheduler  │
└────────────────────┬────────────────────────┘
                     │ REST
┌────────────────────▼────────────────────────┐
│           FastAPI  —  main.py               │
│  POST /run-analysis  GET /status            │
│  GET /results        GET /download          │
│  GET /credits        GET /health            │
└────────────────────┬────────────────────────┘
                     │ BackgroundTask
┌────────────────────▼────────────────────────┐
│      LangGraph Pipeline  —  workflow.py     │
│  research → extract → analyse → risk →      │
│  score → report → qa                        │
│  (error_node terminal on any failure)       │
└───────┬─────────────────────┬───────────────┘
        │ LLM calls           │ Tool calls
┌───────▼──────────┐  ┌───────▼───────────────┐
│ groq_fallback.py │  │  mcp_server/tools.py  │
│ OpenRouter →     │  │  search_sources()     │
│ Groq →           │  │  scrape_content()     │
│ Anthropic        │  │  write_report()       │
└──────────────────┘  │  save_decision_matrix()│
                      └───────────────────────┘
```

### 5.2 Technology Decisions

The following technology choices are approved for this build. Changes require a revised SDD version.

| Decision | Choice | Rationale | Alternatives Considered |
|---|---|---|---|
| LLM orchestration | LangGraph | Stateful graph with typed state, conditional edges, built-in loop control | CrewAI (insufficient state control), AutoGen (heavyweight), raw chains (no retry routing) |
| Primary LLM provider | OpenRouter | No daily quota cap, access to multiple frontier models under one key | OpenAI direct (no fallback), Anthropic direct (no model variety) |
| Secondary LLM | Groq | High throughput, free tier, OpenAI-compatible API | Together AI (requires card), HuggingFace Inference (lower throughput) |
| Final LLM fallback | Anthropic | Reliable uptime, no daily quota, own SDK | Azure OpenAI (requires Azure account) |
| Web search | DuckDuckGo (DDGS) | No API key required, no per-query cost | SerpAPI (paid), Bing Search API (requires Azure), Brave Search (API key required) |
| Web scraping | BeautifulSoup + requests | Lightweight, no API key, corporate proxy support via env vars | Playwright (requires browser install), Scrapy (heavyweight) |
| API framework | FastAPI | Native async, automatic OpenAPI docs, BackgroundTasks, Pydantic validation | Flask (no native async), Django (heavyweight) |
| State management | TypedDict | Type-safe, LangGraph-native, low overhead | Pydantic model (serialisation overhead), dataclass (less LangGraph-compatible) |

---

## 6. Agent Specifications

Agents are defined here by their **interface contract**: required inputs, guaranteed outputs, and observable behaviour constraints. Implementation detail below this level is at the discretion of the developer, provided all contract terms are satisfied.

---

### Agent 1 — Research Analyst

**Source file:** `backend/agents/research_agent.py`  
**Role:** Discover relevant, high-quality sources for the given topic.

#### 6.1.1 Input Contract

| State Key | Type | Required | Description |
|---|---|---|---|
| `topic` | str | Yes | Financial topic to research |
| `industry` | str | Yes | Industry context (e.g. "Banking / Financial Services") |

#### 6.1.2 Output Contract

| State Key | Type | Guarantee |
|---|---|---|
| `sources` | list[dict] | SHALL contain 0–10 items; empty list on search failure |
| `current_agent` | str | SHALL be set to this agent's node name |

**Source item schema:**

```python
{
    "title":           str,   # page title from search result
    "url":             str,   # canonical URL
    "summary":         str,   # snippet, max 300 characters
    "regulatory_flag": bool,  # True if regulatory keyword detected in snippet
    "relevance_score": float  # 8.5 for regulatory sources, 7.5 for general
}
```

#### 6.1.3 Behaviour Constraints

- SHALL execute two searches per run (regulatory angle, implementation angle)
- SHALL deduplicate results by URL before scoring
- SHALL sort results by `relevance_score` descending
- SHALL return at most 10 sources
- On `SearchError`, SHALL return `sources=[]` and allow the pipeline to continue
- LLM task tier: `light`; max_tokens: 1024

---

### Agent 2 — Extraction Specialist

**Source file:** `backend/agents/extraction_agent.py`  
**Role:** Scrape and clean web content from discovered source URLs.

#### 6.2.1 Input Contract

| State Key | Type | Required |
|---|---|---|
| `sources` | list[dict] | Yes (may be empty) |

#### 6.2.2 Output Contract

| State Key | Type | Guarantee |
|---|---|---|
| `extracted_content` | str | Concatenated text from all successful scrapes; empty string if all fail |
| `extraction_metadata` | list[dict] | One entry per attempted URL |
| `current_agent` | str | SHALL be set to this agent's node name |

**Extraction metadata item schema:**

```python
{
    "url":        str,
    "char_count": int,
    "status":     "success" | "failed",
    "error":      str | None
}
```

#### 6.2.3 Behaviour Constraints

- SHALL scrape all source URLs concurrently
- SHALL strip non-content elements: navigation, footers, scripts, styles
- SHALL prefer `<article>` or `<main>` over full `<body>`
- SHALL truncate each page to `SCRAPE_MAX_CHARS`
- SHALL skip hard-blocked domains before making any network request (see §7.2)
- SHALL NOT make any LLM calls (zero quota cost)

---

### Agent 3 — Enterprise Architect (Analysis)

**Source file:** `backend/agents/analysis_agent.py`  
**Role:** Synthesise extracted content into a structured financial analysis.

#### 6.3.1 Input Contract

| State Key | Type | Required | Notes |
|---|---|---|---|
| `topic` | str | Yes | |
| `industry` | str | Yes | |
| `extracted_content` | str | Yes | May be empty |
| `qa_feedback` | str | No | Present only on QA loop re-entry |
| `iteration_count` | int | Yes | 0 on first pass |

#### 6.3.2 Output Contract

| State Key | Type | Guarantee |
|---|---|---|
| `analysis` | str | SHALL contain all four required sections |
| `current_agent` | str | SHALL be set to this agent's node name |

**Required output sections:**

```
## Key Trends
## Architectural Patterns
## Regulatory Implications
## Strategic Implications
```

#### 6.3.3 Behaviour Constraints

- On QA loop re-entry (`iteration_count > 0`), SHALL incorporate `qa_feedback` into the prompt
- LLM task tier: `heavy`; max_tokens: 2048
- System prompt SHALL be static (cached); topic/industry injected in user message only

---

### Agent 4 — Risk Specialist

**Source file:** `backend/agents/risk_agent.py`  
**Role:** Identify material risks with regulatory references and mitigations.

#### 6.4.1 Input Contract

| State Key | Type | Required |
|---|---|---|
| `topic` | str | Yes |
| `industry` | str | Yes |
| `analysis` | str | Yes |
| `sources` | list[dict] | Yes |

#### 6.4.2 Output Contract

| State Key | Type | Guarantee |
|---|---|---|
| `risks` | list[dict] | SHALL contain 6–10 items |
| `current_agent` | str | SHALL be set to this agent's node name |

**Risk item schema:**

```python
{
    "name":           str,   # 5–8 word risk name
    "category":       str,   # Regulatory | Operational | Technology | Strategic
    "level":          str,   # Critical | High | Medium | Low
    "impact":         str,   # 1–2 sentence impact description
    "mitigation":     str,   # specific, actionable control
    "owner":          str,   # responsible role (e.g. CTO, CRO)
    "regulatory_ref": str    # specific regulation or "N/A"
}
```

#### 6.4.3 Behaviour Constraints

- Invalid `level` values SHALL be normalised to `"Medium"`
- Invalid `category` values SHALL be normalised to `"Operational"`
- Model output wrapped in a dict (e.g. `{"risks": [...]}`) SHALL be unwrapped automatically
- LLM task tier: `heavy`; max_tokens: 2048

---

### Agent 5 — Investment Strategist (Scoring)

**Source file:** `backend/agents/scoring_agent.py`  
**Role:** Score investment/architecture approaches against a weighted decision matrix.

#### 6.5.1 Input Contract

| State Key | Type | Required |
|---|---|---|
| `topic` | str | Yes |
| `industry` | str | Yes |
| `analysis` | str | Yes |
| `risks` | list[dict] | Yes |

#### 6.5.2 Output Contract

| State Key | Type | Guarantee |
|---|---|---|
| `decision_matrix` | list[dict] | SHALL contain 3–4 items, sorted by `weighted_total` descending |
| `current_agent` | str | SHALL be set to this agent's node name |

**Decision matrix item schema:**

```python
{
    "approach":               str,    # approach name
    "regulatory_compliance":  int,    # 0–10
    "security_privacy":       int,    # 0–10
    "scalability":            int,    # 0–10
    "tco":                    int,    # 0–10 (10 = lowest cost)
    "implementation_risk":    int,    # 0–10 (10 = lowest risk)
    "vendor_maturity":        int,    # 0–10
    "weighted_total":         float,  # server-recalculated; model value overridden
    "verdict":                str     # Recommended | Conditional | Not Recommended
}
```

#### 6.5.3 Behaviour Constraints

- `weighted_total` SHALL be recalculated server-side using the weights in FR-26; model value SHALL be discarded
- Any approach with `regulatory_compliance < 5` SHALL receive `verdict = "Not Recommended"`
- Matrix SHALL be sorted by `weighted_total` descending before returning
- LLM task tier: `medium`; max_tokens: 1024

---

### Agent 6 — Report Writer

**Source file:** `backend/agents/report_writer_agent.py`  
**Role:** Produce a client-ready executive report in Markdown, and write output files to disk.

#### 6.6.1 Input Contract

| State Key | Type | Required |
|---|---|---|
| `topic` | str | Yes |
| `industry` | str | Yes |
| `analysis` | str | Yes |
| `risks` | list[dict] | Yes |
| `decision_matrix` | list[dict] | Yes |
| `sources` | list[dict] | Yes |
| `run_id` | str | Yes |

#### 6.6.2 Output Contract

| State Key | Type | Guarantee |
|---|---|---|
| `report` | str | SHALL contain all six required sections |
| `report_path` | str | Absolute path to written `.md` file |
| `matrix_path` | str | Absolute path to written `.csv` file |
| `current_agent` | str | SHALL be set to this agent's node name |

**Required report sections:**

```
## Executive Summary
## Key Findings
## Risk Register
## Decision Matrix
## Recommendations
## Sources
```

#### 6.6.3 Behaviour Constraints

- Risk register and decision matrix SHALL be pre-formatted as Markdown tables before LLM invocation
- Sources passed to the LLM SHALL be capped at 15 (first 15 by current sort order)
- Output filename SHALL be derived from `make_file_slug(topic, run_id)` (see §7.5)
- LLM task tier: `heavy`; max_tokens: 4096

---

### Agent 7 — QA Director

**Source file:** `backend/agents/qa_agent.py`  
**Role:** Score the report against a quality rubric and either approve or return with feedback.

#### 6.7.1 Input Contract

| State Key | Type | Required |
|---|---|---|
| `topic` | str | Yes |
| `report` | str | Yes |
| `analysis` | str | Yes |
| `risks` | list[dict] | Yes |
| `decision_matrix` | list[dict] | Yes |

#### 6.7.2 Output Contract

| State Key | Type | Guarantee |
|---|---|---|
| `qa_score` | float | 0.0–10.0 |
| `qa_feedback` | str | Actionable feedback; empty string on pass |
| `qa_pass` | bool | True if `qa_score >= QA_PASS_THRESHOLD` |
| `iteration_count` | int | Incremented by 1 from input value |
| `current_agent` | str | SHALL be set to this agent's node name |

#### 6.7.3 QA Scoring Rubric

Each category is worth 2 points (maximum score: 10):

| Category | Pass Criteria |
|---|---|
| Source Grounding | Every material claim references a source |
| Risk Completeness | All Critical/High risks include a mitigation |
| Regulatory Coverage | At least one specific regulation cited |
| Recommendation Quality | Each recommendation includes an owner and a 90-day next step |
| Executive Clarity | Executive summary is ≤200 words and free of jargon |

#### 6.7.4 Routing Logic

- `qa_score >= QA_PASS_THRESHOLD` → route to END
- `qa_score < QA_PASS_THRESHOLD` AND `iteration_count < MAX_QA_ITERATIONS` → route to Agent 3 with `qa_feedback`
- `qa_score < QA_PASS_THRESHOLD` AND `iteration_count >= MAX_QA_ITERATIONS` → route to END (accept as-is)
- LLM task tier: `light`; max_tokens: 300

---

## 7. MCP Tool Layer Contract

All external I/O SHALL be implemented in `backend/mcp_server/tools.py`. This is the only module permitted to make network or filesystem calls. Agents SHALL call these functions; they SHALL NOT replicate their behaviour inline.

### 7.1 `search_sources(query, industry, max_results) → list[dict]`

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `query` | str | Yes | — | Search query |
| `industry` | str | No | `""` | Appended to query if not already present |
| `max_results` | int | No | 10 | Maximum results to return |

**Returns:** List of source dicts matching the schema in §6.1.2.  
**Raises:** `SearchError` on blank query, provider failure, or zero results.

---

### 7.2 `scrape_content(url, max_chars) → dict`

| Parameter | Type | Required | Default | Description |
|---|---|---|---|---|
| `url` | str | Yes | — | Must begin with `http` |
| `max_chars` | int | No | `cfg.SCRAPE_MAX_CHARS` | Character cap per page |

**Returns:** `{"url": str, "content": str, "char_count": int, "status": "success"}`  
**Raises:** `ScrapeError` on blocked domain, timeout, HTTP error, non-HTML response, or content below 50 characters.

**Hard-blocked domains** (skipped before any network call):

- `researchgate.net`, `bloomberg.com`, `ft.com`, `wsj.com`, `spglobal.com`, `economist.com`, `hbr.org`

**SSL configuration:**  
- `CUSTOM_CA_BUNDLE=/path/to/ca.crt` — use custom CA bundle  
- `DISABLE_SSL_VERIFY=true` — disable verification (development/proxy environments only)

---

### 7.3 `write_report(content, run_id, topic) → str`

Writes the Markdown report to `OUTPUT_DIR` using the filename slug from `make_file_slug(topic, run_id)`.  
**Returns:** Absolute path to the written file.  
**Raises:** `FileWriteError` on empty content.

---

### 7.4 `save_decision_matrix(matrix, run_id, topic) → str`

Writes the decision matrix to `OUTPUT_DIR` as a CSV file.  
**Returns:** Absolute path to the written file.  
**Raises:** `FileWriteError` on empty matrix or inconsistent row keys.  
**Pre-condition:** All rows in `matrix` SHALL share the same set of keys.

---

### 7.5 `make_file_slug(topic, run_id) → str`

Converts topic to a filesystem-safe slug. Falls back to `run_id` if topic is empty or blank.

**Examples:**

| Input topic | Output slug |
|---|---|
| `"AI Agent Adoption for Banking"` | `"ai_agent_adoption_for_banking"` |
| `"GLP-1 Weight Loss Market 2026"` | `"glp_1_weight_loss_market_2026"` |
| `""` | `"<run_id_uuid>"` |

---

## 8. LLM Fallback Chain

Implemented in `backend/groq_fallback.py`.

### 8.1 Provider Chain

The system SHALL attempt LLM calls in the following order, advancing to the next provider only on exhaustion, credit threshold breach, or unrecoverable error:

```
① OpenRouter (paid — primary)
   heavy tasks  → openai/gpt-4o
   medium/light → openai/gpt-4o-mini
       ↓ on: all models exhausted | 402 error | balance < OR_SKIP_CREDIT_USD

② Groq (free — secondary)
   openai/gpt-oss-120b
   openai/gpt-oss-20b
   openai/gpt-oss-safeguard-20b
   qwen/qwen3.8-27b
       ↓ on: all Groq models exhausted

③ Anthropic (paid — final backstop)
   claude-sonnet-4-6
       ↓ on: failure

ModelExhaustedError → agent sets status=error → error_node → END
```

### 8.2 Task Tier to Model Mapping

| Task tier | Starting model | Assigned agents |
|---|---|---|
| `heavy` | gpt-4o (OpenRouter) | Analysis, Risk, Report Writer |
| `medium` | gpt-4o-mini (OpenRouter) | Scoring |
| `light` | gpt-4o-mini (OpenRouter) | Research, QA |

### 8.3 Rate Limiting Requirements

The implementation SHALL enforce the following rate limiting behaviour on Groq:

- Minimum inter-call gap: `GROQ_INTER_CALL_DELAY_SEC` (default 2.0s)
- After HTTP 429: additional wait of `GROQ_429_EXTRA_WAIT_SEC` (default 5.0s) plus random jitter of 1–3 seconds
- All client instances SHALL set `max_retries=1` to prevent SDK-level retry compounding
- OpenRouter timeout: 30 seconds; Groq timeout: 60 seconds

### 8.4 Credit Monitoring Requirements

- OpenRouter balance SHALL be fetched from `GET /api/v1/auth/key` and cached for `OR_CREDIT_CACHE_TTL_SEC` seconds
- Balance SHALL be force-refreshed after every successful OpenRouter call and after any 402 response
- At balance `< OR_LOW_CREDIT_WARN_USD`: log WARNING; calls continue
- At balance `< OR_SKIP_CREDIT_USD`: skip all OpenRouter models; route immediately to Groq

> **Note:** Per-key credit limit is distinct from account balance. Operators SHALL configure key caps at `openrouter.ai/keys`.

---

## 9. API Specification

Base URL: `http://localhost:8000`  
All request and response bodies SHALL use `application/json`.

---

### POST /run-analysis

Initiates a new pipeline run asynchronously.

**Request body:**

```json
{
  "topic":    "AI Agent Adoption for Banking",
  "industry": "Banking / Financial Services"
}
```

**Validation:** Both fields are required. Empty `topic` SHALL return HTTP 422.

**Response 202 — Accepted:**

```json
{
  "run_id":     "dad2fe02-59f9-4682-bf54-e9c70cb6e322",
  "status":     "started",
  "poll_url":   "/status/dad2fe02-59f9-4682-bf54-e9c70cb6e322",
  "result_url": "/results/dad2fe02-59f9-4682-bf54-e9c70cb6e322"
}
```

---

### GET /status/{run_id}

Returns current pipeline progress for a run.

**Response 200:**

```json
{
  "run_id":          "dad2fe02-59f9-4682-bf54-e9c70cb6e322",
  "status":          "running",
  "current_agent":   "analysis_node",
  "iteration_count": 0,
  "progress_pct":    45,
  "provider_used":   "groq/openai/gpt-oss-120b",
  "error_count":     1,
  "error":           null
}
```

**Status lifecycle:** `initialized` → `running` → `completed` | `error`

**Response 404:** Run ID not found.

---

### GET /results/{run_id}

Returns the full pipeline result for a completed run.

**Response 200 (status = completed):**

Full `AnalysisState` dict (see §10), plus:

```json
{
  "download_files": {
    "report": "ai_agent_adoption_for_banking_report.md",
    "matrix": "ai_agent_adoption_for_banking_matrix.csv"
  }
}
```

**Response 409 — Conflict (status = running):**

```json
{
  "error": "Run is still in progress — poll /status/{run_id} first"
}
```

**Response 404:** Run ID not found.

---

### GET /download/{run_id}/{filename}

Downloads a report (`.md`) or decision matrix (`.csv`) file for the specified run.

**Constraint:** `filename` SHALL match the topic slug for the given `run_id`. Mismatched filenames SHALL return HTTP 404.

---

### GET /credits

Returns current OpenRouter credit status.

**Response 200:**

```json
{
  "remaining_usd": 9.82,
  "used_usd":      0.18,
  "limit_usd":     10.0,
  "status":        "ok",
  "top_up_url":    "https://openrouter.ai/credits",
  "error":         null
}
```

**Status values:** `ok` | `low` (< $0.50) | `critical` (< $0.10) | `unknown` | `error`

---

### GET /health

Returns system readiness including provider availability and credit status.

**Response 200:**

```json
{
  "status":   "ok",
  "problems": [],
  "providers": {
    "openrouter": true,
    "groq":       true,
    "anthropic":  true
  },
  "openrouter_credits": {
    "remaining_usd": 9.82,
    "status":        "ok"
  },
  "llm": "OpenRouter gpt-4o → Groq → Anthropic claude-sonnet-4-6"
}
```

**`status`:** `"ok"` when `problems` is empty; `"degraded"` otherwise.

---

## 10. State Schema

The following `TypedDict` is the single shared state object passed through the LangGraph pipeline. All agents read from and write to this structure. No agent SHALL introduce ad hoc state keys outside this schema.

```python
class AnalysisState(TypedDict):
    # Inputs
    topic:               str           # submitted financial topic
    industry:            str           # submitted industry context
    run_id:              str           # UUID assigned at pipeline start

    # Agent outputs
    sources:             list[dict]    # Agent 1 output
    extracted_content:   str           # Agent 2 output
    extraction_metadata: list[dict]    # Agent 2 output
    analysis:            str           # Agent 3 output
    risks:               list[dict]    # Agent 4 output
    decision_matrix:     list[dict]    # Agent 5 output
    report:              str           # Agent 6 output
    report_path:         str           # Agent 6 output
    matrix_path:         str           # Agent 6 output

    # QA loop
    qa_score:            float         # Agent 7 output; 0.0–10.0
    qa_feedback:         str           # Agent 7 output; empty string on pass
    qa_pass:             bool          # Agent 7 output
    iteration_count:     int           # incremented by Agent 7 on each pass

    # Pipeline metadata
    status:              str           # initialized | running | completed | error
    current_agent:       str           # node name of currently executing agent
    error:               str | None    # error message; None on success
    provider_used:       str           # last successful LLM provider string
    error_count:         int           # count of non-fatal fallback events
```

---

## 11. Error Handling Strategy

### 11.1 Agent-Level Error Contract

Every agent SHALL wrap its primary LLM call in a try/except block. On failure, the agent SHALL return a state dict with `status="error"` and a descriptive `error` string. The agent SHALL NOT raise exceptions to the graph layer.

```python
# Required error return structure
return {
    "status":        "error",
    "error":         "<descriptive message>",
    "current_agent": "<node_name>"
}
```

### 11.2 Graph-Level Error Routing

Every LangGraph edge SHALL use a routing function that checks `state.get("status") == "error"` before advancing. On error detection, the routing function SHALL redirect to `error_node`.

`error_node` is a no-op terminal node. Its sole function is to route to `END`.

### 11.3 Recursion Safety

The pipeline graph SHALL be invoked with `recursion_limit=50`. Normal runs use between 7 and 14 node invocations. This limit provides headroom for QA loops while preventing runaway execution.

---

## 12. Configuration Reference

All configuration is managed in `backend/config.py`, driven by environment variables. A `.env` file is the expected delivery mechanism in development and staging.

| Variable | Default | Required | Description |
|---|---|---|---|
| `OPENROUTER_API_KEY` | — | Yes | Paid OpenRouter API key |
| `GROQ_API_KEY` | — | Recommended | Free Groq API key |
| `ANTHROPIC_API_KEY` | — | Recommended | Anthropic backstop key |
| `LLM_MODEL` | `openai/gpt-4o` | No | Primary model identifier |
| `LLM_TEMPERATURE` | `0.3` | No | LLM sampling temperature |
| `OUTPUT_DIR` | `./outputs` | No | Directory for report and matrix output files |
| `LOG_LEVEL` | `INFO` | No | Python logging level |
| `SCRAPE_MAX_CHARS` | `8000` | No | Per-page character cap for scraping |
| `QA_PASS_THRESHOLD` | `7.0` | No | Minimum QA score to accept a report |
| `MAX_QA_ITERATIONS` | `1` | No | Maximum QA re-entry loops |
| `GROQ_INTER_CALL_DELAY_SEC` | `2.0` | No | Minimum seconds between Groq calls |
| `GROQ_429_EXTRA_WAIT_SEC` | `5.0` | No | Additional wait on Groq HTTP 429 |
| `OR_LOW_CREDIT_WARN_USD` | `0.50` | No | OpenRouter credit warning threshold |
| `OR_SKIP_CREDIT_USD` | `0.10` | No | OpenRouter credit skip threshold |
| `OR_CREDIT_CACHE_TTL_SEC` | `60` | No | OpenRouter balance cache TTL in seconds |
| `CUSTOM_CA_BUNDLE` | — | No | Path to custom CA certificate bundle |
| `DISABLE_SSL_VERIFY` | `false` | No | Disable SSL verification (non-production only) |

---

## 13. Open Questions and Assumptions

The following items were unresolved or assumed at the time of writing. They SHALL be resolved before development begins on the affected components.

| ID | Item | Owner | Status |
|---|---|---|---|
| OQ-01 | Should the hard-blocked domain list (`bloomberg.com`, `ft.com`, etc.) be configurable at runtime, or is a hardcoded list acceptable? | Product | Open |
| OQ-02 | What is the expected SLA for a complete pipeline run under normal conditions (no QA loop)? This affects timeout choices at the API layer. | Engineering | Open |
| OQ-03 | Should `GET /results` return partial results (e.g. analysis only) while the run is still in progress, or continue to return 409 until completion? | Product | Open |
| OQ-04 | Is the `DISABLE_SSL_VERIFY=true` option acceptable for production environments, or must it be restricted to non-production? | Security | Open |
| OQ-05 | Should reports be retained indefinitely in `OUTPUT_DIR`, or is a retention/cleanup policy required? | Ops | Open |

**Assumptions:**

- A1: The system will be deployed on a single host; horizontal scaling is out of scope for this version.
- A2: `run_id` values are UUIDs generated at request time and are not user-supplied.
- A3: The caller is responsible for polling `/status` at an appropriate interval; server-sent events or WebSocket push are out of scope.
- A4: The Groq free tier is sufficient for secondary LLM load in normal operation; burst scenarios may exceed Groq rate limits and fall through to Anthropic.

---

## 14. Out of Scope

The following are explicitly excluded from this version:

- Authentication or authorisation on any API endpoint
- Multi-tenancy or per-user run isolation
- Horizontal scaling or load balancing
- A user-facing web frontend (API-only)
- Persistent storage of runs beyond the in-process run registry and `OUTPUT_DIR` files
- Support for non-English topics or reports
- Streaming of LLM output tokens to the client
