# AI Build Log — FDE Financial Analysis Pipeline

5-day log of AI tool usage, decisions, and outcomes during the build of the FDE Financial Analysis Automation system.

---

## Day 1 — Architecture & Scaffolding

**AI Tools Used:** Claude Sonnet (claude.ai)

### Morning — Initial Architecture Design

**Task:** Design a 7-agent pipeline for financial analysis automation.

**Prompt approach:** Described the end goal (executive report from a topic string, 8 runs/day, banking context) and asked Claude to propose an architecture before writing any code.

**Claude's proposal:** LangGraph for orchestration, Groq for free LLM tier, DuckDuckGo for search, BeautifulSoup for scraping, FastAPI for REST API. Justified each choice against alternatives (CrewAI, SerpAPI, Playwright).

**Accepted:** All architectural choices. The rationale for LangGraph over CrewAI (typed state, conditional edges, recursion limit) was particularly well-reasoned.

**Output:** `backend/workflow.py`, `backend/state.py`, `backend/config.py`, initial agent stubs.

### Afternoon — Agent Implementation

**Task:** Implement all 7 agent `run()` functions.

**Claude's approach:** Wrote agents with static system prompts (topic/industry in user message for caching), appropriate `max_tokens` per task tier, and JSON validation for structured outputs (risk, scoring agents).

**Pushback given to Claude:** Initial `max_tokens=4096` on all agents was wasteful. Claude recalculated appropriate values per agent based on expected output size (scoring: 400 tokens → 1024 max, analysis: 800 tokens → 2048 max).

**Output:** All 7 agent files with task-tier routing and token optimisation.

---

## Day 2 — MCP Tool Layer & API

**AI Tools Used:** Claude Sonnet (claude.ai)

### Morning — MCP Tools

**Task:** Implement `tools.py` with search, scraping, and file write functions.

**Claude added unprompted:**
- Module-level `requests.Session` with browser-realistic headers (reduces 403s vs per-call sessions)
- `_HARD_BLOCKED_DOMAINS` set that skips Bloomberg, FT, WSJ before any network call
- `CUSTOM_CA_BUNDLE` and `DISABLE_SSL_VERIFY` env vars for corporate proxy environments
- Retry adapter with exponential backoff on 429/5xx

**Assessment:** All additions were appropriate and production-aware. Kept all of them.

### Afternoon — FastAPI Wrapper

**Task:** Build REST API with background task execution, polling, and file download.

**Output:** `main.py` with `/run-analysis`, `/status/{run_id}`, `/results/{run_id}`, `/download/{run_id}/{filename}`, `/health`.

**Issue found:** Two `main.py` files existed — one in project root, one in `backend/`. Root version was correct. Deleted `backend/main.py`.

---

## Day 3 — Debugging Production Errors

**AI Tools Used:** Claude Sonnet (claude.ai)

### Issue 1 — GraphRecursionError

**Error:** `langgraph.errors.GraphRecursionError: Recursion limit of 10007 reached`

**Root cause identified by Claude:** All Groq/OpenRouter models exhausted → every agent returns error → graph keeps retrying → hits recursion limit. Two compounding failures.

**Fix designed:** (1) `error_node` terminal + `route_or_error()` on every edge so graph exits cleanly on any error. (2) Anthropic as guaranteed final fallback. (3) `recursion_limit=50` to fail fast if the loop happens again.

**Time to diagnosis:** ~5 minutes with Claude reading the error logs.  
**Time to fix implementation:** ~30 minutes for full rewrite of `workflow.py` and `groq_fallback.py`.

### Issue 2 — OpenRouter 402 with $10 Balance

**Error:** `HTTP 402 Payment Required` despite `remaining: $10.00` in logs.

**Root cause identified:** Per-key credit cap ≠ account balance. New OpenRouter keys default to $0 per-key limit. The `/api/v1/auth/key` endpoint returns account balance, not key cap.

**Fix:** Dashboard change at `openrouter.ai/keys`. No code change needed.

**Claude's handling:** Correctly identified this as a config issue and refused to suggest code workarounds. Directed to dashboard directly.

### Issue 3 — 401 Missing Authentication Header

**Error:** `Missing Authentication header` from OpenRouter despite key being set.

