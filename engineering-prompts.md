# Engineering Prompts Log — FDE Financial Analysis Pipeline

This log captures prompts used during AI-assisted development, Claude's responses, pushbacks, decisions made, and rationale. Maintained as a record of the AI-assisted build process.

---

## Session 1 — Initial Architecture & Error Diagnosis

### Prompt 1.1 — Error Diagnosis
**Prompt:**
> "What does this error mean in FDE project: `[analysis] all models exhausted` repeated 40+ times followed by `GraphRecursionError: Recursion limit of 10007 reached`"

**Claude's response summary:**
Identified two compounding problems: (1) Groq/OpenRouter quota exhausted causing all LLM calls to fail, (2) LangGraph graph stuck in infinite retry loop because no agent ever succeeds, eventually hitting recursion limit.

**Decision:** Fix both root cause (add Anthropic fallback so LLM calls never fully fail) and symptom (add `error_node` to LangGraph so failed agents exit cleanly).

**Rationale:** The recursion error was masking the real problem. Fixing only the recursion limit without fixing quota exhaustion would just make the failure faster, not prevent it.

---

### Prompt 1.2 — Requesting Full Code Fix
**Prompt:**
> "How can I rewrite the code to not exhaust the quota and run successfully? With all fixes above. I need to run 8 queries like this daily."

**Claude's response summary:**
Requested all relevant files before rewriting. Identified 4 fixes needed:
1. Add Anthropic as guaranteed final fallback in `groq_fallback.py`
2. Add `error_node` + `route_or_error()` in `workflow.py`
3. Set `recursion_limit=50` in `main.py`
4. Fix `qa_routing` state mutation bug

**Pushback from Claude:** Claude asked for `workflow.py` and `groq_fallback.py` before writing fixes — refused to rewrite blind without seeing the existing implementation.

**Decision:** Uploaded all files. Claude rewrote `groq_fallback.py`, `workflow.py`, `main.py`, `state.py`.

---

## Session 2 — Provider Configuration

### Prompt 2.1 — OpenRouter as Primary
**Prompt:**
> "I have paid OpenRouter quotas using key provided. I want to use it."

**Claude's response:**
Asked two clarifying questions: which model for heavy tasks, and whether to keep Groq as fallback. Chose `openai/gpt-4o` for heavy tasks, kept Groq as secondary.

**Decision:** Rewrote provider chain to: OpenRouter paid (primary) → Groq free (secondary) → Anthropic (backstop).

**Rationale:** Paid OpenRouter has no daily quota cap, making 8 daily runs reliable. Groq absorbs overflow if OpenRouter has transient issues.

---

### Prompt 2.2 — Credit Balance Checking
**Prompt:**
> "Can I add these steps in my code to check credits while code is running?"

**Claude's response:**
Added `get_openrouter_balance()` with 60-second caching, force-refresh after 402 errors and successful calls. Added `_openrouter_has_sufficient_credits()` that skips OpenRouter if balance < $0.10. Added `/credits` endpoint to FastAPI. Added warning logs at $0.50 threshold.

**Decision accepted:** Credit check integrated into `groq_fallback.py` as a pre-flight check before every OpenRouter call attempt.

---

## Session 3 — Debugging 402 Errors

### Prompt 3.1 — 402 with $10 Balance
**Prompt:**
> "Why is code showing insufficient credits? Balance shows $10.00 but still getting 402."

**Claude's analysis:**
Identified the key issue: OpenRouter has two separate limits — account balance ($10, fine) and per-key credit cap (defaulting to $0 on new keys). The `/api/v1/auth/key` endpoint returns account balance, not the key-specific cap.

**Pushback from Claude:** Claude was direct that no code change would fix this — it required a dashboard change at `openrouter.ai/keys`. Refused to suggest a code workaround for what is a configuration issue.

**Decision:** Fixed the key cap in OpenRouter dashboard. Added explicit `Authorization` header to `_get_openrouter_client()` as defence against auth header issues.

---

### Prompt 3.2 — 401 Missing Authentication
**Prompt:**
> "What does this error mean? `Missing Authentication header` — 401"

**Claude's analysis:**
`.env` still had placeholder value `sk-or-YOUR_PAID_KEY_HERE`. Identified with `Get-Content .env | Select-String "OPENROUTER"`.

