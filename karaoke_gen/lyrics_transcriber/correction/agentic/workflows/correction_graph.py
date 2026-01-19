from __future__ import annotations

from typing import Dict, Any, List, Annotated
from typing_extensions import TypedDict


class CorrectionState(TypedDict):
    """State for the correction workflow.
    
    This is a minimal state for now, but can be expanded as we add
    more sophisticated correction logic (e.g., multi-step reasoning,
    validation loops, etc.)
    """
    prompt: str
    proposals: List[Dict[str, Any]]


def build_correction_graph(callbacks=None) -> Any:
    """Build a LangGraph workflow for lyrics correction.
    
    Currently a simple pass-through, but structured to allow future
    expansion with multi-step reasoning, validation loops, etc.
    
    Args:
        callbacks: Optional callbacks (e.g., Langfuse handlers) to attach
        
    Returns:
        Compiled LangGraph or None if LangGraph not installed
    """
    try:
        from langgraph.graph import StateGraph, END
    except ImportError:
        return None

    def correction_node(state: CorrectionState) -> CorrectionState:
        """Main correction node - currently a pass-through.
        
        Future expansion: This could invoke sub-agents, do multi-step
        reasoning, or implement validation loops.
        """
        # For now, just pass through - actual correction happens in provider
        return state

    # Build the graph
    graph_builder = StateGraph(CorrectionState)
    graph_builder.add_node("correct", correction_node)
    graph_builder.set_entry_point("correct")
    graph_builder.set_finish_point("correct")
    
    # Compile with optional callbacks
    # Note: Per Langfuse docs, we can use .with_config() to add callbacks
    compiled = graph_builder.compile()
    
    if callbacks:
        return compiled.with_config({"callbacks": callbacks})
    
    return compiled


