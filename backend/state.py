from typing import TypedDict


class AnalysisState(TypedDict):
    topic: str
    industry: str
    run_id: str
    sources: list[dict]
    extracted_content: str
    extraction_metadata: list[dict]
    analysis: str
    risks: list[dict]
    decision_matrix: list[dict]
    report: str
    report_path: str
    matrix_path: str
    qa_score: float
    qa_feedback: str
    qa_pass: bool
    status: str
    current_agent: str
    iteration_count: int
    error: str | None


def initial_state(
    topic: str = "",
    industry: str = "",
    run_id: str = ""
) -> AnalysisState:
    return {
        "topic": topic,
        "industry": industry,
        "run_id": run_id,
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