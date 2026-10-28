"""
Test A: Immediate human review workflow.

Verifies:
1. Valid content evaluation runs once.
2. Workflow reaches human review and pauses.
3. Persistent checkpoint is saved to SQLite.
4. Relevant events are recorded.
5. Approval and resumption transitions workflow to completion.
6. Node execution counts are accurate.
"""

from workflow.engine import WorkflowEngine
from workflow.evaluator import DeterministicFakeEvaluator
from workflow.models import ContentItem, WorkflowStage, WorkflowStatus
from workflow.reviser import DeterministicReviser


def test_immediate_human_review_flow(tmp_db: str, sample_content: ContentItem):
    evaluator = DeterministicFakeEvaluator(scenario="immediate_review")
    reviser = DeterministicReviser()
    engine = WorkflowEngine(db_path=tmp_db, evaluator=evaluator, reviser=reviser)
    run_id = "test-run-immediate-001"

    # 1. Run workflow with immediate_review scenario
    state = engine.run(run_id=run_id, content=sample_content, revision_limit=2)

    # 2. Verify paused at human review
    assert state.status == WorkflowStatus.IN_REVIEW
    assert state.current_stage == WorkflowStage.HUMAN_REVIEW
    assert state.revision_count == 0
    assert state.evaluation_result is not None
    assert state.evaluation_result.decision.value == "human_review"
    assert evaluator.call_count == 1
    assert reviser.call_count == 0

    # 3. Verify persistent checkpoint exists in SQLite
    persisted_state = engine.status(run_id)
    assert persisted_state is not None
    assert persisted_state.status == WorkflowStatus.IN_REVIEW
    assert persisted_state.current_stage == WorkflowStage.HUMAN_REVIEW

    # 4. Verify audit event log entries
    events = engine.events(run_id)
    event_types = [e.event_type for e in events]
    assert "workflow_started" in event_types
    assert "evaluation_started" in event_types
    assert "evaluation_completed" in event_types
    assert "human_review_required" in event_types
    assert "checkpoint_saved" in event_types
    assert "workflow_completed" not in event_types

    # 5. Approve the review
    approve_res = engine.approve(run_id=run_id, decision="approve", reviewer="Lead Reviewer")
    assert approve_res["status"] == "approved"

    # 6. Resume from checkpoint
    completed_state = engine.resume(run_id=run_id)

    # 7. Verify terminal completion
    assert completed_state.status == WorkflowStatus.COMPLETED
    assert completed_state.current_stage == WorkflowStage.COMPLETED

    # 8. Verify post-completion events and call counts
    final_events = engine.events(run_id)
    final_types = [e.event_type for e in final_events]
    assert "human_approved" in final_types
    assert "workflow_completed" in final_types

    # Verify completed work was not rerun
    assert evaluator.call_count == 1
    assert reviser.call_count == 0

    engine.close()
