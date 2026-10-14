# Software Design Document — FDE Financial Analysis Pipeline

**Version:** 1.0  
**Status:** Final  

---

## 1. Purpose and Scope

This document describes the design of the FDE Financial Analysis Pipeline — an autonomous 7-agent AI system that produces client-ready executive reports on any financial technology topic. It covers agent specifications, MCP tool contracts, API specifications, state management, error handling, and the QA feedback loop.

---

## 2. System Context

The system accepts a `topic` and optional `industry` from a REST client, runs a LangGraph pipeline of 7 agents, and produces two output artefacts: a markdown executive report and a CSV decision matrix. All LLM inference is handled by Groq. All web research is handled by DuckDuckGo. No paid APIs are required beyond a free Groq account.

---

## 3. State Management

All agents communicate through a single shared state object defined as a `TypedDict` in `backend/state.py`.

### 3.1 AnalysisState Schema

| Field | Type | Set by | Description |
|-------|------|--------|-------------|
| `topic` | `str` | Client | Research topic (required) |
| `industry` | `str` | Client | Target industry (default: Banking) |
| `run_id` | `str` | main.py | UUID for this pipeline run |
| `sources` | `list[dict]` | research_agent | Scored, filtered source list |
| `extracted_content` | `str` | extraction_agent | Concatenated scraped text |
| `extraction_metadata` | `list[dict]` | extraction_agent | Per-URL scrape status |
| `analysis` | `str` | analysis_agent | Structured markdown analysis |
| `risks` | `list[dict]` | risk_agent | Risk register entries |
| `decision_matrix` | `list[dict]` | scoring_agent | Scored investment approaches |
| `report` | `str` | report_writer_agent | Full executive report (markdown) |
| `report_path` | `str` | report_writer_agent | Absolute path to .md file |
| `matrix_path` | `str` | report_writer_agent | Absolute path to .csv file |
| `qa_score` | `float` | qa_agent | Report quality score (0-10) |
| `qa_feedback` | `str` | qa_agent | QA deduction summary |
| `qa_pass` | `bool` | qa_agent | True if score >= QA_PASS_THRESHOLD |
| `status` | `str` | various | initialized / running / completed / error |
| `current_agent` | `str` | every agent | Node name of last agent to run |
| `iteration_count` | `int` | workflow | Number of QA re-analysis loops |
| `error` | `str \| None` | various | Error message if status == error |

### 3.2 State Initialisation

`initial_state(topic, industry, run_id)` in `backend/state.py` produces a zero-value state. Every list field gets a new list instance per call (no shared mutable defaults).

---

## 4. Agent Specifications

### 4.1 Agent 1 — Research Analyst (`research_agent.py`)

**Node name:** `research_node`  
**Reads from state:** `topic`, `industry`  
**Writes to state:** `sources`, `current_agent`

**Behaviour:**
1. Validates that `topic` is non-empty; returns error state if not.
2. Runs two DuckDuckGo queries via `search_sources()`:
   - `"{topic} regulation compliance framework {industry}"`
   - `"{topic} implementation best practices risk"`
3. Deduplicates results by URL.
4. Calls Groq with a scoring/filtering system prompt to keep sources with relevance ≥ 6/10 and flag regulatory sources.
5. Falls back to unscored raw results if Groq call fails.

**System prompt contract:** Returns a JSON array. Each object must have: `title`, `url`, `summary`, `regulatory_flag` (bool), `relevance_score` (float 1-10).

**Error handling:** `SearchError` from all queries → returns error state. Individual query failures are logged and skipped (non-fatal). Groq failure → falls back to raw results (non-fatal).

---

### 4.2 Agent 2 — Content Extraction Specialist (`extraction_agent.py`)

**Node name:** `extractor_node`  
**Reads from state:** `sources`  
**Writes to state:** `extracted_content`, `extraction_metadata`, `current_agent`

**Behaviour:**
1. If `sources` is empty, returns empty content immediately.
2. For each source URL, calls `scrape_content()`.
3. Successful scrapes are concatenated with `[Source: {url}]` headers.
4. Failed scrapes are logged and recorded in `extraction_metadata` with `status: failed`.
5. Pipeline continues regardless of how many sources fail.

**Error handling:** `ScrapeError` per URL is caught individually. No single failure aborts the pipeline.

---

