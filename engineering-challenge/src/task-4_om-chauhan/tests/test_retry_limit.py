"""
Test C: Bounded execution and retry limit enforcement.

Verifies:
1. Evaluator repeatedly returning 'revise' stops after configurable maximum (default: 2).
2. Terminal state is retry_limit_reached with an explicit reason recorded.
3. No infinite loop occurs.
4. Event history clearly logs retry_limit_reached.
5. Configurable limits (e.g. limit=1) are respected.
"""

from workflow.engine import WorkflowEngine
from workflow.evaluator import DeterministicFakeEvaluator
from workflow.models import ContentItem, WorkflowStage, WorkflowStatus
from workflow.reviser import DeterministicReviser


def test_default_retry_limit_reached(tmp_db: str, sample_revise_content: ContentItem):
    evaluator = DeterministicFakeEvaluator(scenario="retry_limit")
    reviser = DeterministicReviser()
    engine = WorkflowEngine(db_path=tmp_db, evaluator=evaluator, reviser=reviser)
    run_id = "test-run-retry-limit-001"

    # Run with default limit 2
    state = engine.run(run_id=run_id, content=sample_revise_content, revision_limit=2)

    # Verify terminal status and reason
    assert state.status == WorkflowStatus.RETRY_LIMIT_REACHED
    assert state.current_stage == WorkflowStage.RETRY_LIMIT_REACHED
    assert state.revision_count == 2
    assert "Revision limit of 2 reached" in (state.rejection_reason or "")

    # Check exact execution counts
    assert reviser.call_count == 2
    assert evaluator.call_count == 3  # Initial + after rev 1 + after rev 2

    # Verify events
    events = engine.events(run_id)
    types = [e.event_type for e in events]
    assert types[-1] == "retry_limit_reached"
    assert "workflow_completed" not in types

    engine.close()


def test_custom_retry_limit_one(tmp_db: str, sample_revise_content: ContentItem):
    evaluator = DeterministicFakeEvaluator(scenario="retry_limit")
    reviser = DeterministicReviser()
    engine = WorkflowEngine(db_path=tmp_db, evaluator=evaluator, reviser=reviser)
    run_id = "test-run-limit-one-002"

    # Run with custom limit 1
    state = engine.run(run_id=run_id, content=sample_revise_content, revision_limit=1)

    assert state.status == WorkflowStatus.RETRY_LIMIT_REACHED
    assert state.revision_count == 1
    assert reviser.call_count == 1
    assert evaluator.call_count == 2

    engine.close()
