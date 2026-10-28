"""
Test F: Input validation, untrusted data handling, and failure modes.

Verifies:
1. Malformed or empty content items are rejected before workflow start.
2. Invalid revision limits (< 1) are rejected.
3. Unknown run IDs produce informative errors.
4. Content is treated as untrusted data and executable tags are disarmed.
5. Malformed evaluator exceptions transition safely to 'failed' status and log 'workflow_failed'.
"""

import pytest
from pydantic import ValidationError

from workflow.engine import WorkflowEngine
from workflow.evaluator import DeterministicFakeEvaluator
from workflow.models import ContentItem, WorkflowStage, WorkflowStatus


def test_empty_content_rejection():
    """Verify empty or whitespace-only content fields are rejected by Pydantic."""
    with pytest.raises(ValidationError):
        ContentItem(id="", title="Valid Title", body="Valid Body")

    with pytest.raises(ValidationError):
        ContentItem(id="id-1", title="   ", body="Valid Body")

    with pytest.raises(ValidationError):
        ContentItem(id="id-1", title="Valid Title", body="")


def test_invalid_revision_limit_rejection(tmp_db: str, sample_content: ContentItem):
    """Verify non-positive revision limits are rejected."""
    engine = WorkflowEngine(db_path=tmp_db)
    with pytest.raises(ValueError, match="revision_limit must be at least 1"):
        engine.run(run_id="test-val-001", content=sample_content, revision_limit=0)
    engine.close()


def test_empty_run_id_rejection(tmp_db: str, sample_content: ContentItem):
    """Verify empty run IDs are rejected."""
    engine = WorkflowEngine(db_path=tmp_db)
    with pytest.raises(ValueError, match="run_id cannot be empty"):
        engine.run(run_id="  ", content=sample_content)
    engine.close()


def test_unknown_run_id_approval(tmp_db: str):
    """Verify attempting to approve an unknown workflow fails gracefully."""
    engine = WorkflowEngine(db_path=tmp_db)
    with pytest.raises(ValueError, match="not found"):
        engine.approve(run_id="non-existent-run-999")
    engine.close()


def test_untrusted_content_sanitization():
    """Verify executable payload strings in content are disarmed into passive text."""
    item = ContentItem(
        id="content-untrusted-001",
        title="Security Test",
        body="Normal text with malicious <script>alert('pwned')</script> and javascript:void(0)",
    )
    assert "<script>" not in item.body
    assert "[DISARMED]" in item.body


def test_malformed_evaluator_response_handling(tmp_db: str, sample_content: ContentItem):
    """Verify malformed evaluator exceptions transition safely to failed status."""
    evaluator = DeterministicFakeEvaluator(scenario="malformed")
    engine = WorkflowEngine(db_path=tmp_db, evaluator=evaluator)
    run_id = "test-malformed-001"

    state = engine.run(run_id=run_id, content=sample_content)
    assert state.status == WorkflowStatus.FAILED
    assert state.current_stage == WorkflowStage.FAILED
    assert "Evaluator failed" in (state.error_message or "")

    events = engine.events(run_id)
    failed_events = [e for e in events if e.event_type == "workflow_failed"]
    assert len(failed_events) == 1

    engine.close()