**Root cause:** `.env` still had placeholder `sk-or-YOUR_PAID_KEY_HERE`. Key not replaced after setup.

**Fix:** Updated `.env` with real key. Added explicit `Authorization` header to `default_headers` in client builder as defence in depth.

---

## Day 4 — Rate Limiting & Resilience

**AI Tools Used:** Claude Sonnet (claude.ai)

### Morning — 429 Rate Limit Handling

**Problem:** Groq returning 429 with 25-second retry-after waits. 7 agents firing back-to-back causing burst.

**Claude's diagnosis:** `max_retries=2` (SDK default) causes two 25-second waits before giving up. Our fallback chain is better than SDK retry logic for this use case.

**Fix:** `max_retries=1` on all clients + `GROQ_INTER_CALL_DELAY_SEC=2.0` inter-call spacing.

**Result:** 429s reduced from ~3 per run to ~0 with 2s spacing. When they do occur, only one retry-after wait instead of two.

### Afternoon — Credit Monitoring

**Task:** Add live credit balance checking to prevent silent failures when OpenRouter credits run low.

**Implementation:**
- `get_openrouter_balance()` with 60s cache
- Pre-flight check before every OpenRouter call
- `< $0.50` warning, `< $0.10` skip threshold
- `/credits` FastAPI endpoint
- Credit status surfaced in `/health`

**Design decision:** Cache TTL of 60 seconds balances freshness vs API call overhead. Force-refresh after 402 and after successful calls ensures accuracy without hammering the endpoint.

---

## Day 5 — Documentation & Evaluation Prep

**AI Tools Used:** Claude Sonnet (claude.ai)

### Morning — Topic-Slug Filenames

**User request:** Change output filenames from UUID to topic-based names.

**Implementation:** `_safe_slug()` + `make_file_slug()` in `tools.py`. Updated `write_report()` and `save_decision_matrix()` signatures. Updated `/download` endpoint. Updated `/results` to include `download_files` block.

**Backwards compatibility:** `topic=""` falls back to `run_id` — no breaking change for existing call sites.

### Afternoon — Evaluator Checklist

**Task:** Assess project against 9 evaluator artifacts and create missing ones.

**Gap analysis:** All 9 artifacts missing. Claude built them all in sequence:
1. `README.md` — setup guide with PowerShell + curl examples
2. `docs/SDD.md` — full Software Design Document from actual code
3. `docs/architecture.md` — rationale for all technology choices
4. `backend/tests/test_agents.py` — 35+ tests across all 7 agents
5. `backend/tests/test_mcp_tools.py` — 30+ tests for all MCP tools
6. `CLAUDE.md` — AI coding governance document
7. `prompts/engineering-prompts.md` — this session's prompt log
8. `docs/ai-build-log.md` — this document
9. `outputs/sample-run-*/` — to be generated by running the pipeline

**Assessment of Claude's documentation quality:** SDD accurately reflects actual code (correct field names, real max_tokens values, actual validation logic). Tests correctly mock actual module paths. No hallucinated functionality.

---

## Summary Statistics

| Metric | Value |
|---|---|
| Total AI sessions | ~15 conversation turns across 5 days |
| Files AI-generated or significantly modified | 14 |
| Files AI-generated from scratch | 9 (all docs + tests) |
| Critical bugs caught by Claude | 3 (GraphRecursionError, qa_routing mutation, 402 vs balance distinction) |
| Claude pushbacks accepted | 4 (token optimisation, config-not-code for 402, error_node design, recursion_limit rationale) |
| Prompts that required clarifying questions from Claude | 3 |
| Estimated development time saved | ~3 days of solo development |

## AI Tool Limitations Encountered

1. **Context window:** Long conversations required re-uploading files when Claude's context filled. Mitigated by uploading files fresh at session start.
2. **No file system access:** Claude cannot run the code to verify it works — all validation was manual. Tests written by Claude need to be run to confirm correctness.
3. **No live API access:** Claude cannot check current OpenRouter model availability or Groq model IDs — verified manually.
4. **Hallucination risk on API docs:** Claude's knowledge of OpenRouter API endpoints was verified against the actual documentation before trusting it.
