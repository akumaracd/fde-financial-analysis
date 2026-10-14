# FDE Financial Analysis Automation

A 7-agent LangGraph pipeline that researches a financial topic, extracts web content, produces a structured analysis, risk register, decision matrix, and executive report — fully automated, end-to-end.

## Architecture

```
Client → FastAPI → LangGraph Pipeline → Multi-Provider LLM Chain → Outputs
                   Research → Extract → Analyse → Risk → Score → Report → QA
```

Primary LLM: OpenRouter (paid) → Groq (free fallback) → Anthropic (final backstop)  
Search: DuckDuckGo (no API key required)  
Scraping: BeautifulSoup (no API key required)

## Prerequisites

- Python 3.11+
- A free Groq API key → [console.groq.com](https://console.groq.com)
- A paid OpenRouter API key → [openrouter.ai/keys](https://openrouter.ai/keys) (set per-key credit limit to match account balance)
- Optional: Anthropic API key → [console.anthropic.com](https://console.anthropic.com) (final fallback)

## Quick Start

**Step 1 — Clone and install**
```bash
git clone <repo-url>
cd FDE-financial-analysis
python -m venv venv
# Windows:
venv\Scripts\activate
# Mac/Linux:
source venv/bin/activate
pip install -r requirements.txt
```

**Step 2 — Configure `.env`**

Copy the template and fill in your keys:
```bash
cp .env.template .env
```

Minimum required — one line:
```
OPENROUTER_API_KEY=sk-or-v1-your-paid-key-here
```

Full recommended `.env`:
```env
OPENROUTER_API_KEY=sk-or-v1-...
GROQ_API_KEY=gsk_...
ANTHROPIC_API_KEY=sk-ant-...
LLM_MODEL=openai/gpt-4o
LLM_TEMPERATURE=0.3
OUTPUT_DIR=./outputs
LOG_LEVEL=INFO
MAX_QA_ITERATIONS=1
QA_PASS_THRESHOLD=7.0
GROQ_INTER_CALL_DELAY_SEC=2.0
GROQ_429_EXTRA_WAIT_SEC=5.0
OR_LOW_CREDIT_WARN_USD=0.50
OR_SKIP_CREDIT_USD=0.10
```

**Step 3 — Verify setup**
```bash
python test_setup.py
```

Expected output:
```
✓ OPENROUTER_API_KEY set
✓ GROQ_API_KEY set
✓ OpenRouter balance: $10.00 remaining
✓ Groq connection OK
✓ DuckDuckGo search OK
✓ Output directory: ./outputs
All checks passed — ready to run.
```

**Step 4 — Start the server**
```bash
# Windows PowerShell:
uvicorn main:app --port 8000 --reload

# With log file:
uvicorn main:app --port 8000 --log-config log_config.json
```

**Step 5 — Run an analysis**
```powershell
# PowerShell
$r = (Invoke-WebRequest -Uri http://localhost:8000/run-analysis `
      -Method POST -ContentType "application/json" `
      -Body '{"topic": "AI Agent Adoption for Banking"}').Content | ConvertFrom-Json
$id = $r.run_id

# Poll until done
do {
    Start-Sleep 3
    $s = (Invoke-WebRequest "http://localhost:8000/status/$id").Content | ConvertFrom-Json
    Write-Host "$($s.status) | $($s.current_agent) | $($s.progress_pct)%"
} while ($s.status -eq "running")

# Download report
$files = (Invoke-WebRequest "http://localhost:8000/results/$id").Content | ConvertFrom-Json
$report = $files.download_files.report
Invoke-WebRequest "http://localhost:8000/download/$id/$report" -OutFile "outputs\$report"
```

```bash
# curl (Mac/Linux)
curl -s -X POST http://localhost:8000/run-analysis \
  -H "Content-Type: application/json" \
  -d '{"topic":"AI Agent Adoption for Banking","industry":"Banking / Financial Services"}' | jq .
```

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/run-analysis` | POST | Start pipeline. Body: `{"topic": "...", "industry": "..."}` |
| `/status/{run_id}` | GET | Poll progress. Returns `status`, `current_agent`, `progress_pct`, `provider_used` |
| `/results/{run_id}` | GET | Full results + `download_files` block when completed |
| `/download/{run_id}/{filename}` | GET | Download `topic-slug_report.md` or `topic-slug_matrix.csv` |
| `/credits` | GET | Live OpenRouter credit balance |
| `/health` | GET | All provider statuses + credit level |

## Output Files

Files are named by topic slug, not UUID:

```
outputs/
  ai_agent_adoption_for_banking_report.md    ← Executive report (Markdown)
  ai_agent_adoption_for_banking_matrix.csv   ← Decision matrix (CSV)
```

## Running Tests

```bash
# All tests
pytest backend/tests/ -v

# Agents only
pytest backend/tests/test_agents.py -v

# MCP tools only
pytest backend/tests/test_mcp_tools.py -v

# With coverage
pytest backend/tests/ --cov=backend --cov-report=term-missing
```

## Daily Usage (8 runs/day)

Check credits before running:
```bash
curl http://localhost:8000/credits
```

Estimated cost per run with OpenRouter gpt-4o: ~$0.20–0.35  
Estimated cost for 8 runs/day: ~$1.60–2.80/day

If OpenRouter quota is exhausted or credits are low, the pipeline automatically falls back to Groq (free) then Anthropic.

## Troubleshooting

| Error | Cause | Fix |
|---|---|---|
| `402 Payment Required` from OpenRouter | Per-key credit limit is $0 | Go to openrouter.ai/keys → set key credit limit to $10 |
| `401 Missing Authentication header` | Wrong API key in `.env` | Check `OPENROUTER_API_KEY` starts with `sk-or-v1-` |
| `GraphRecursionError` | Old `workflow.py` without `error_node` | Replace with updated `workflow.py` from this repo |
| `All models exhausted` | Groq free tier daily quota used | Wait for midnight UTC reset, or add `ANTHROPIC_API_KEY` |
| `uvicorn: module not found` | Running from wrong directory | Run from project root, use `uvicorn main:app` not `uvicorn backend.main:app` |

## Project Structure

```
FDE-financial-analysis/
├── main.py                          ← FastAPI entry point (project root)
├── .env                             ← API keys (never commit)
├── .env.template                    ← Template
├── log_config.json                  ← Uvicorn log config
├── test_setup.py                    ← Setup verification script
├── requirements.txt
├── README.md
├── CLAUDE.md                        ← AI coding governance
├── backend/
│   ├── config.py                    ← All env-driven config
│   ├── state.py                     ← AnalysisState TypedDict
│   ├── workflow.py                  ← LangGraph graph definition
│   ├── groq_fallback.py             ← Multi-provider LLM chain
│   ├── agents/
│   │   ├── research_agent.py
│   │   ├── extraction_agent.py
│   │   ├── analysis_agent.py
│   │   ├── risk_agent.py
│   │   ├── scoring_agent.py
│   │   ├── report_writer_agent.py
│   │   └── qa_agent.py
│   ├── mcp_server/
│   │   ├── tools.py                 ← All I/O isolated here
│   │   └── exceptions.py
│   └── tests/
│       ├── test_agents.py
│       └── test_mcp_tools.py
├── docs/
│   ├── SDD.md
│   ├── architecture.md
│   └── ai-build-log.md
├── prompts/
│   └── engineering-prompts.md
└── outputs/
    └── sample-run-ai_agent_adoption_for_banking/
```