**Decision:** Added explicit `"Authorization": f"Bearer {api_key}"` to `default_headers` in `_get_openrouter_client()` as defence in depth, even though OpenAI SDK normally handles this.

---

## Session 4 — Rate Limiting

### Prompt 4.1 — 429 Too Many Requests
**Prompt:**
> "How can I limit too many requests? Seeing 25 second retry waits."

**Claude's analysis:**
Identified that `max_retries=2` on the Groq client caused the SDK to respect Groq's `retry-after: 25s` header twice before giving up. 7 agents firing back-to-back with no spacing caused rapid successive calls.

**Two-part fix:**
1. `GROQ_INTER_CALL_DELAY_SEC=2.0` — spacing between Groq calls
2. `max_retries=1` on all clients — cap retry wait to one occurrence

**Decision accepted.** Added configurable via `.env` so the delay can be adjusted without code changes.

---

## Session 5 — File Naming

### Prompt 5.1 — Topic-Based Filenames
**Prompt:**
> "Can I change report name to match the topic rather than a cryptic UUID name?"

**Claude's response:**
Added `_safe_slug()` and `make_file_slug()` to `tools.py`. Updated `write_report()` and `save_decision_matrix()` signatures to accept optional `topic` parameter. Updated `report_writer_agent.py` to pass topic. Updated `/download` endpoint to use topic slug for filename validation. Updated `/results` to return `download_files` block with friendly names.

**Design decision:** `topic` parameter is optional with `default=""`, so existing call sites without topic still work (backwards compatible, falls back to run_id).

---

## Session 6 — Documentation & Artifacts

### Prompt 6.1 — Evaluator Checklist
**Prompt:**
> "Please see if my project satisfies all these requirements. Create additional artifacts if needed."

**Gap analysis by Claude:**
All 9 required artifacts missing — `SDD.md`, `architecture.md`, `test_agents.py`, `test_mcp_tools.py`, `README.md`, `engineering-prompts.md`, `CLAUDE.md`, `sample-run-*`, `ai-build-log.md`.

**Claude's approach:** Read all uploaded source files first before writing documentation, ensuring accuracy. Built `SDD.md` from actual agent code (correct `max_tokens`, actual field names, real validation logic). Built tests from actual agent behaviour (correct mock targets, real error types).

**Pushback:** Claude noted that `qa_routing` state mutation bug in `workflow.py` (incrementing `iteration_count` in router rather than agent) was a bug that needed fixing regardless of documentation — flagged in `qa_agent_patch.py`.

---

## Key Design Decisions Log

### Why `error_node` instead of just `recursion_limit`?

Setting `recursion_limit=50` alone would cause `GraphRecursionError` after 50 iterations — still an unhandled exception. The `error_node` pattern allows the graph to exit cleanly via `END` with `status=error` set, which the `/status` endpoint can report correctly.

### Why `ModelExhaustedError` subclass instead of bare `RuntimeError`?

Agents originally caught `RuntimeError` to handle quota exhaustion. But bare `RuntimeError` also catches legitimate programming errors. A specific `ModelExhaustedError` subclass lets agents distinguish "all providers exhausted, set error status" from "unexpected bug, let it propagate".

### Why `max_retries=1` on SDK clients?

The OpenAI SDK's built-in retry logic respects `retry-after` headers from providers. Groq sometimes returns `retry-after: 25s` — with `max_retries=2` (default), the SDK waits 25s twice before giving up. With `max_retries=1`, it waits once then raises, and the fallback chain tries the next model. Our chain provides better retry logic than the SDK's built-in.

### Why DuckDuckGo over SerpAPI?

Zero setup for evaluators. The requirement for "get free Groq key, one `.env` line, python test_setup.py" setup means no paid search API. DuckDuckGo via `ddgs` requires no API key and costs nothing. Quality is sufficient for financial research queries.

### Why topic slugs instead of UUIDs for filenames?

Evaluators and end users looking at the `outputs/` directory can immediately identify which report corresponds to which topic. `ai_agent_adoption_for_banking_report.md` is self-documenting. UUID filenames require cross-referencing with run logs.
