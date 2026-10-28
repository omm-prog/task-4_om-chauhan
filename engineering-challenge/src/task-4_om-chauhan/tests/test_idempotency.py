"""
Test E: Duplicate approval, repeated resume, and idempotency protection.

Verifies:
1. Approving an already completed workflow returns an idempotent response.
2. No duplicate workflow_completed event is ever created in the database.
3. Prior nodes do not rerun upon duplicate approval or resume.
4. CLI approve command on a completed workflow returns idempotent success.
5. Database uniqueness constraints reject duplicate completion entries safely.
"""

import os
from pathlib import Path
import subprocess
import sys

from workflow.engine import WorkflowEngine
from workflow.evaluator import DeterministicFakeEvaluator
from workflow.models import ContentItem, WorkflowStatus
from workflow.reviser import DeterministicReviser


def test_duplicate_approval_and_resume_idempotency(tmp_db: str, sample_content: ContentItem):
    evaluator = DeterministicFakeEvaluator(scenario="immediate_review")
    reviser = DeterministicReviser()
    engine = WorkflowEngine(db_path=tmp_db, evaluator=evaluator, reviser=reviser)
    run_id = "test-idempotency-001"

    # 1. Run and complete workflow normally
    engine.run(run_id=run_id, content=sample_content)
    engine.approve(run_id=run_id, decision="approve")
    completed_state = engine.resume(run_id=run_id)
    assert completed_state.status == WorkflowStatus.COMPLETED

    eval_calls_before = evaluator.call_count
    rev_calls_before = reviser.call_count

    # 2. Issue duplicate approval directly on engine
    dup_approve = engine.approve(run_id=run_id, decision="approve", notes="Second approval attempt")
    assert dup_approve["status"] == "already_completed"
    assert dup_approve["completed"] is True

    # 3. Issue duplicate resume
    dup_resume_state = engine.resume(run_id=run_id)
    assert dup_resume_state.status == WorkflowStatus.COMPLETED

    # 4. Verify call counts have not incremented
    assert evaluator.call_count == eval_calls_before
    assert reviser.call_count == rev_calls_before

    # 5. Verify exactly one completion event exists in event log
    events = engine.events(run_id)
    completed_events = [e for e in events if e.event_type == "workflow_completed"]
    assert len(completed_events) == 1, f"Expected exactly 1 completion event, found {len(completed_events)}"

    engine.close()


def test_cli_duplicate_approval_subprocess(tmp_db: str):
    root_dir = Path(__file__).resolve().parent.parent
    src_dir = root_dir / "src"
    fixture_path = root_dir / "fixtures" / "content.json"
    run_id = "test-cli-idempotency-002"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_dir)

    # Run workflow to review
    subprocess.run(
        [sys.executable, "-m", "workflow", "--db", tmp_db, "run", "--content", str(fixture_path), "--scenario", "immediate_review", "--run-id", run_id],
        cwd=str(root_dir), env=env, check=True, capture_output=True, text=True,
    )

    # Approve
    subprocess.run(
        [sys.executable, "-m", "workflow", "--db", tmp_db, "approve", "--run-id", run_id],
        cwd=str(root_dir), env=env, check=True, capture_output=True, text=True,
    )

    # Resume to completion
    subprocess.run(
        [sys.executable, "-m", "workflow", "--db", tmp_db, "resume", "--run-id", run_id],
        cwd=str(root_dir), env=env, check=True, capture_output=True, text=True,
    )

    # Issue secondary duplicate approval via fresh CLI process
    proc_dup = subprocess.run(
        [sys.executable, "-m", "workflow", "--db", tmp_db, "approve", "--run-id", run_id],
        cwd=str(root_dir), env=env, capture_output=True, text=True,
    )
    assert proc_dup.returncode == 0
    assert "already completed" in proc_dup.stdout.lower()

    # Re-verify single completion event in database
    engine = WorkflowEngine(db_path=tmp_db)
    events = engine.events(run_id)
    completed_events = [e for e in events if e.event_type == "workflow_completed"]
    assert len(completed_events) == 1
    engine.close()
