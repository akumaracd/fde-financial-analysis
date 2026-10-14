# Software Design Document — FDE Financial Analysis Automation

**Version:** 2.0  
**Date:** September 2026  
**Status:** Production

---

## 1. Overview

The FDE Financial Analysis Automation system is a 7-agent LangGraph pipeline that:

1. Researches a financial topic using DuckDuckGo
2. Scrapes and extracts content from discovered URLs
3. Synthesises a structured analysis
4. Identifies material risks with regulatory references
5. Scores investment approaches against a weighted decision matrix
6. Writes a client-ready executive report in Markdown
7. QA-reviews the report and optionally loops back for improvement

All agents share a single `AnalysisState` TypedDict that flows through the LangGraph graph. External I/O (search, scraping, file writes) is isolated in `backend/mcp_server/tools.py`. LLM calls go through `backend/groq_fallback.py` which implements a three-provider fallback chain.

---

## 2. System Architecture

### 2.1 Layer Diagram

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
│ OR → Groq →      │  │  search_sources()     │
│ Anthropic        │  │  scrape_content()     │
└──────────────────┘  │  write_report()       │
                      │  save_decision_matrix()│
                      └───────────────────────┘
```

### 2.2 Technology Decisions

| Decision | Choice | Rationale | Alternatives Considered |
|---|---|---|---|
| LLM orchestration | LangGraph | Stateful graph with typed state, conditional edges, built-in loop control | CrewAI (less control over state flow), AutoGen (too heavyweight), raw chains (no retry routing) |
| Primary LLM provider | OpenRouter (paid) | No daily quota cap, gpt-4o quality, single key for multiple models | OpenAI direct (no fallback), Anthropic direct (no gpt-4o) |
| Secondary LLM | Groq (free) | High speed, free tier, same OpenAI-compatible API | Together AI (requires card), HuggingFace Inference (slower) |
| Final LLM fallback | Anthropic | Reliable, no daily quota, own SDK | Azure OpenAI (requires Azure account) |
| Web search | DuckDuckGo (DDGS) | No API key, no cost, sufficient quality for financial research | SerpAPI ($50/mo), Bing Search API (requires Azure), Brave Search (API key required) |
| Web scraping | BeautifulSoup + requests | Lightweight, reliable, no API key, works with corporate proxy via env vars | Playwright (overkill, requires browser install), Scrapy (too heavy) |
| API framework | FastAPI | Async-ready, auto-docs, BackgroundTasks, Pydantic validation | Flask (no async), Django (too heavy) |
| State management | TypedDict | Type-safe, LangGraph-native, no ORM overhead | Pydantic model (serialisation overhead), dataclass (less LangGraph-friendly) |

---

## 3. Agent Specifications

### Agent 1 — Research Analyst (`research_agent.py`)

**Role:** Discover relevant, high-quality sources for the given topic.

**Input state keys:** `topic`, `industry`  
**Output state keys:** `sources` (list of dicts), `current_agent`

**LLM task tier:** `light` (gpt-4o-mini / gpt-oss-20b)  
**max_tokens:** 1024

**Behaviour:**
- Runs 2 DuckDuckGo searches: one for regulatory/compliance angle, one for implementation/best practices angle
- Scores each result: `regulatory_flag=True` sources score 8.5, others 7.5
- Deduplicates by URL
- Returns up to 10 sources sorted by relevance score

**Source schema:**
```python
{
    "title":           str,   # page title
    "url":             str,   # canonical URL
    "summary":         str,   # first 300 chars of DDGS snippet
    "regulatory_flag": bool,  # True if regulatory keyword found
    "relevance_score": float  # 8.5 regulatory, 7.5 general
}
```

**Error handling:** Returns `sources=[]` on SearchError; pipeline continues (extraction agent handles empty sources gracefully).

---

### Agent 2 — Extraction Specialist (`extraction_agent.py`)

**Role:** Scrape and clean web content from discovered source URLs.

**Input state keys:** `sources`  
**Output state keys:** `extracted_content` (str), `extraction_metadata` (list), `current_agent`

**LLM task tier:** None — no LLM used, zero quota cost  
**max_tokens:** N/A

**Behaviour:**
- Scrapes all source URLs in parallel (ThreadPoolExecutor)
- Strips nav/footer/script/style tags via BeautifulSoup
- Prefers `<article>` or `<main>` over full `<body>`
- Truncates each page to `SCRAPE_MAX_CHARS` (default 8000)
- Concatenates all successful pages into `extracted_content`
- Skips hard-blocked domains (Bloomberg, FT, WSJ, etc.) without wasting retry budget

**Extraction metadata schema:**
```python
{
    "url":        str,
    "char_count": int,
    "status":     "success" | "failed",
    "error":      str | None
}
```

---

### Agent 3 — Enterprise Architect (`analysis_agent.py`)

**Role:** Synthesise extracted content into a structured financial analysis.

**Input state keys:** `topic`, `industry`, `extracted_content`, `qa_feedback`, `iteration_count`  
**Output state keys:** `analysis` (str), `current_agent`

**LLM task tier:** `heavy` (gpt-4o / gpt-oss-120b)  
**max_tokens:** 2048

**System prompt:** Static (cached) — topic/industry injected in user message.

**Output structure (enforced by prompt):**
```markdown
## Key Trends
## Architectural Patterns
## Regulatory Implications
## Strategic Implications
```

**QA loop behaviour:** On re-entry (iteration > 0), `qa_feedback` is appended to the user message so the analyst can self-correct specific gaps identified by Agent 7.

**Content truncation:** `extracted_content[:8000]` — quality does not meaningfully improve beyond ~2000 input tokens; truncation saves ~1000 tokens per call.

---

### Agent 4 — Risk Specialist (`risk_agent.py`)

**Role:** Identify 6–10 material risks with regulatory references and mitigations.

**Input state keys:** `topic`, `industry`, `analysis`, `sources`  
**Output state keys:** `risks` (list of dicts), `current_agent`

**LLM task tier:** `heavy`  
**max_tokens:** 2048

**Output schema (JSON array, validated server-side):**
```python
{
    "name":           str,   # 5-8 word risk name
    "category":       str,   # Regulatory | Operational | Technology | Strategic
    "level":          str,   # Critical | High | Medium | Low
    "impact":         str,   # 1-2 sentences
    "mitigation":     str,   # specific actionable control
    "owner":          str,   # role (e.g. CTO, CRO)
    "regulatory_ref": str    # specific regulation or "N/A"
}
```

**Validation:** Invalid `level` values default to `"Medium"`. Invalid `category` values default to `"Operational"`. Model wrapping array in dict (`{"risks": [...]}`) is unwrapped automatically.

---

### Agent 5 — Investment Strategist (`scoring_agent.py`)

**Role:** Score 3–4 investment/architecture approaches against a weighted decision matrix.

**Input state keys:** `topic`, `industry`, `analysis`, `risks`  
**Output state keys:** `decision_matrix` (list of dicts), `current_agent`

**LLM task tier:** `medium` (gpt-4o-mini / gpt-oss-20b)  
**max_tokens:** 1024

**Scoring weights (server-side recalculation overrides model's weighted_total):**

| Criterion | Weight |
|---|---|
| regulatory_compliance | 25% |
| security_privacy | 20% |
| scalability | 15% |
| tco | 15% |
| implementation_risk | 15% |
| vendor_maturity | 10% |

**Rule:** Any approach scoring < 5 on `regulatory_compliance` must be `"Not Recommended"`.

**Output schema:**
```python
{
    "approach":               str,    # approach name
    "regulatory_compliance":  int,    # 0-10
    "security_privacy":       int,
    "scalability":            int,
    "tco":                    int,    # 10 = lowest cost
    "implementation_risk":    int,    # 10 = lowest risk
    "vendor_maturity":        int,
    "weighted_total":         float,  # server-recalculated
    "verdict":                str     # Recommended | Conditional | Not Recommended
}
```

Matrix is sorted by `weighted_total` descending before returning.

---

### Agent 6 — Report Writer (`report_writer_agent.py`)

**Role:** Produce a client-ready executive report in Markdown.

**Input state keys:** `topic`, `industry`, `analysis`, `risks`, `decision_matrix`, `sources`, `run_id`  
**Output state keys:** `report` (str), `report_path` (str), `matrix_path` (str), `current_agent`

**LLM task tier:** `heavy`  
**max_tokens:** 4096

**Output structure (enforced by prompt):**
```markdown
## Executive Summary
## Key Findings
## Risk Register
## Decision Matrix
## Recommendations
## Sources
```

**File naming:** `make_file_slug(topic, run_id)` converts topic to a filename slug. Example: `"AI Agent Adoption for Banking"` → `ai_agent_adoption_for_banking_report.md`.

**Source cap:** Top 15 sources only (LLM ignores beyond ~10; saves input tokens on large source lists).

**Pre-formatted tables:** Risk register and decision matrix are pre-formatted as Markdown tables by the agent before sending to the LLM, ensuring consistent output regardless of model.

---

### Agent 7 — QA Director (`qa_agent.py`)

**Role:** Score the report against a 5-category rubric and provide actionable feedback.

**Input state keys:** `topic`, `report`, `analysis`, `risks`, `decision_matrix`  
**Output state keys:** `qa_score` (float), `qa_feedback` (str), `qa_pass` (bool), `iteration_count` (int, incremented), `current_agent`

**LLM task tier:** `light`  
**max_tokens:** 300

**Scoring rubric (2 points each, max 10):**

| Category | Criteria |
|---|---|
| Source grounding | Every claim references a source |
| Risk completeness | All Critical/High risks have mitigations |
| Regulatory coverage | At least one specific regulation cited |
| Recommendation quality | Each recommendation has owner + 90-day step |
| Executive clarity | Summary ≤ 200 words, no jargon |

**Routing:** Score ≥ `QA_PASS_THRESHOLD` (default 7.0) → END. Score < threshold and iterations < `MAX_QA_ITERATIONS` (default 1) → loop back to Agent 3 with feedback. `iteration_count` is incremented in the agent's return dict (not in the routing function).

---

## 4. MCP Tool Layer Contract

All external I/O is in `backend/mcp_server/tools.py`. Agents must not make network or filesystem calls directly.

### 4.1 `search_sources(query, industry, max_results) → list[dict]`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `query` | str | required | Search query |
| `industry` | str | `""` | Appended to query if not already present |
| `max_results` | int | 10 | Max results from DDGS |

Raises `SearchError` on blank query, DDGS failure, or zero results.

### 4.2 `scrape_content(url, max_chars) → dict`

| Parameter | Type | Default | Description |
|---|---|---|---|
| `url` | str | required | Must start with `http` |
| `max_chars` | int | `cfg.SCRAPE_MAX_CHARS` | Character cap |

Returns: `{"url": str, "content": str, "char_count": int, "status": "success"}`  
Raises `ScrapeError` on blocked domain, timeout (15s), HTTP error, non-HTML, or content < 50 chars.

Hard-blocked domains (skipped before any network call): `researchgate.net`, `bloomberg.com`, `ft.com`, `wsj.com`, `spglobal.com`, `economist.com`, `hbr.org`.

SSL configuration via env vars: `CUSTOM_CA_BUNDLE=/path/to/ca.crt` or `DISABLE_SSL_VERIFY=true`.

### 4.3 `write_report(content, run_id, topic) → str`

Writes `{topic-slug}_report.md` to `OUTPUT_DIR`. Returns absolute path. Raises `FileWriteError` on empty content.

### 4.4 `save_decision_matrix(matrix, run_id, topic) → str`

Writes `{topic-slug}_matrix.csv` to `OUTPUT_DIR`. Validates consistent row keys before writing. Returns absolute path. Raises `FileWriteError` on empty matrix or key mismatch.

### 4.5 `make_file_slug(topic, run_id) → str`

Converts topic to a safe filename slug. Falls back to `run_id` if topic is empty.

```python
"AI Agent Adoption for Banking" → "ai_agent_adoption_for_banking"
"GLP-1 Weight Loss Market 2026" → "glp_1_weight_loss_market_2026"
""                              → "<run_id_uuid>"
```

---

## 5. LLM Fallback Chain

Implemented in `backend/groq_fallback.py`.

### 5.1 Provider Chain

```
① OpenRouter PAID (primary)
   openai/gpt-4o           ← heavy tasks
   openai/gpt-4o-mini      ← medium/light tasks
       ↓ all OR exhausted / 402 / credits < $0.10

