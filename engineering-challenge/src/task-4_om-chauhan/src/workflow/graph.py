"""
LangGraph workflow graph definition for the Resumable Review Workflow.
"""

from __future__ import annotations

from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.base import BaseCheckpointSaver

from workflow.event_store import EventStore
from workflow.evaluator import ContentEvaluator
from workflow.nodes import (
    GraphState,
    WorkflowNodes,
    route_after_evaluation,
    route_after_human_review,
)
from workflow.reviser import ContentReviser


def build_review_workflow(
    event_store: EventStore,
    evaluator: ContentEvaluator,
    reviser: ContentReviser,
    checkpointer: BaseCheckpointSaver,
):
    """
    Construct, wire, and compile the LangGraph StateGraph.
    
    Nodes:
        - evaluate: executes evaluator component
        - revise: executes reviser component and routes back to evaluate
        - retry_limit: terminal state when revision attempts reach limit
        - human_review: pauses execution using interrupt() and saves checkpoint
        - complete: records completion and enters terminal END state
        - reject: records rejection and enters terminal END state
        - fail: handles unexpected errors safely
    """
    nodes = WorkflowNodes(event_store=event_store, evaluator=evaluator, reviser=reviser)
    builder = StateGraph(GraphState)

    # Add workflow nodes
    builder.add_node("evaluate", nodes.evaluate)
    builder.add_node("revise", nodes.revise)
    builder.add_node("retry_limit", nodes.retry_limit)
    builder.add_node("human_review", nodes.human_review)
    builder.add_node("complete", nodes.complete)
    builder.add_node("reject", nodes.reject)
    builder.add_node("fail", nodes.fail)

    # Initial transition
    builder.add_edge(START, "evaluate")

    # Routing from evaluate
    builder.add_conditional_edges(
        "evaluate",
        route_after_evaluation,
        {
            "revise": "revise",
            "human_review": "human_review",
            "retry_limit": "retry_limit",
            "reject": "reject",
            "fail": "fail",
        },
    )

    # Revision loops back to evaluate
    builder.add_edge("revise", "evaluate")

    # Terminal edges for bounded retry limit
    builder.add_edge("retry_limit", END)

    # Human review routing (after interrupt / resume)
    builder.add_conditional_edges(
        "human_review",
        route_after_human_review,
        {
            "complete": "complete",
            "reject": "reject",
            "fail": "fail",
        },
    )

    # Final terminal edges
    builder.add_edge("complete", END)
    builder.add_edge("reject", END)
    builder.add_edge("fail", END)

    return builder.compile(checkpointer=checkpointer)
