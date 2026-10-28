"""
WorkflowEngine: facade managing lifecycle, graph execution, persistence, and audit logging.
"""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from langgraph.types import Command

from workflow.event_store import EventStore
from workflow.evaluator import ContentEvaluator, DeterministicFakeEvaluator
from workflow.graph import build_review_workflow
from workflow.models import (
    ContentItem,
    EvaluationResult,
    WorkflowEvent,
    WorkflowStage,
    WorkflowState,
    WorkflowStatus,
)
from workflow.persistence import init_storage
from workflow.reviser import ContentReviser, DeterministicReviser


class WorkflowEngine:
    """
    Production facade orchestrating the Resumable Review Workflow.
    
    Provides high-level methods for running, inspecting, approving, and resuming.
    """

    def __init__(
        self,
        db_path: str | Path = "workflow.db",
        evaluator: Optional[ContentEvaluator] = None,
        reviser: Optional[ContentReviser] = None,
        scenario: str = "revise_then_review",
    ):
        self.db_path = str(db_path)
        self.saver_conn, self.saver, self.event_store = init_storage(self.db_path)
        self.scenario = scenario
        self.evaluator = evaluator or DeterministicFakeEvaluator(scenario=self.scenario)
        self.reviser = reviser or DeterministicReviser()
        self.graph = build_review_workflow(
            event_store=self.event_store,
            evaluator=self.evaluator,
            reviser=self.reviser,
            checkpointer=self.saver,
        )

    def run(
        self,
        run_id: str,
        content: ContentItem,
        scenario: Optional[str] = None,
        revision_limit: int = 2,
    ) -> WorkflowState:
        """
        Start execution of a review workflow.
        
        Validates inputs, initializes state, records start event, and executes
        the graph until completion or a review pause.
        """
        if not run_id or not run_id.strip():
            raise ValueError("run_id cannot be empty or whitespace")
        run_id = run_id.strip()

        if revision_limit < 1:
            raise ValueError(f"revision_limit must be at least 1, got {revision_limit}")

        active_scenario = scenario or self.scenario

        # Recompile graph if scenario changed and no custom evaluator was explicitly injected
        if scenario and isinstance(self.evaluator, DeterministicFakeEvaluator):
            self.evaluator = DeterministicFakeEvaluator(scenario=active_scenario)
            self.graph = build_review_workflow(
                event_store=self.event_store,
                evaluator=self.evaluator,
                reviser=self.reviser,
                checkpointer=self.saver,
            )

        # Log workflow_started event
        self.event_store.append_event(
            run_id=run_id,
            event_type="workflow_started",
            stage=WorkflowStage.INITIALIZED,
            details={
                "content_id": content.id,
                "revision_limit": revision_limit,
                "scenario": active_scenario,
            },
        )

        initial_state = {
            "run_id": run_id,
            "content": content.model_dump(mode="json"),
            "status": WorkflowStatus.INITIALIZED.value,
            "current_stage": WorkflowStage.INITIALIZED.value,
            "evaluation_result": None,
            "revision_count": 0,
            "revision_limit": revision_limit,
            "approval_state": None,
            "rejection_reason": None,
            "error_message": None,
            "evaluator_scenario": active_scenario,
        }

        config = {"configurable": {"thread_id": run_id}}
        self.graph.invoke(initial_state, config=config)

        state = self.status(run_id)
        if not state:
            raise RuntimeError(f"Workflow '{run_id}' failed to produce a valid state snapshot.")
        return state

    def status(self, run_id: str) -> Optional[WorkflowState]:
        """
        Inspect current workflow state from the persisted LangGraph checkpoint.
        """
        if not run_id:
            return None
        config = {"configurable": {"thread_id": run_id.strip()}}
        snapshot = self.graph.get_state(config)
        if not snapshot or not snapshot.values:
            return None

        vals = snapshot.values
        eval_result = None
        if vals.get("evaluation_result"):
            eval_result = EvaluationResult(**vals["evaluation_result"])

        content = ContentItem(**vals["content"])

        # Determine stage and status
        status_val = vals.get("status", WorkflowStatus.INITIALIZED.value)
        stage_val = vals.get("current_stage", WorkflowStage.INITIALIZED.value)

        # If graph is paused at interrupt, ensure status reflects in_review
        if snapshot.tasks and any(getattr(t, "interrupts", None) for t in snapshot.tasks):
            status_val = WorkflowStatus.IN_REVIEW.value
            stage_val = WorkflowStage.HUMAN_REVIEW.value

        return WorkflowState(
            run_id=vals.get("run_id", run_id),
            content=content,
            status=WorkflowStatus(status_val),
            current_stage=WorkflowStage(stage_val),
            evaluation_result=eval_result,
            revision_count=vals.get("revision_count", 0),
            revision_limit=vals.get("revision_limit", 2),
            approval_state=vals.get("approval_state"),
            rejection_reason=vals.get("rejection_reason"),
            error_message=vals.get("error_message"),
            checkpoint_version=getattr(snapshot, "step", 0) or 0,
            updated_at=datetime.now(timezone.utc).isoformat(),
        )

    def events(self, run_id: str) -> List[WorkflowEvent]:
        """Retrieve the ordered audit event history for a run ID."""
        return self.event_store.get_events(run_id)

    def approve(
        self,
        run_id: str,
        decision: str = "approve",
        reviewer: str = "human-reviewer",
        notes: str = "",
    ) -> Dict[str, Any]:
        """
        Record human review approval or rejection.
        
        Idempotent: if the workflow is already completed, returns a clear response
        without modifying state or logging duplicate events.
        """
        if not run_id or not run_id.strip():
            raise ValueError("run_id cannot be empty")
        run_id = run_id.strip()

        # Idempotency check: is workflow already completed?
        if self.event_store.has_event(run_id, "workflow_completed"):
            return {
                "status": "already_completed",
                "message": f"Workflow '{run_id}' is already completed. No action taken.",
                "completed": True,
            }

        state = self.status(run_id)
        if not state:
            raise ValueError(f"Workflow '{run_id}' not found. Cannot approve unknown run.")

        if state.status == WorkflowStatus.COMPLETED:
            return {
                "status": "already_completed",
                "message": f"Workflow '{run_id}' is already completed. No action taken.",
                "completed": True,
            }

        if state.status not in (WorkflowStatus.IN_REVIEW, WorkflowStatus.INITIALIZED):
            raise ValueError(
                f"Workflow '{run_id}' is in state '{state.status.value}', not awaiting human review."
            )

        # Stage the approval
        self.event_store.stage_approval(
            run_id=run_id, decision=decision, reviewer=reviewer, notes=notes
        )

        return {
            "status": "approved" if decision == "approve" else "rejected",
            "run_id": run_id,
            "decision": decision,
            "reviewer": reviewer,
            "notes": notes,
        }

    def resume(
        self,
        run_id: str,
        decision: Optional[str] = None,
        reviewer: Optional[str] = None,
        notes: Optional[str] = None,
    ) -> WorkflowState:
        """
        Resume a paused workflow from its saved checkpoint.
        
        Loads the checkpoint and advances the graph without rerunning completed nodes.
        Idempotent: if already completed, returns current state safely.
        """
        if not run_id or not run_id.strip():
            raise ValueError("run_id cannot be empty")
        run_id = run_id.strip()

        # 1. Idempotency check
        if self.event_store.has_event(run_id, "workflow_completed"):
            state = self.status(run_id)
            if state:
                return state

        config = {"configurable": {"thread_id": run_id}}
        snapshot = self.graph.get_state(config)
        if not snapshot or not snapshot.values:
            raise ValueError(f"Workflow '{run_id}' not found in checkpoints.")

        # Check if already at terminal state
        if not snapshot.next:
            state = self.status(run_id)
            if state:
                return state

        # Determine approval payload
        if decision:
            approval_payload = {
                "decision": decision,
                "reviewer": reviewer or "human-reviewer",
                "notes": notes or "",
            }
        else:
            staged = self.event_store.get_staged_approval(run_id)
            if staged:
                approval_payload = staged
            else:
                # Default to approval if invoked directly via resume
                approval_payload = {
                    "decision": "approve",
                    "reviewer": reviewer or "human-reviewer",
                    "notes": notes or "Approved via CLI resume",
                }

        # Invoke graph resume command
        self.graph.invoke(Command(resume=approval_payload), config=config)

        # Clean up staged approval
        self.event_store.clear_approval(run_id)

        updated_state = self.status(run_id)
        if not updated_state:
            raise RuntimeError(f"Workflow '{run_id}' failed to produce a resumed state.")
        return updated_state

    def reset(self, run_id: str) -> bool:
        """Development utility: safely clear state and events for a test run."""
        self.event_store.clear_events(run_id)
        with self.saver_conn:
            self.saver_conn.execute("DELETE FROM checkpoints WHERE thread_id = ?", (run_id,))
            self.saver_conn.execute("DELETE FROM writes WHERE thread_id = ?", (run_id,))
        return True

    def close(self) -> None:
        """Close database connections cleanly."""
        self.event_store.close()
        if self.saver_conn:
            self.saver_conn.close()
