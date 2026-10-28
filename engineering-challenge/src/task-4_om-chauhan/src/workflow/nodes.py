"""
Graph node functions and conditional routers for review workflow.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any, Dict, Optional, TypedDict
from langgraph.types import interrupt

from workflow.event_store import EventStore
from workflow.evaluator import ContentEvaluator
from workflow.models import (
    ContentItem,
    EvaluationDecision,
    EvaluationResult,
    ReviewDecision,
    WorkflowStage,
    WorkflowStatus,
)
from workflow.reviser import ContentReviser


class GraphState(TypedDict):
    """LangGraph execution state schema."""
    run_id: str
    content: Dict[str, Any]
    status: str
    current_stage: str
    evaluation_result: Optional[Dict[str, Any]]
    revision_count: int
    revision_limit: int
    approval_state: Optional[Dict[str, Any]]
    rejection_reason: Optional[str]
    error_message: Optional[str]
    evaluator_scenario: str


class WorkflowNodes:
    """Encapsulates node execution logic bound to components and event store."""

    def __init__(
        self,
        event_store: EventStore,
        evaluator: ContentEvaluator,
        reviser: ContentReviser,
    ):
        self.event_store = event_store
        self.evaluator = evaluator
        self.reviser = reviser

    def evaluate(self, state: GraphState) -> Dict[str, Any]:
        """Node: evaluate mock content item."""
        run_id = state["run_id"]
        content_item = ContentItem(**state["content"])
        rev_count = state["revision_count"]

        # Log evaluation started
        self.event_store.append_event(
            run_id=run_id,
            event_type="evaluation_started",
            stage=WorkflowStage.EVALUATING,
            details={"revision_count": rev_count, "content_id": content_item.id},
        )

        try:
            eval_result: EvaluationResult = self.evaluator.evaluate(content_item, rev_count)
        except Exception as exc:
            self.event_store.append_event(
                run_id=run_id,
                event_type="workflow_failed",
                stage=WorkflowStage.FAILED,
                details={"error": str(exc), "stage": "evaluating"},
            )
            return {
                "status": WorkflowStatus.FAILED.value,
                "current_stage": WorkflowStage.FAILED.value,
                "error_message": f"Evaluator failed: {exc}",
            }

        # Log evaluation completed
        self.event_store.append_event(
            run_id=run_id,
            event_type="evaluation_completed",
            stage=WorkflowStage.EVALUATING,
            details={
                "decision": eval_result.decision.value,
                "score": eval_result.score,
                "reason": eval_result.reason,
                "feedback": eval_result.feedback,
                "revision_count": rev_count,
            },
        )

        return {
            "current_stage": WorkflowStage.EVALUATING.value,
            "evaluation_result": eval_result.model_dump(mode="json"),
        }

    def revise(self, state: GraphState) -> Dict[str, Any]:
        """Node: revise content item deterministically based on evaluator feedback."""
        run_id = state["run_id"]
        content_item = ContentItem(**state["content"])
        rev_count = state["revision_count"]
        feedback = ""
        if state.get("evaluation_result"):
            feedback = state["evaluation_result"].get("feedback", "")

        # Log revision requested
        self.event_store.append_event(
            run_id=run_id,
            event_type="revision_requested",
            stage=WorkflowStage.REVISING,
            details={"revision_attempt": rev_count + 1, "feedback": feedback},
        )

        # Perform deterministic revision
        revised_item: ContentItem = self.reviser.revise(content_item, feedback, rev_count)
        new_rev_count = rev_count + 1

        # Log revision completed
        self.event_store.append_event(
            run_id=run_id,
            event_type="revision_completed",
            stage=WorkflowStage.REVISING,
            details={
                "revision_count": new_rev_count,
                "new_version": revised_item.version,
            },
        )

        return {
            "content": revised_item.model_dump(mode="json"),
            "revision_count": new_rev_count,
            "current_stage": WorkflowStage.REVISING.value,
        }

    def retry_limit(self, state: GraphState) -> Dict[str, Any]:
        """Node: handle exceeding the maximum revision attempts."""
        run_id = state["run_id"]
        rev_count = state["revision_count"]
        limit = state["revision_limit"]

        self.event_store.append_event(
            run_id=run_id,
            event_type="retry_limit_reached",
            stage=WorkflowStage.RETRY_LIMIT_REACHED,
            details={
                "revision_count": rev_count,
                "revision_limit": limit,
                "reason": f"Maximum allowed revisions ({limit}) exhausted without passing evaluation.",
            },
        )

        return {
            "status": WorkflowStatus.RETRY_LIMIT_REACHED.value,
            "current_stage": WorkflowStage.RETRY_LIMIT_REACHED.value,
            "rejection_reason": f"Revision limit of {limit} reached.",
        }

    def human_review(self, state: GraphState) -> Dict[str, Any]:
        """
        Node: pause for human review and persist state.
        
        Uses LangGraph's native interrupt() mechanism.
        """
        run_id = state["run_id"]
        rev_count = state["revision_count"]

        # Only append human_review_required if not yet logged
        if not self.event_store.has_event(run_id, "human_review_required"):
            self.event_store.append_event(
                run_id=run_id,
                event_type="human_review_required",
                stage=WorkflowStage.HUMAN_REVIEW,
                details={
                    "revision_count": rev_count,
                    "content_id": state["content"]["id"],
                },
            )

        # Checkpoint is automatically saved by LangGraph SqliteSaver before interrupt
        if not self.event_store.has_event(run_id, "checkpoint_saved"):
            self.event_store.append_event(
                run_id=run_id,
                event_type="checkpoint_saved",
                stage=WorkflowStage.HUMAN_REVIEW,
                details={"run_id": run_id, "stage": "human_review"},
            )

        # Trigger interrupt and pause execution
        review_input = interrupt({
            "run_id": run_id,
            "stage": "human_review",
            "content_id": state["content"]["id"],
            "revision_count": rev_count,
            "message": "Human approval required to complete workflow.",
        })

        # --- RESUMED EXECUTION STARTS HERE ---
        # Parse resume input
        if isinstance(review_input, str):
            dec_str = review_input.lower()
            reviewer_name = "human-reviewer"
            notes = ""
        elif isinstance(review_input, dict):
            dec_str = str(review_input.get("decision", "approve")).lower()
            reviewer_name = review_input.get("reviewer", "human-reviewer")
            notes = review_input.get("notes", "")
        else:
            dec_str = "approve"
            reviewer_name = "human-reviewer"
            notes = ""

        if dec_str == "reject":
            if not self.event_store.has_event(run_id, "workflow_rejected"):
                self.event_store.append_event(
                    run_id=run_id,
                    event_type="workflow_rejected",
                    stage=WorkflowStage.REJECTED,
                    details={"reviewer": reviewer_name, "notes": notes},
                )
            return {
                "approval_state": {
                    "decision": "reject",
                    "reviewer": reviewer_name,
                    "notes": notes,
                    "timestamp": datetime.now(timezone.utc).isoformat(),
                },
                "status": WorkflowStatus.REJECTED.value,
                "current_stage": WorkflowStage.REJECTED.value,
                "rejection_reason": notes or "Rejected by human reviewer.",
            }

        # Approved
        if not self.event_store.has_event(run_id, "human_approved"):
            self.event_store.append_event(
                run_id=run_id,
                event_type="human_approved",
                stage=WorkflowStage.APPROVAL,
                details={"reviewer": reviewer_name, "notes": notes},
            )

        return {
            "approval_state": {
                "decision": "approve",
                "reviewer": reviewer_name,
                "notes": notes,
                "timestamp": datetime.now(timezone.utc).isoformat(),
            },
            "current_stage": WorkflowStage.APPROVAL.value,
        }

    def complete(self, state: GraphState) -> Dict[str, Any]:
        """Node: mark workflow completed and log completion event idempotently."""
        run_id = state["run_id"]
        rev_count = state["revision_count"]

        # Append exactly one workflow_completed event (idempotent)
        self.event_store.append_event(
            run_id=run_id,
            event_type="workflow_completed",
            stage=WorkflowStage.COMPLETED,
            details={
                "revision_count": rev_count,
                "completed_at": datetime.now(timezone.utc).isoformat(),
            },
        )

        return {
            "status": WorkflowStatus.COMPLETED.value,
            "current_stage": WorkflowStage.COMPLETED.value,
        }

    def reject(self, state: GraphState) -> Dict[str, Any]:
        """Node: terminal rejection state."""
        run_id = state["run_id"]
        return {
            "status": WorkflowStatus.REJECTED.value,
            "current_stage": WorkflowStage.REJECTED.value,
        }

    def fail(self, state: GraphState) -> Dict[str, Any]:
        """Node: terminal failure state."""
        return {
            "status": WorkflowStatus.FAILED.value,
            "current_stage": WorkflowStage.FAILED.value,
        }


def route_after_evaluation(state: GraphState) -> str:
    """Conditional edge router after evaluation."""
    if state.get("status") == WorkflowStatus.FAILED.value:
        return "fail"

    eval_result = state.get("evaluation_result")
    if not eval_result:
        return "fail"

    decision = eval_result.get("decision")
    rev_count = state.get("revision_count", 0)
    rev_limit = state.get("revision_limit", 2)

    if decision == EvaluationDecision.REVISE.value:
        if rev_count >= rev_limit:
            return "retry_limit"
        return "revise"
    elif decision == EvaluationDecision.HUMAN_REVIEW.value:
        return "human_review"
    elif decision == EvaluationDecision.REJECT.value:
        return "reject"
    else:
        return "fail"


def route_after_human_review(state: GraphState) -> str:
    """Conditional edge router after human review."""
    approval_state = state.get("approval_state")
    if not approval_state:
        # Paused at interrupt, never reached during active interrupt
        return "human_review"

    decision = approval_state.get("decision")
    if decision == "approve":
        return "complete"
    elif decision == "reject":
        return "reject"
    return "fail"