② Groq FREE (secondary)
   openai/gpt-oss-120b
   openai/gpt-oss-20b
   openai/gpt-oss-safeguard-20b
   qwen/qwen3.8-27b
       ↓ all Groq exhausted

③ Anthropic PAID (final backstop)
   claude-sonnet-4-6
       ↓ all fail

ModelExhaustedError → agents set status=error → error_node → END
```

### 5.2 Task Routing

| Task tier | Starting model | Used by |
|---|---|---|
| `heavy` | gpt-4o (OR) | analysis, risk, report_writer |
| `medium` | gpt-4o-mini (OR) | scoring |
| `light` | gpt-4o-mini (OR) | research, qa |

### 5.3 Rate Limiting

- `GROQ_INTER_CALL_DELAY_SEC=2.0` — minimum gap between successive Groq calls
- `GROQ_429_EXTRA_WAIT_SEC=5.0` — additional wait (+ random jitter 1–3s) after 429
- `max_retries=1` on all clients — prevents SDK from compounding retry-after waits
- `timeout=30s` OpenRouter, `timeout=60s` Groq

### 5.4 Credit Monitoring

- Balance fetched from `GET /api/v1/auth/key` and cached for `OR_CREDIT_CACHE_TTL_SEC=60`
- Force-refreshed after every successful OR call and after any 402
- `< $0.50` → WARNING logged, calls continue
- `< $0.10` → all OR models skipped, Groq used immediately
- **Important:** Per-key credit limit ≠ account balance. Set key cap at `openrouter.ai/keys`.

---

## 6. API Specification

Base URL: `http://localhost:8000`

