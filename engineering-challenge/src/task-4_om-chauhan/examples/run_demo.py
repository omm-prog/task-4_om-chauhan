"""
Executable demonstration script for the Resumable Review Workflow.

Demonstrates:
1. Running a workflow that undergoes deterministic revision and pauses at human review.
2. Persisting the checkpoint to SQLite.
3. Process restart simulation (reloading state from disk).
4. Approving and resuming to terminal completion.
5. Verifying idempotency and inspection of audit event history.
"""

from pathlib import Path
import sys

# Ensure src is in sys.path
root_dir = Path(__file__).resolve().parent.parent
src_dir = root_dir / "src"
if str(src_dir) not in sys.path:
    sys.path.insert(0, str(src_dir))

from workflow.cli import format_events, format_state, load_content_item
from workflow.engine import WorkflowEngine


def main():
    print("\n============================================================")
    print(" ATOMITY TASK 4: RESUMABLE REVIEW WORKFLOW DEMONSTRATION")
    print("============================================================\n")

    db_path = root_dir / "demo.db"
    content_fixture = root_dir / "fixtures" / "content.json"
    run_id = "demo-run-001"

    # Step 1: Initialize engine and run
    print(f"[Phase 1] Starting workflow run '{run_id}' with scenario 'revise_then_review'...")
    content = load_content_item(str(content_fixture))
    engine = WorkflowEngine(db_path=str(db_path))
    engine.reset(run_id)

    state = engine.run(run_id=run_id, content=content, scenario="revise_then_review", revision_limit=2)
    print(format_state(state))
    print(f"\n=> Status: {state.status.value.upper()} (Current Stage: {state.current_stage.value})")
    assert state.status.value == "in_review"

    # Step 2: Simulate process boundary
    print("\n[Phase 2] Simulating process termination and database persistence...")
    engine.close()
    del engine
    print("=> Process exited. SQLite file 'demo.db' contains state checkpoint.\n")

    # Step 3: Launch new process instance
    print("[Phase 3] Starting fresh process. Reconnecting to 'demo.db'...")
    reloaded_engine = WorkflowEngine(db_path=str(db_path))
    reloaded_state = reloaded_engine.status(run_id)
    print(f"=> Loaded State: Status={reloaded_state.status.value}, Revision Count={reloaded_state.revision_count}")
    assert reloaded_state.status.value == "in_review"

    # Step 4: Approve and resume
    print("\n[Phase 4] Human reviewer issuing approval...")
    reloaded_engine.approve(run_id=run_id, decision="approve", reviewer="Om Chauhan", notes="Production ready.")

    print(f"[Phase 5] Resuming workflow '{run_id}' from checkpoint...")
    final_state = reloaded_engine.resume(run_id=run_id)
    print(format_state(final_state))
    assert final_state.status.value == "completed"

    # Step 5: Duplicate approval protection
    print("\n[Phase 6] Testing duplicate approval idempotency...")
    dup_approve = reloaded_engine.approve(run_id=run_id, decision="approve")
    print(f"=> Duplicate approval response: {dup_approve}")
    dup_resume = reloaded_engine.resume(run_id=run_id)
    print(f"=> Duplicate resume status: {dup_resume.status.value}")

    # Step 6: Audit log
    print("\n[Phase 7] Inspecting complete audit event history:")
    events = reloaded_engine.events(run_id)
    print(format_events(events))

    completed_events = [e for e in events if e.event_type == "workflow_completed"]
    print(f"\nAudit check: Exactly {len(completed_events)} 'workflow_completed' event(s) recorded.")
    assert len(completed_events) == 1

    reloaded_engine.close()
    print("\n============================================================")
    print(" DEMONSTRATION COMPLETE: ALL ACCEPTANCE CHECKS PASSED")
    print("============================================================\n")


if __name__ == "__main__":
    main()