### 4.3 Agent 3 — Principal Enterprise Architect (`analysis_agent.py`)

**Node name:** `analysis_node`  
**Reads from state:** `topic`, `industry`, `extracted_content`  
**Writes to state:** `analysis`, `current_agent`

**Behaviour:**
1. If `extracted_content` is empty, returns a canned "Insufficient data" message.
2. Truncates content to 12,000 characters before sending to Groq.
3. Calls Groq with a synthesis prompt requiring exactly four headings.

**System prompt contract:** Response must use exactly these headings:
- `## Key Trends`
- `## Architectural Patterns`
- `## Regulatory Implications`
- `## Strategic Implications`

All findings must be grounded in the provided content only. "Insufficient data from sources." is the required phrase when evidence is absent.

**Error handling:** Groq failure → returns error state (fatal; pipeline halts).

---

### 4.4 Agent 4 — Risk & Governance Specialist (`risk_agent.py`)

**Node name:** `risk_node`  
**Reads from state:** `topic`, `industry`, `analysis`, `sources`  
**Writes to state:** `risks`, `current_agent`

**Behaviour:**
1. If `analysis` is empty, returns empty risks list immediately.
2. Calls Groq requesting 6-10 risk objects as a JSON array.
3. Validates `level` enum (`Critical|High|Medium|Low`) — defaults to `Medium` if invalid.
4. Validates `category` enum (`Regulatory|Operational|Technology|Strategic`) — defaults to `Operational` if invalid.
5. JSON parse failure → returns empty list (non-fatal). Groq failure → returns error state (fatal).

**System prompt contract:** Each risk object must have: `name`, `category`, `level`, `impact`, `mitigation`, `owner`, `regulatory_ref`. Mitigations must be specific (not generic like "implement controls").

---

### 4.5 Agent 5 — Technology Investment Strategist (`scoring_agent.py`)

**Node name:** `scoring_node`  
**Reads from state:** `topic`, `industry`, `analysis`, `risks`  
**Writes to state:** `decision_matrix`, `current_agent`

**Behaviour:**
1. Calls Groq requesting 3-4 investment approach objects as a JSON array.
2. **Server-side recalculation:** ignores `weighted_total` from LLM; recalculates using fixed weights:

| Criterion | Weight |
|-----------|--------|
| `regulatory_compliance` | 0.25 |
| `security_privacy` | 0.20 |
| `scalability` | 0.15 |
| `tco` | 0.15 |
| `implementation_risk` | 0.15 |
| `vendor_maturity` | 0.10 |

3. Validates `verdict` enum (`Recommended|Conditional|Not Recommended`) — defaults to `Conditional` if invalid.
4. Sorts matrix by `weighted_total` descending.
5. JSON parse failure → returns empty matrix (non-fatal). Groq failure → returns error state (fatal).

**Regulatory constraint enforced by prompt:** No approach scoring below 5 on `regulatory_compliance` may be `Recommended`.

---

### 4.6 Agent 6 — Senior Management Consultant (`report_writer_agent.py`)

**Node name:** `report_node`  
**Reads from state:** `topic`, `industry`, `run_id`, `analysis`, `risks`, `decision_matrix`, `sources`  
**Writes to state:** `report`, `report_path`, `matrix_path`, `current_agent`

**Behaviour:**
1. Builds pre-formatted risk table and decision matrix table as markdown strings.
2. Calls Groq with a report-writing prompt specifying exact headings and word limits.
3. Writes report to `{OUTPUT_DIR}/{run_id}_report.md` via `write_report()`.
4. Writes decision matrix to `{OUTPUT_DIR}/{run_id}_matrix.csv` via `save_decision_matrix()` (non-fatal if this fails).

**System prompt contract:** Report must use exactly these headings:
- `# {topic} — Executive Analysis Report`
- `## Executive Summary` (≤200 words)
- `## Key Findings` (3-5 findings, each with source reference)
- `## Risk Register` (pre-formatted table)
- `## Decision Matrix` (pre-formatted table)
- `## Recommendations` (3-5 numbered, each with action, rationale, owner, 90-day first step)
- `## Sources` (numbered list)

**Error handling:** Groq failure or `FileWriteError` on report → returns error state (fatal). `FileWriteError` on matrix → logged and skipped (non-fatal).

---

