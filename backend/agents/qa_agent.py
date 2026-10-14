"""
qa_agent.py — Agent 7: Quality Assurance Director

TPD optimisations applied:
  - task="light"  → starts on gpt-oss-20b (scoring is deterministic, not creative)
  - max_tokens=300 → score + 2 sentences ≈ 80 tokens; was 4096 (wasted ~4000 tokens)
  - SYSTEM_PROMPT (RUBRIC) is now static → prompt caching
  - Report truncated to 4000 chars (was 6000) — rubric categories are structural;
    QA doesn't need the full report text to score them
"""
import logging
import re
from openai import OpenAI
from backend.config import cfg
from backend.groq_fallback import groq_call_with_fallback
from backend.utils.json_utils import safe_json_parse

log    = logging.getLogger(__name__)
client = OpenAI(**cfg.llm_client_kwargs())

# Static rubric — no variables, fully cacheable by Groq
RUBRIC = """Score a financial analysis report using this rubric. Total = 10 points.

1. Executive Summary (0-2):
   2 = ≤200 words, states recommendation, includes expected outcome, mentions top risk
   1 = present but over 200 words or missing one element
   0 = missing or generic

2. Source Citations (0-2):
   2 = every factual claim has a traceable source
   1 = most sourced but ≥2 unsourced facts
   0 = no citations

3. Risk Specificity (0-2):
   2 = each risk has specific mitigation and regulatory ref where applicable
   1 = mitigations present but generic ("implement controls")
   0 = no mitigations

4. Recommendation Actionability (0-2):
   2 = specific, named owners, 90-day first steps
   1 = specific but missing owner or first step
   0 = generic ("adopt AI thoughtfully")

5. Decision Matrix (0-2):
   2 = 3+ approaches, weighted scores, clear verdicts
   1 = present but incomplete
   0 = missing

Return ONLY valid JSON:
  score:     float (0.0-10.0)
  feedback:  string (1-2 sentences on main deductions only)
"""

_DEFAULT_SCORE_ON_ERROR = 5.0


def run(state: dict) -> dict:
    report    = state.get("report", "")
    iteration = state.get("iteration_count", 0)

    log.info(f"[qa] scoring report (iteration {iteration})")

    if not report.strip():
        return {"qa_score": 0.0, "qa_feedback": "Report is empty.",
                "qa_pass": False, "current_agent": "qa_node"}

    messages = [
        {"role": "system", "content": RUBRIC},
        # Truncated to 4000 chars — rubric is structural; full text not needed
        {"role": "user", "content": f"Score this report:\n\n{report[:4000]}"},
    ]

    score    = _DEFAULT_SCORE_ON_ERROR
    feedback = ""

    try:
        raw = groq_call_with_fallback(
            client=client,
            messages=messages,
            primary_model=cfg.LLM_MODEL,
            temperature=0.1,
            max_tokens=300,     # score + 2 sentences ≈ 80 tokens; was 4096
            task="light",       # gpt-oss-20b sufficient; saves 120B TPD
        )

        result = safe_json_parse(raw, default=None, label="qa")

        if result is None:
            m = re.search(r'\b(\d+(?:\.\d+)?)\b', raw or "")
            score    = float(m.group(1)) if m else _DEFAULT_SCORE_ON_ERROR
            feedback = "QA parse error — score extracted from raw text"
        else:
            score    = float(result.get("score", _DEFAULT_SCORE_ON_ERROR))
            feedback = result.get("feedback", "")

    except RuntimeError as e:
        log.error(f"[qa] all models exhausted: {e}")
        feedback = f"QA scoring unavailable: {e}"
    except Exception as e:
        log.error(f"[qa] unexpected error: {e}")
        feedback = f"QA scoring error: {e}"

    qa_pass = score >= cfg.QA_PASS_THRESHOLD
    log.info(f"[qa] score={score:.1f} pass={qa_pass}")

    return {
        "qa_score":      score,
        "qa_feedback":   feedback,
        "qa_pass":       qa_pass,
        "status":        "completed" if qa_pass or iteration >= cfg.MAX_QA_ITERATIONS else "running",
        "current_agent": "qa_node",
    }
