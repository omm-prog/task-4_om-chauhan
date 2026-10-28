"""
Test B: One revision then human review workflow.

Verifies:
1. Evaluator requests revision on attempt 0.
2. Content is deterministically revised (version incremented, body updated).
3. Re-evaluation clears for human review on attempt 1.
4. Checkpoint is saved with revision_count == 1.
5. Audit events reflect complete lifecycle.
6. Resume after pause does NOT rerun evaluation or revision.
"""

from workflow.engine import WorkflowEngine
from workflow.evaluator import DeterministicFakeEvaluator
from workflow.models import ContentItem, WorkflowStage, WorkflowStatus
from workflow.reviser import DeterministicReviser


def test_one_revision_then_human_review(tmp_db: str, sample_revise_content: ContentItem):
    evaluator = DeterministicFakeEvaluator(scenario="revise_then_review")
    reviser = DeterministicReviser()
    engine = WorkflowEngine(db_path=tmp_db, evaluator=evaluator, reviser=reviser)
    run_id = "test-run-revision-001"

    # 1. Run workflow with revise_then_review
    state = engine.run(run_id=run_id, content=sample_revise_content, revision_limit=2)

    # 2. Verify paused at human review after 1 revision
    assert state.status == WorkflowStatus.IN_REVIEW
    assert state.current_stage == WorkflowStage.HUMAN_REVIEW
    assert state.revision_count == 1
    assert state.content.version == 2
    assert "[Revision 1 Applied]" in state.content.body
    assert evaluator.call_count == 2  # Evaluation 0 (revise) + Evaluation 1 (human_review)
    assert reviser.call_count == 1

    # 3. Verify audit event sequence
    events = engine.events(run_id)
    types = [e.event_type for e in events]
    assert types == [
        "workflow_started",
        "evaluation_started",
        "evaluation_completed",
        "revision_requested",
        "revision_completed",
        "evaluation_started",
        "evaluation_completed",
        "human_review_required",
        "checkpoint_saved",
    ]

    # 4. Approve and Resume
    engine.approve(run_id=run_id, decision="approve", reviewer="Senior Editor", notes="Revision looks solid.")
    completed_state = engine.resume(run_id=run_id)

    # 5. Verify terminal completion
    assert completed_state.status == WorkflowStatus.COMPLETED
    assert completed_state.current_stage == WorkflowStage.COMPLETED
    assert completed_state.revision_count == 1

    # 6. Verify work was not repeated
    assert evaluator.call_count == 2
    assert reviser.call_count == 1

    # 7. Check final event sequence
    final_events = engine.events(run_id)
    final_types = [e.event_type for e in final_events]
    assert final_types[-2:] == ["human_approved", "workflow_completed"]

    engine.close()
