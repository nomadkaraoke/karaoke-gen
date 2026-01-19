from __future__ import annotations

from typing import Any, Dict


def build_consensus_workflow() -> Any:
    """Return a minimal consensus workflow (scaffold).

    Returns None if langgraph not installed to avoid hard dependency.
    """
    try:
        from langgraph.graph import StateGraph  # type: ignore
    except Exception:
        return None

    def merge_results(state: Dict[str, Any]) -> Dict[str, Any]:
        return state

    g = StateGraph(dict)
    g.add_node("MergeResults", merge_results)
    g.set_entry_point("MergeResults")
    return g.compile()


