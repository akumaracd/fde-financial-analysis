from langgraph.graph import StateGraph, END
from backend.state import AnalysisState
from backend.agents import (
    research_agent,
    extraction_agent,
    analysis_agent,
    risk_agent,
    scoring_agent,
    report_writer_agent,
    qa_agent,
)
from backend.config import cfg


def qa_routing(state: dict) -> str:
    """
    Routes after qa_node.

    Returns "END" if quality passes or max iterations hit.
    Returns "analysis_node" to loop back for another attempt.

    This is the standalone routing function referenced in the
    implementation guide, Step 5.2 — it's why the graph can retry the
    analysis -> risk -> scoring -> report -> qa chain instead of
    accepting a poor report on the first pass.
    """
    score = state.get("qa_score", 0.0)
    iteration = state.get("iteration_count", 0)

    if score >= cfg.QA_PASS_THRESHOLD:
        return "END"

    if iteration >= cfg.MAX_QA_ITERATIONS:
        # Safety valve: stop looping and accept the best effort produced
        # so far, rather than retrying forever on a topic the LLM
        # struggles to score well on.
        return "END"

    # Mutating state directly here (rather than returning it) matches
    # LangGraph's conditional-edge convention: this function's return
    # value selects the next node, it doesn't merge into state the way
    # a node's return dict does. Bumping iteration_count here ensures
    # the safety valve above eventually trips.
    state["iteration_count"] = iteration + 1
    return "analysis_node"


def build_graph():
    """
    Wire all seven agent nodes into a LangGraph StateGraph and compile it.

    Pipeline: research -> extract -> analysis -> risk -> scoring -> report -> qa
    qa_node then either ends the run (score high enough, or iteration
    budget exhausted) or loops back to analysis_node for another attempt
    using qa_agent's feedback.
    """
    graph = StateGraph(AnalysisState)

    graph.add_node("research_node", research_agent.run)
    graph.add_node("extractor_node", extraction_agent.run)
    graph.add_node("analysis_node", analysis_agent.run)
    graph.add_node("risk_node", risk_agent.run)
    graph.add_node("scoring_node", scoring_agent.run)
    graph.add_node("report_node", report_writer_agent.run)
    graph.add_node("qa_node", qa_agent.run)

    graph.set_entry_point("research_node")

    # Linear chain up through the first report attempt.
    graph.add_edge("research_node", "extractor_node")
    graph.add_edge("extractor_node", "analysis_node")
    graph.add_edge("analysis_node", "risk_node")
    graph.add_edge("risk_node", "scoring_node")
    graph.add_edge("scoring_node", "report_node")
    graph.add_edge("report_node", "qa_node")

    # Conditional edge: qa_routing decides whether to stop here or send
    # the run back through analysis_node for a re-attempt.
    graph.add_conditional_edges(
        "qa_node",
        qa_routing,
        {"END": END, "analysis_node": "analysis_node"},
    )

    return graph.compile()


# Compiled once at import time and reused for every request/run — this is
# what run_pipeline.py and (in Phase 5) main.py invoke.
ANALYSIS_GRAPH = build_graph()
