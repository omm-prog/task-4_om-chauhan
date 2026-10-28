"""
Test D: Actual process restart and cross-process resume.

Verifies:
1. Subprocess 1 executes workflow until human review pause and exits.
2. Subprocess 2 inspects state from persistent SQLite database.
3. Subprocess 3 approves the workflow.
4. Subprocess 4 resumes execution to completion.
5. Prior evaluation and revision work are not re-executed upon resumption.
6. Event history accurately reflects single execution of prior stages.
"""

import os
from pathlib import Path
import subprocess
import sys
from workflow.engine import WorkflowEngine
from workflow.models import WorkflowStatus


def test_cross_process_restart_and_resume(tmp_db: str):
    root_dir = Path(__file__).resolve().parent.parent
    src_dir = root_dir / "src"
    fixture_path = root_dir / "fixtures" / "content.json"
    run_id = "test-process-restart-001"

    env = os.environ.copy()
    env["PYTHONPATH"] = str(src_dir)

    # --- Subprocess 1: Initial run pausing at human review ---
    proc1 = subprocess.run(
        [
            sys.executable,
            "-m",
            "workflow",
            "--db",
            tmp_db,
            "run",
            "--content",
            str(fixture_path),
            "--scenario",
            "revise_then_review",
            "--run-id",
            run_id,
        ],
        cwd=str(root_dir),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc1.returncode == 0, f"Subprocess 1 failed:\n{proc1.stderr}"
    assert "Workflow paused at human-review boundary" in proc1.stdout
    assert "REVISION COUNT:  1" in proc1.stdout.upper()

    # --- Subprocess 2: Fresh process inspects status ---
    proc2 = subprocess.run(
        [
            sys.executable,
            "-m",
            "workflow",
            "--db",
            tmp_db,
            "status",
            "--run-id",
            run_id,
        ],
        cwd=str(root_dir),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc2.returncode == 0, f"Subprocess 2 failed:\n{proc2.stderr}"
    assert "STATUS:          IN_REVIEW" in proc2.stdout.upper()
    assert "REVISION COUNT:  1" in proc2.stdout.upper()

    # --- Subprocess 3: Fresh process issues approval ---
    proc3 = subprocess.run(
        [
            sys.executable,
            "-m",
            "workflow",
            "--db",
            tmp_db,
            "approve",
            "--run-id",
            run_id,
            "--decision",
            "approve",
            "--reviewer",
            "RestartVerifier",
        ],
        cwd=str(root_dir),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc3.returncode == 0, f"Subprocess 3 failed:\n{proc3.stderr}"
    assert "recorded approval" in proc3.stdout

    # --- Subprocess 4: Fresh process resumes workflow ---
    proc4 = subprocess.run(
        [
            sys.executable,
            "-m",
            "workflow",
            "--db",
            tmp_db,
            "resume",
            "--run-id",
            run_id,
        ],
        cwd=str(root_dir),
        env=env,
        capture_output=True,
        text=True,
    )
    assert proc4.returncode == 0, f"Subprocess 4 failed:\n{proc4.stderr}"
    assert "completed and verified" in proc4.stdout

    # --- Verification in parent process via independent engine ---
    engine = WorkflowEngine(db_path=tmp_db)
    final_state = engine.status(run_id)
    assert final_state is not None
    assert final_state.status == WorkflowStatus.COMPLETED
    assert final_state.revision_count == 1

    # Verify event counts to prove prior evaluation/revision was NOT repeated
    events = engine.events(run_id)
    eval_started = [e for e in events if e.event_type == "evaluation_started"]
    rev_completed = [e for e in events if e.event_type == "revision_completed"]
    completed_events = [e for e in events if e.event_type == "workflow_completed"]

    assert len(eval_started) == 2, f"Evaluation should run exactly twice (before pause), found {len(eval_started)}"
    assert len(rev_completed) == 1, f"Revision should run exactly once, found {len(rev_completed)}"
    assert len(completed_events) == 1, f"Completion event must occur exactly once, found {len(completed_events)}"

    engine.close()