### POST /run-analysis

**Request:**
```json
{
  "topic": "AI Agent Adoption for Banking",
  "industry": "Banking / Financial Services"
}
```

**Response 202:**
```json
{
  "run_id": "dad2fe02-59f9-4682-bf54-e9c70cb6e322",
  "status": "started",
  "poll_url": "/status/dad2fe02-...",
  "result_url": "/results/dad2fe02-..."
}
```

### GET /status/{run_id}

**Response 200:**
```json
{
  "run_id": "dad2fe02-...",
  "status": "running",
  "current_agent": "analysis_node",
  "iteration_count": 0,
  "progress_pct": 45,
  "provider_used": "groq/openai/gpt-oss-120b",
  "error_count": 1,
  "error": null
}
```

Status values: `initialized` → `running` → `completed` | `error`

### GET /results/{run_id}

**Response 200 (completed):** Full `AnalysisState` dict plus:
```json
{
  "download_files": {
    "report": "ai_agent_adoption_for_banking_report.md",
    "matrix": "ai_agent_adoption_for_banking_matrix.csv"
  }
}
```

**Response 409:** `{"error": "still running — poll /status first"}`

### GET /download/{run_id}/{filename}

Downloads the report `.md` or matrix `.csv` file. Filename must match topic slug for the run.

