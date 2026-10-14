# Engineering Prompts Log

This document records the key prompting decisions made during development of the FDE Financial Analysis Pipeline — including the thought process, pushbacks encountered, and rationale for final choices.

---

## 1. Research Agent — Source Scoring Prompt

### Problem
DuckDuckGo returns raw results with no quality signal. The pipeline needs to keep authoritative sources and drop marketing content, without a second paid search API.

### Approach tried first
Asked Groq to filter results in free-form prose, then parsed the prose with regex.

**Pushback:** Regex parsing of prose is brittle. Groq would occasionally produce explanatory paragraphs before the list, breaking the regex.

### Final prompt design
Moved to strict JSON-only output with a schema contract:

```
Rules:
- Keep sources with relevance 6/10 or higher
- Flag regulatory sources (DORA, Basel, SR 11-7, FCA, OCC, GDPR, EU AI Act)
- Remove duplicates and marketing-only content
- Return ONLY valid JSON — no prose, no markdown fences

Each object must have exactly:
  title, url, summary, regulatory_flag (boolean), relevance_score (float 1-10)
```

**Rationale:** Enumerating the exact fields and types eliminates ambiguity. The "no markdown fences" instruction is necessary because Groq wraps JSON in ` ```json ``` ` blocks despite being told otherwise — a `_strip_fences()` helper handles the remaining cases defensively.

**Decision:** Added server-side regulatory keyword detection in `search_sources()` as a backstop so the pipeline has some signal even when Groq scoring fails and the fallback raw results are used.

---

## 2. Risk Agent — Preventing Generic Mitigations

### Problem
Early prompt versions produced risks with generic mitigations like "implement appropriate controls" or "conduct regular audits" — useless to a CRO reading the report.

### Approach tried first
```
mitigation — how to address the risk
```

**Pushback:** Generic mitigations every time. Evaluator feedback: "These could apply to any project."

### Iteration 1
Added a negative example:

```
mitigation — specific actionable control (NOT generic like "implement controls")
```

**Result:** Improved, but still occasionally generic for lower-severity risks.

### Final prompt design
Combined the negative example with an ownership requirement:

```
mitigation — specific actionable control (NOT generic like "implement controls")
owner      — who owns it (e.g. CTO, CRO, CISO)
regulatory_ref — specific regulation or "N/A" (e.g. "DORA Article 28", "SR 11-7")
```

**Rationale:** Forcing the model to name an owner and a regulatory reference makes vague mitigations structurally impossible — you can't write "implement controls" and also name DORA Article 28 and the CRO without the contradiction being obvious. The model self-corrects.

---

## 3. Scoring Agent — Weighted Total Recalculation

### Problem
Trusting the LLM to calculate `weighted_total` correctly is unreliable. Groq occasionally produced totals that didn't match the weights, or left the field at 0.

### Approach tried first
Included the weight table in the prompt and asked Groq to calculate totals.

**Pushback:** Arithmetic errors on ~20% of runs, inconsistent rounding.

### Decision
Set `weighted_total: 0` as a sentinel in the prompt ("set to 0, server recalculates this") and recalculate server-side with a fixed weight dict:

```python
_WEIGHTS = {
    "regulatory_compliance": 0.25,
    "security_privacy":      0.20,
    "scalability":           0.15,
    "tco":                   0.15,
    "implementation_risk":   0.15,
    "vendor_maturity":       0.10,
}
```

**Rationale:** LLMs are not reliable calculators. Arithmetic that affects a business decision (which approach to recommend) must be deterministic and server-controlled. The LLM's job is to produce the 0-10 scores; the server's job is to weight them.

---

## 4. QA Agent — Rubric Design

### Problem
A simple "score this report 1-10" prompt produces inconsistent scores with no actionable feedback. Scores of 7 or 8 were given to reports missing entire sections.

### Approach tried first
```
Score the report quality from 0-10. Return JSON with score and feedback.
```

**Pushback:** Scores were inconsistently high. A report with no decision matrix got an 8.

### Final prompt design
A 5-category rubric with explicit scoring criteria for each band:

```
1. Executive Summary (0-2):
   2 = ≤200 words, states recommendation, includes expected outcome, mentions top risk
   1 = present but over 200 words or missing one element
   0 = missing or generic

2. Source Citations (0-2): ...
3. Risk Specificity (0-2): ...
4. Recommendation Actionability (0-2): ...
5. Decision Matrix (0-2): ...
```

**Rationale:** Structured rubrics force the model to evaluate each dimension independently before summing. This prevents halo effects (a well-written executive summary inflating the overall score) and produces feedback that is specific enough for the re-analysis loop to act on.

**Temperature:** Set to `0.1` for the QA agent (vs `0.3` for all others). QA is a scoring task, not a creative one — lower temperature produces more consistent scores across runs.

---

## 5. Report Writer — Section Heading Enforcement

### Problem
Without explicit heading constraints, the report writer produced inconsistent structures — sometimes merging Risk Register and Recommendations, sometimes omitting the Decision Matrix table entirely.

### Final prompt design
Specified exact heading strings in the system prompt:

```
Use these exact Markdown headings:
# {topic} — Executive Analysis Report
## Executive Summary
## Key Findings
## Risk Register
## Decision Matrix
## Recommendations
## Sources
```

And pre-formatted the Risk Register and Decision Matrix as markdown tables in the user message before the LLM sees them, rather than asking the LLM to format raw JSON as tables.

**Rationale:** The LLM's job is to write prose around pre-built structural elements, not to invent the structure. Pre-formatting the tables server-side (`_risk_table()` and `_matrix_table()` in `report_writer_agent.py`) guarantees correct column alignment regardless of what the LLM does with the surrounding text.

---

## 6. JSON Fence Stripping — Cross-Agent Pattern

### Problem
Groq wraps JSON responses in ` ```json ``` ` fences on approximately 30-40% of calls despite explicit "no markdown fences" instructions. This breaks `json.loads()`.

### Solution
A shared `_strip_fences()` helper implemented identically in `research_agent.py`, `risk_agent.py`, `scoring_agent.py`, and `qa_agent.py`:

```python
def _strip_fences(raw: str) -> str:
    if raw.startswith("```"):
        parts = raw.split("```")
        raw = parts[1]
        if raw.startswith("json"):
            raw = raw[4:]
    return raw.strip()
```

**Pushback considered:** Should this be a shared utility in `backend/utils.py`? Decided against it to keep each agent self-contained and independently testable without importing a utility module. The duplication is intentional.

**Rationale:** The pattern is simple enough (6 lines) that duplication is preferable to introducing a shared dependency that could be accidentally modified and break multiple agents at once.

---

## 7. Extraction Agent — No LLM Required

### Decision
The extraction agent deliberately has no LLM call. It is pure BeautifulSoup scraping.

**Rationale:** Asking an LLM to "extract content" from raw HTML is unnecessary when BeautifulSoup already does it deterministically and for free. LLM calls are expensive (latency + tokens) — they should be reserved for tasks that require reasoning, not tasks that are solved by parsing. The extraction agent strips noise tags, joins lines, and caps length; the analysis agent then reasons over the result.

This was a deliberate pushback against an early design that had the extraction agent summarise each source with Groq before handing off to the analysis agent. Removing that call reduced pipeline latency by ~30% and removed a failure point.
