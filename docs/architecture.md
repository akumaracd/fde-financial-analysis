# Architecture — FDE Financial Analysis Pipeline

## Diagram

See `architecture_1.mermaid` (renderable at mermaid.live) and `architecture_1.png` for the full visual.

```
Client
  │
  ▼
FastAPI (main.py)
  │  BackgroundTask — returns run_id immediately (202)
  ▼
AnalysisState (state.py)
  │
  ▼
LangGraph Pipeline (workflow.py) ─── recursion_limit=50
  │
  ├─ research_node    → Agent 1: Research Analyst
  │    └─ search_sources() → DuckDuckGo × 2 queries
  │
  ├─ extractor_node   → Agent 2: Extraction Specialist  [no LLM]
  │    └─ scrape_content() → BeautifulSoup per URL
  │
  ├─ analysis_node    → Agent 3: Enterprise Architect   [heavy]
  │
  ├─ risk_node        → Agent 4: Risk Specialist        [heavy]
  │
  ├─ scoring_node     → Agent 5: Investment Strategist  [medium]
  │
  ├─ report_node      → Agent 6: Report Writer          [heavy]
  │    └─ write_report() + save_decision_matrix()
  │
  ├─ qa_node          → Agent 7: QA Director            [light]
  │    └─ score ≥ 7.0 → END
  │       score < 7.0 → loop back to analysis_node (max 1 iteration)
  │       status=error → error_node → END
  │
  └─ error_node       → Terminal sink (any agent failure)

Every agent LLM call → groq_fallback.py
  ① OpenRouter paid  (gpt-4o / gpt-4o-mini)
  ② Groq free        (gpt-oss-120b chain)
  ③ Anthropic paid   (claude-sonnet-4-6)
```

---

## Key Design Decisions

### LangGraph vs CrewAI

**Chosen: LangGraph**

LangGraph was chosen over CrewAI for three reasons:

1. **Typed state.** `AnalysisState` is a `TypedDict` that flows through every node. Every agent reads from and writes to the same dict — no message-passing ambiguity, no hidden state.

2. **Conditional edge control.** The QA loop (`qa_routing`), the error routing (`route_or_error`), and the `error_node` terminal are all first-class graph constructs. In CrewAI, implementing a conditional retry loop with a hard iteration cap requires workarounds.

3. **Recursion limit.** LangGraph exposes `recursion_limit` as a config parameter. Setting it to 50 provides a hard safety valve against runaway graphs — the bug that caused the original `GraphRecursionError` at 10,007 iterations.

CrewAI was considered but rejected because its role-based abstraction hides the exact flow of data between agents, making debugging production failures harder.

### OpenRouter (paid) vs OpenAI direct

**Chosen: OpenRouter as primary**

OpenRouter provides:
- Access to multiple model providers (OpenAI, Meta, Mistral, etc.) through a single API key
- Automatic failover across providers on their side
- Unified billing — one credit balance instead of multiple accounts
- Paid tier has no daily quota cap, making 8 daily runs reliable

OpenAI direct was rejected because it locks the system to one provider. If OpenAI has an outage, the entire pipeline fails. OpenRouter adds a layer of resilience.

### Groq (free) vs other free tiers

**Chosen: Groq as secondary fallback**

Groq's free tier offers:
- High inference speed (500–1000 tokens/second vs 50–100 on most alternatives)
- OpenAI-compatible API (same client, just different base URL)
- Multiple model options for progressive fallback

Together AI and Hugging Face Inference API were considered but rejected — Together AI requires a credit card for higher limits, and Hugging Face Inference is significantly slower for the output sizes this pipeline requires.

### Anthropic as final backstop

**Chosen: Anthropic claude-sonnet-4-6**

Anthropic was chosen as the final fallback because:
- No daily quota cap (metered only by spend)
- Reliable API uptime
- Claude Sonnet produces high-quality structured output comparable to gpt-4o

The Anthropic SDK uses a different API shape (not OpenAI-compatible), so it's called via a separate `_call_anthropic()` function in `groq_fallback.py`. It is **never** marked as exhausted in `_exhausted_today` — it is always available as long as the API key is set and the account has credits.

### DuckDuckGo vs SerpAPI

**Chosen: DuckDuckGo (DDGS)**