### GET /credits

```json
{
  "remaining_usd": 9.82,
  "used_usd": 0.18,
  "limit_usd": 10.0,
  "status": "ok",
  "top_up_url": "https://openrouter.ai/credits",
  "error": null
}
```

Status: `ok` | `low` (< $0.50) | `critical` (< $0.10) | `unknown` | `error`

### GET /health

```json
{
  "status": "ok",
  "problems": [],
  "providers": {
    "openrouter": true,
    "groq": true,
    "anthropic": true
  },
  "openrouter_credits": {
    "remaining_usd": 9.82,
    "status": "ok"
  },
  "llm": "OpenRouter gpt-4o → Groq → Anthropic claude-sonnet-4-6"
}
```

---

## 7. State Schema

```python
class AnalysisState(TypedDict):
    topic:               str
    industry:            str
    run_id:              str
    sources:             list[dict]
    extracted_content:   str
    extraction_metadata: list[dict]
    analysis:            str
    risks:               list[dict]
    decision_matrix:     list[dict]
    report:              str
    report_path:         str
    matrix_path:         str
    qa_score:            float
    qa_feedback:         str
    qa_pass:             bool
    status:              str          # initialized | running | completed | error
    current_agent:       str          # node name
    iteration_count:     int          # QA loop counter
    error:               str | None
    provider_used:       str          # last successful LLM provider
    error_count:         int          # non-fatal fallback count
```

---

## 8. Error Handling

### 8.1 Agent-level errors

Every agent wraps its LLM call in try/except:

```python
except ModelExhaustedError as e:
    return {"status": "error", "error": str(e), "current_agent": "..."}
except Exception as e:
    return {"status": "error", "error": f"Agent error: {e}", "current_agent": "..."}
```

### 8.2 Graph-level routing

Every edge uses `route_or_error(next_node)` which checks `state.get("status") == "error"` and routes to `error_node` instead of continuing. `error_node` is a no-op terminal that routes to `END`.

### 8.3 Recursion safety

`ANALYSIS_GRAPH.invoke(state, config={"recursion_limit": 50})` — prevents runaway graphs. Normal runs use 7–14 node invocations; 50 gives headroom while catching bugs fast.

---

## 9. Configuration Reference

All configuration in `backend/config.py`, driven by `.env`:

| Variable | Default | Description |
|---|---|---|
| `OPENROUTER_API_KEY` | required | Paid OpenRouter key |
| `GROQ_API_KEY` | recommended | Free Groq key |
| `ANTHROPIC_API_KEY` | recommended | Anthropic backstop key |
| `LLM_MODEL` | `openai/gpt-4o` | Primary model |
| `LLM_TEMPERATURE` | `0.3` | Sampling temperature |
| `OUTPUT_DIR` | `./outputs` | Report output directory |
| `LOG_LEVEL` | `INFO` | Python logging level |
| `SCRAPE_MAX_CHARS` | `8000` | Per-page scrape cap |
| `QA_PASS_THRESHOLD` | `7.0` | Min QA score to accept |
| `MAX_QA_ITERATIONS` | `1` | Max QA retry loops |
| `GROQ_INTER_CALL_DELAY_SEC` | `2.0` | Groq rate limit spacing |
| `GROQ_429_EXTRA_WAIT_SEC` | `5.0` | Extra wait after 429 |
| `OR_LOW_CREDIT_WARN_USD` | `0.50` | Warn threshold |
| `OR_SKIP_CREDIT_USD` | `0.10` | Skip OR threshold |
| `OR_CREDIT_CACHE_TTL_SEC` | `60` | Balance cache TTL |
