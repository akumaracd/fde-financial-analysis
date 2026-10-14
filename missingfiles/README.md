# FDE Financial Analysis — AI-Powered Research Pipeline

An autonomous 7-agent pipeline that researches any financial topic, identifies regulatory risks, scores investment approaches, and produces a client-ready executive report — using **Groq** (free LLM API) and **DuckDuckGo** (free search, no key required).

---

## Architecture Overview

```
POST /run-analysis
        │
        ▼
┌─────────────────────────────────────────────────────┐
│                  LangGraph Pipeline                  │
│                                                      │
│  Research → Extraction → Analysis → Risk →           │
│  Scoring → Report Writer → QA ──► END                │
│                  ▲                  │                │
│                  └── re-analyse ────┘ (if score < 7) │
└─────────────────────────────────────────────────────┘
        │
        ▼
  outputs/{run_id}_report.md
  outputs/{run_id}_matrix.csv
```

---

## Quick Start

### 1. Get a free Groq API key

Go to [https://console.groq.com](https://console.groq.com), sign up, and create an API key. It's free with generous rate limits.

### 2. Clone and create your virtual environment

```bash
git clone <your-repo-url>
cd fde-financial-analysis
python -m venv venv

# Windows
venv\Scripts\activate

# macOS / Linux
source venv/bin/activate
```

### 3. Install dependencies

```bash
pip install -r requirements.txt
```

### 4. Configure environment

Create a `.env` file in the project root with a single line:

```
GROQ_API_KEY=gsk_your_key_here
```

All other settings have sensible defaults (see `backend/config.py`).

### 5. Verify setup

```bash
python test_setup.py
```

Expected output:
```
✅ GROQ_API_KEY found
✅ DuckDuckGo reachable
✅ Groq API reachable
✅ Output directory writable
All checks passed — ready to run.
```

### 6. Start the server

```bash
uvicorn backend.main:app --reload --port 8000
```

---

## Running an Analysis

### Submit a topic

```powershell
# PowerShell
(Invoke-WebRequest -Uri http://localhost:8000/run-analysis `
  -Method POST -ContentType "application/json" `
  -Body '{"topic": "AI Agent Adoption for Banking", "industry": "Banking / Financial Services"}').Content
```

```bash
# bash / curl
curl -X POST http://localhost:8000/run-analysis \
  -H "Content-Type: application/json" \
  -d '{"topic": "AI Agent Adoption for Banking"}'
```

Response:
```json
{"run_id": "abc-123", "status": "started", "poll_url": "/status/abc-123"}
```

### Poll for status

```powershell
(Invoke-WebRequest -Uri http://localhost:8000/status/abc-123).Content
```

### Get full results

```powershell
(Invoke-WebRequest -Uri http://localhost:8000/results/abc-123).Content
```

### Download output files

```powershell
Invoke-WebRequest -Uri http://localhost:8000/download/abc-123/abc-123_report.md -OutFile report.md
Invoke-WebRequest -Uri http://localhost:8000/download/abc-123/abc-123_matrix.csv -OutFile matrix.csv
```

---

## API Reference

| Method | Endpoint | Description |
|--------|----------|-------------|
| `GET` | `/health` | LLM + search backend status |
| `POST` | `/run-analysis` | Start a pipeline run (returns `run_id`) |
| `GET` | `/status/{run_id}` | Lightweight poll — status + current agent |
| `GET` | `/results/{run_id}` | Full state once completed |
| `GET` | `/download/{run_id}/{filename}` | Download `.md` report or `.csv` matrix |

### Request body for `/run-analysis`

```json
{
  "topic": "string (required)",
  "industry": "string (optional, default: Banking / Financial Services)"
}
```

---

## Configuration

All settings are driven by environment variables. Defaults work out of the box except for `GROQ_API_KEY`.

| Variable | Default | Description |
|----------|---------|-------------|
| `GROQ_API_KEY` | — | **Required.** Your Groq API key |
| `LLM_MODEL` | `openai/gpt-oss-120b` | Model served by Groq |
| `LLM_TEMPERATURE` | `0.3` | Sampling temperature |
| `LLM_MAX_TOKENS` | `2000` | Max tokens per LLM call |
| `OUTPUT_DIR` | `./outputs` | Where reports and matrices are written |
| `SCRAPE_MAX_CHARS` | `8000` | Max characters scraped per source |
| `QA_PASS_THRESHOLD` | `7.0` | Minimum QA score to accept report |
| `MAX_QA_ITERATIONS` | `2` | Max re-analysis loops before accepting best effort |

---

## Running Tests

```bash
pytest backend/tests/ -v --cov=backend --cov-report=term-missing
```

163 tests, fully offline (all LLM and network calls are mocked).

---

## Project Structure

```
fde-financial-analysis/
├── .env                          # Your API key (never commit this)
├── README.md
├── CLAUDE.md                     # AI coding governance
├── pytest.ini
├── test_setup.py                 # Pre-flight check script
├── backend/
│   ├── config.py                 # All env-driven settings
│   ├── main.py                   # FastAPI app + endpoints
│   ├── state.py                  # AnalysisState TypedDict
│   ├── workflow.py               # LangGraph graph definition
│   ├── agents/
│   │   ├── research_agent.py
│   │   ├── extraction_agent.py
│   │   ├── analysis_agent.py
│   │   ├── risk_agent.py
│   │   ├── scoring_agent.py
│   │   ├── report_writer_agent.py
│   │   └── qa_agent.py
│   ├── mcp_server/
│   │   ├── tools.py              # search_sources, scrape_content, write_report
│   │   └── exceptions.py         # SearchError, ScrapeError, FileWriteError
│   └── tests/
│       ├── conftest.py
│       ├── test_agents.py
│       ├── test_mcp_tools.py
│       ├── test_config.py
│       ├── test_exceptions.py
│       ├── test_state.py
│       └── test_main.py
├── docs/
│   ├── SDD.md
│   ├── architecture.md
│   └── ai-build-log.md
├── prompts/
│   └── engineering-prompts.md
└── outputs/
    └── sample-run-*/             # Committed sample pipeline outputs
```

---

## Dependencies

Key packages (see `requirements.txt` for full list):

- `fastapi` + `uvicorn` — REST API server
- `langgraph` — agent orchestration graph
- `openai` — Groq-compatible LLM client
- `duckduckgo-search` — free web search
- `beautifulsoup4` — HTML scraping
- `requests` — HTTP client
- `python-dotenv` — `.env` loading
- `pytest` + `pytest-cov` + `pytest-mock` — testing