DuckDuckGo via the `ddgs` Python library:
- **No API key required** — zero setup for evaluators
- **No cost** — consistent with the free-tier-first philosophy
- Sufficient result quality for financial research queries
- Returns title, URL, and body snippet — enough for source scoring

SerpAPI ($50/month) and Bing Search API (requires Azure account) were rejected because they add cost and setup friction. The evaluator requirement for "one `.env` line" setup is met by DuckDuckGo.

### BeautifulSoup vs Playwright

**Chosen: BeautifulSoup + requests**

BeautifulSoup:
- Lightweight — no browser binary required
- Works in corporate proxy environments via `CUSTOM_CA_BUNDLE` env var
- Sufficient for text extraction from financial/consulting sites
- Module-level session with browser-realistic headers reduces 403s

Playwright was rejected because it requires a browser binary installation, complicates deployment, and adds significant startup latency. The hard-blocked domain list (`_HARD_BLOCKED_DOMAINS`) handles sites that genuinely require JavaScript rendering by skipping them rather than attempting and failing.

---

## Pipeline Data Flow

```
topic + industry
       │
       ▼
research_agent ──── DuckDuckGo × 2 ──── sources (list[dict])
       │
       ▼
extraction_agent ── scrape_content() ── extracted_content (str, ~80k chars max)
       │
       ▼
analysis_agent ──── LLM heavy ──────── analysis (str, ~600 words)
       │
       ▼
risk_agent ─────── LLM heavy ──────── risks (list[dict], 6-10 items)
       │
       ▼
scoring_agent ───── LLM medium ─────── decision_matrix (list[dict], 3-4 rows)
                    + server-side weighted_total recalculation
       │
       ▼
report_writer ───── LLM heavy ──────── report (str, Markdown)
                    write_report() → topic-slug_report.md
                    save_decision_matrix() → topic-slug_matrix.csv
       │
       ▼
qa_agent ────────── LLM light ──────── qa_score (float, 0-10)
                                        qa_feedback (str)
       │
       ├── score ≥ 7.0 ──────────────── END (completed)
       ├── score < 7.0, iter < max ───── loop → analysis_agent
       └── status=error ─────────────── error_node → END (error)
```

---

## Fault Tolerance

| Failure | Detection | Recovery |
|---|---|---|
| OpenRouter 402 (key cap) | HTTP 402 caught in fallback | Skip all OR models, use Groq |
| OpenRouter credits low | Balance check before each call | Warn at $0.50, skip at $0.10 |
| Groq 429 rate limit | HTTP 429, `retry-after` header | 2s inter-call delay + 5s extra wait, try next Groq model |
| Groq daily quota (TPD) | `_mark_exhausted()` on rate limit | Move to next Groq model, then Anthropic |
| All providers exhausted | `ModelExhaustedError` raised | Agent returns `status=error`, graph exits via `error_node` |
| Graph recursion runaway | `recursion_limit=50` | `GraphRecursionError` caught in `_run_pipeline`, logged as error |
| Scrape timeout | 15s request timeout | `ScrapeError` raised, URL skipped, pipeline continues |
| JSON parse failure | `safe_json_parse()` with default | Returns `[]` or `{}`, agent handles gracefully |
| QA score too low | `qa_score < QA_PASS_THRESHOLD` | Loop back to analysis_agent with feedback (max 1 iteration) |

---

## Performance Characteristics

**Typical run duration:** 3–6 minutes (including 2s Groq inter-call delays and scraping)

**Token usage per run (approximate):**

| Agent | Input tokens | Output tokens | Model |
|---|---|---|---|
| Research | ~500 | ~800 | gpt-4o-mini |
| Extraction | 0 | 0 | none |
| Analysis | ~2500 | ~800 | gpt-4o |
| Risk | ~1500 | ~1200 | gpt-4o |
| Scoring | ~1200 | ~400 | gpt-4o-mini |
| Report | ~3000 | ~2000 | gpt-4o |
| QA | ~2000 | ~300 | gpt-4o-mini |
| **Total** | **~10,700** | **~5,500** | — |

**Estimated cost per run:** ~$0.20–0.35 on OpenRouter paid tier  
**Estimated cost for 8 runs/day:** ~$1.60–2.80/day