### 4.7 Agent 7 — Quality Assurance Director (`qa_agent.py`)

**Node name:** `qa_node`  
**Reads from state:** `report`, `iteration_count`  
**Writes to state:** `qa_score`, `qa_feedback`, `qa_pass`, `status`, `current_agent`

**Behaviour:**
1. If `report` is empty, returns `qa_score: 0.0`, `qa_pass: False` immediately.
2. Calls Groq with a 5-category rubric (total 10 points).
3. Sets `qa_pass = (score >= QA_PASS_THRESHOLD)` (default 7.0).
4. Sets `status = "completed"` if `qa_pass` or `iteration_count >= MAX_QA_ITERATIONS`; else `"running"`.
5. On Groq failure, defaults to `score = 5.0` (non-fatal).

**QA Rubric:**

| Category | Max | Pass criteria |
|----------|-----|---------------|
| Executive Summary | 2 | ≤200 words, states recommendation, outcome, top risk |
| Source Citations | 2 | Every factual claim has a traceable source |
| Risk Specificity | 2 | Specific mitigation + regulatory ref per risk |
| Recommendation Actionability | 2 | Named owners + 90-day first steps |
| Decision Matrix | 2 | 3+ approaches, weighted scores, clear verdicts |

**Routing logic (in `workflow.py`):**
```
qa_pass == True  →  END
qa_pass == False AND iteration_count < MAX_QA_ITERATIONS  →  analysis_node
qa_pass == False AND iteration_count >= MAX_QA_ITERATIONS  →  END
```

---

## 5. MCP Tool Contracts

All external I/O is handled by `backend/mcp_server/tools.py`. Agents call these functions; they do not make network calls directly.

### 5.1 `search_sources(query, industry, max_results) -> list[dict]`

**Parameters:**
- `query: str` — search query (required, non-empty)
- `industry: str` — appended to query if non-empty (default: `""`)
- `max_results: int` — passed to DDGS (default: `10`)

**Returns:** List of source dicts, each with:
```python
{
    "title":            str,    # Page title
    "url":              str,    # Page URL (from DDGS "href")
    "summary":          str,    # Body text, capped at 300 chars
    "regulatory_flag":  bool,   # True if any REGULATORY_KEYWORD matched
    "relevance_score":  float,  # 8.5 if regulatory, else 7.5
}
```

**Raises:**
- `SearchError("Query cannot be empty")` — blank query
- `SearchError("DuckDuckGo failed: {e}")` — DDGS network/parse error
- `SearchError("0 results for: '{query}'")` — zero results returned

**Regulatory keywords:** `regulation`, `regulatory`, `compliance`, `basel`, `dora`, `sr 11-7`, `federal reserve`, `fca`, `occ`, `ffiec`, `eu ai act`, `gdpr`, `psd2`, `government`, `guidance`

---

### 5.2 `scrape_content(url, max_chars) -> dict`

**Parameters:**
- `url: str` — must start with `http`
- `max_chars: int` — character cap; falls back to `cfg.SCRAPE_MAX_CHARS` if not provided

**Returns:**
```python
{
    "url":        str,   # Input URL
    "content":    str,   # Cleaned plain text (strip tags removed)
    "char_count": int,   # Length of content string
    "status":     str,   # Always "success" if returned
}
```

**Raises:**
- `ScrapeError("Invalid URL: '{url}'")` — empty or non-http URL
- `ScrapeError("SCRAPE_MAX_CHARS is not configured")` — zero/None cap
- `ScrapeError("Scrape timeout: {url}")` — `requests.Timeout`
- `ScrapeError("Request failed: {url} ({e})")` — `requests.RequestException`
- `ScrapeError("403 bot-blocked: {url}")` — HTTP 403
- `ScrapeError("HTTP {code}: {url}")` — any non-200 status
- `ScrapeError("Non-HTML: {url}")` — non-`text/html` content type
- `ScrapeError("Content too short: {url}")` — extracted text < 50 chars

**Strip tags:** `script`, `style`, `nav`, `footer`, `header`, `aside`, `noscript`  
**Parser:** `html.parser` (stdlib, no extra install)  
**Truncation:** Content > `max_chars` is sliced and appended with `\n[...truncated]`

---

### 5.3 `write_report(content, run_id) -> str`

