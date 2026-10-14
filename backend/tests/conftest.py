"""
conftest.py — shared pytest fixtures available to every test module.
"""
import pytest


# ---------------------------------------------------------------------------
# Minimal valid state dict that every agent's run() accepts
# ---------------------------------------------------------------------------

@pytest.fixture
def base_state():
    return {
        "topic": "AI Agent Adoption for Banking",
        "industry": "Banking / Financial Services",
        "run_id": "test-run-001",
        "sources": [],
        "extracted_content": "",
        "extraction_metadata": [],
        "analysis": "",
        "risks": [],
        "decision_matrix": [],
        "report": "",
        "report_path": "",
        "matrix_path": "",
        "qa_score": 0.0,
        "qa_feedback": "",
        "qa_pass": False,
        "status": "initialized",
        "current_agent": "",
        "iteration_count": 0,
        "error": None,
    }


@pytest.fixture
def sample_sources():
    return [
        {
            "title": "DORA Regulation Overview",
            "url": "https://example.com/dora",
            "summary": "Digital Operational Resilience Act compliance guidance.",
            "regulatory_flag": True,
            "relevance_score": 8.5,
        },
        {
            "title": "AI in Banking Trends",
            "url": "https://example.com/ai-banking",
            "summary": "Key trends in AI adoption across financial institutions.",
            "regulatory_flag": False,
            "relevance_score": 7.5,
        },
    ]


@pytest.fixture
def sample_analysis():
    return (
        "## Key Trends\nAI adoption is accelerating across tier-1 banks.\n\n"
        "## Architectural Patterns\nMicroservices and event-driven architectures dominate.\n\n"
        "## Regulatory Implications\nDORA and SR 11-7 impose strict model governance requirements.\n\n"
        "## Strategic Implications\nEarly movers gain competitive advantage in cost efficiency."
    )


@pytest.fixture
def sample_risks():
    return [
        {
            "name": "Model Governance Failure",
            "category": "Regulatory",
            "level": "Critical",
            "impact": "Non-compliance with SR 11-7 leads to regulatory action.",
            "mitigation": "Establish MRM committee with quarterly model reviews.",
            "owner": "CRO",
            "regulatory_ref": "SR 11-7",
        },
        {
            "name": "Third-Party Vendor Lock-in",
            "category": "Strategic",
            "level": "High",
            "impact": "Dependency on single LLM vendor increases switching costs.",
            "mitigation": "Adopt multi-vendor strategy with abstraction layer.",
            "owner": "CTO",
            "regulatory_ref": "DORA Article 28",
        },
    ]


@pytest.fixture
def sample_matrix():
    return [
        {
            "approach": "Build In-House",
            "regulatory_compliance": 8,
            "security_privacy": 9,
            "scalability": 6,
            "tco": 4,
            "implementation_risk": 5,
            "vendor_maturity": 7,
            "weighted_total": 6.75,
            "verdict": "Conditional",
        },
        {
            "approach": "SaaS LLM Platform",
            "regulatory_compliance": 6,
            "security_privacy": 7,
            "scalability": 9,
            "tco": 8,
            "implementation_risk": 7,
            "vendor_maturity": 9,
            "weighted_total": 7.45,
            "verdict": "Recommended",
        },
    ]
