from __future__ import annotations

from typing import Any, Dict


def build_feedback_workflow() -> Any:
    """Return a minimal feedback processing workflow (scaffold).

    Returns None if langgraph not installed to avoid hard dependency.
    """
    try:
        from langgraph.graph import StateGraph  # type: ignore
    except Exception:
        return None

    def process_feedback(state: Dict[str, Any]) -> Dict[str, Any]:
        return state

    g = StateGraph(dict)
    g.add_node("ProcessFeedback", process_feedback)
    g.set_entry_point("ProcessFeedback")
    return g.compile()