**Parameters:**
- `content: str` — report markdown (required, non-empty)
- `run_id: str` — sanitised with `os.path.basename()` before use

**Returns:** Absolute path to written file (`{OUTPUT_DIR}/{safe_id}_report.md`)

**Raises:**
- `FileWriteError("Empty content")` — blank or whitespace-only content

**Side effects:** Creates `OUTPUT_DIR` if it does not exist.

---

### 5.4 `save_decision_matrix(matrix, run_id) -> str`

**Parameters:**
- `matrix: list[dict]` — must be non-empty; all rows must have identical keys
- `run_id: str` — sanitised with `os.path.basename()`

**Returns:** Absolute path to written file (`{OUTPUT_DIR}/{safe_id}_matrix.csv`)

**Raises:**
- `FileWriteError("Empty matrix")` — empty list
- `FileWriteError("Matrix rows have inconsistent keys at index(es): {idxs}...")` — key mismatch

**Side effects:** Creates `OUTPUT_DIR` if it does not exist.

---

## 6. REST API Specification

**Base URL:** `http://localhost:8000`

### 6.1 `GET /health`

Returns the configured LLM backend and search provider.

**Response 200:**
```json
{
  "llm": "Groq/openai/gpt-oss-120b",
  "search": "DuckDuckGo (no key)"
}
```

---

### 6.2 `POST /run-analysis`

Starts a pipeline run asynchronously.

**Request body:**
```json
{
  "topic":    "string (required, non-empty after strip)",
  "industry": "string (optional, default: Banking / Financial Services)"
}
```

**Response 202:**
```json
{
  "run_id":   "uuid4-string",
  "status":   "started",
  "poll_url": "/status/{run_id}"
}
```

**Response 422:** Validation error (empty topic).

---

### 6.3 `GET /status/{run_id}`

Lightweight poll endpoint.

**Response 200:**
```json
{
  "run_id":        "uuid4-string",
  "status":        "running | completed | error",
  "current_agent": "research_node | extractor_node | ..."
}
```

**Response 404:** `run_id` not found in registry.

---

### 6.4 `GET /results/{run_id}`

Returns the full `AnalysisState` dict once the run is complete.

**Response 200:** Full state dict (all fields from section 3.1).  
**Response 404:** `run_id` not found.  
**Response 409:** Run is still in progress (`status == "running"`).

---

### 6.5 `GET /download/{run_id}/{filename}`

Serves a generated output file.

**Allowed filenames:** `{run_id}_report.md` or `{run_id}_matrix.csv` only. Any other filename returns 400 (security: prevents path traversal to arbitrary files in `OUTPUT_DIR`).

**Response 200:** File content with appropriate MIME type.  
**Response 400:** Filename not in allowed set.  
**Response 404:** File not found on disk (run may not have completed successfully).

---

## 7. Error Handling Strategy

| Failure type | Agent behaviour | Pipeline behaviour |
|---|---|---|
| Empty/invalid input | Return error state immediately | Halt at that node |
| LLM call failure | Return error state | Halt at that node |
| JSON parse failure | Return empty list / fallback | Continue with degraded data |
| Single search query failure | Log warning | Continue with remaining queries |
| Single scrape failure | Log warning, record in metadata | Continue with remaining URLs |
| Matrix CSV write failure | Log warning | Continue (report still written) |
| QA score below threshold | Route back to analysis_node | Re-analyse up to MAX_QA_ITERATIONS |

---

## 8. Configuration Reference

All settings in `backend/config.py`. Override via environment variables or `.env` file.

| Variable | Default | Validation |
|----------|---------|------------|
| `GROQ_API_KEY` | `""` | Required (validated at startup) |
| `GROQ_BASE_URL` | `https://api.groq.com/openai/v1` | Required |
| `LLM_MODEL` | `openai/gpt-oss-120b` | Required |
| `LLM_TEMPERATURE` | `0.3` | float |
| `LLM_MAX_TOKENS` | `2000` | int |
| `OUTPUT_DIR` | `./outputs` | Created on first write |
| `SCRAPE_MAX_CHARS` | `8000` | Must be > 0 |
| `QA_PASS_THRESHOLD` | `7.0` | float, 0-10 |
| `MAX_QA_ITERATIONS` | `2` | int, ≥ 1 |

`Config.validate()` returns a list of problem strings. `main.py` logs warnings for any problems at startup.
