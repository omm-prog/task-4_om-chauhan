"""
Command-Line Interface (CLI) for Resumable Review Workflow.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys
from typing import Optional

from workflow.engine import WorkflowEngine
from workflow.models import ContentItem, WorkflowState


def format_state(state: WorkflowState) -> str:
    """Format WorkflowState into a readable summary."""
    lines = [
        "============================================================",
        f" Workflow State: {state.run_id}",
        "============================================================",
        f" Status:          {state.status.value.upper()}",
        f" Current Stage:   {state.current_stage.value.upper()}",
        f" Revision Count:  {state.revision_count} / {state.revision_limit}",
        f" Content ID:      {state.content.id}",
        f" Content Title:   {state.content.title}",
        f" Content Version: v{state.content.version}",
    ]
    if state.evaluation_result:
        lines.append(f" Last Evaluation: {state.evaluation_result.decision.value} (score: {state.evaluation_result.score:.2f})")
        lines.append(f" Reason / Notes:  {state.evaluation_result.reason}")
        if state.evaluation_result.feedback:
            lines.append(f" Feedback:        {state.evaluation_result.feedback}")
    if state.approval_state:
        lines.append(f" Approval State:  {state.approval_state.get('decision')} by {state.approval_state.get('reviewer')}")
    if state.rejection_reason:
        lines.append(f" Rejection Note:  {state.rejection_reason}")
    if state.error_message:
        lines.append(f" Error Message:   {state.error_message}")
    lines.append("============================================================")
    return "\n".join(lines)


def format_events(events: list) -> str:
    """Format audit events into an inspectable table."""
    if not events:
        return "No events recorded."
    header = f"{'#':<3} | {'Timestamp':<25} | {'Stage':<18} | {'Event Type':<24} | Details"
    sep = "-" * 105
    rows = [header, sep]
    for idx, e in enumerate(events, 1):
        ts = e.timestamp[:23] if len(e.timestamp) >= 23 else e.timestamp
        details_str = json.dumps(e.details)
        if len(details_str) > 35:
            details_str = details_str[:32] + "..."
        rows.append(f"{idx:<3} | {ts:<25} | {e.stage.value:<18} | {e.event_type:<24} | {details_str}")
    return "\n".join(rows)


def find_fixture_path(target: str) -> Optional[Path]:
    """Resolve fixture file path across current working directory and package locations."""
    p = Path(target)
    if p.is_file():
        return p

    module_dir = Path(__file__).resolve().parent  # src/workflow
    task_root = module_dir.parent.parent         # src/task-4_om-chauhan
    candidates = [
        Path.cwd() / target,
        task_root / target,
        task_root / "fixtures" / p.name,
        Path.cwd() / "engineering-challenge" / "src" / "task-4_om-chauhan" / target,
        Path.cwd() / "engineering-challenge" / "src" / "task-4_om-chauhan" / "fixtures" / p.name,
        Path.cwd() / "fixtures" / p.name,
    ]
    for cand in candidates:
        if cand.is_file():
            return cand
    return None


def load_content_item(path_or_json: str) -> ContentItem:
    """Load and validate ContentItem from file path or raw JSON."""
    raw = path_or_json.strip()
    resolved_file = find_fixture_path(raw)
    if resolved_file:
        with open(resolved_file, "r", encoding="utf-8") as f:
            data = json.load(f)
    else:
        try:
            data = json.loads(raw)
        except Exception:
            raise ValueError(f"Content argument '{path_or_json}' is neither an existing file nor valid JSON.")
    return ContentItem(**data)


def main(args: Optional[list] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m workflow",
        description="Resumable Review Workflow Orchestration CLI",
    )
    parser.add_argument("--db", default="workflow.db", help="Path to SQLite database (default: workflow.db)")
    subparsers = parser.add_subparsers(dest="command", help="Available workflow commands")

    # Command: run
    run_parser = subparsers.add_parser("run", help="Start a new review workflow run")
    run_parser.add_argument("--run-id", default=None, help="Unique workflow run ID (e.g. demo-001)")
    run_parser.add_argument("pos_run_id", nargs="?", default=None, help="Optional positional run ID")
    run_parser.add_argument("--content", default="fixtures/content.json", help="Path to content JSON fixture or inline JSON")
    run_parser.add_argument(
        "--scenario",
        default="revise_then_review",
        choices=["immediate_review", "revise_then_review", "retry_limit", "always_revise", "malformed"],
        help="Deterministic evaluator scenario (default: revise_then_review)",
    )
    run_parser.add_argument("--limit", type=int, default=2, help="Maximum revision attempts (default: 2)")

    # Command: status
    status_parser = subparsers.add_parser("status", help="Inspect current persisted workflow state")
    status_parser.add_argument("--run-id", default=None, help="Workflow run ID")
    status_parser.add_argument("pos_run_id", nargs="?", default=None, help="Optional positional run ID")

    # Command: events
    events_parser = subparsers.add_parser("events", help="Print ordered audit event log")
    events_parser.add_argument("--run-id", default=None, help="Workflow run ID")
    events_parser.add_argument("pos_run_id", nargs="?", default=None, help="Optional positional run ID")
    events_parser.add_argument("--json", action="store_true", help="Output events as raw JSON")

    # Command: approve
    approve_parser = subparsers.add_parser("approve", help="Approve or reject a workflow waiting for human review")
    approve_parser.add_argument("--run-id", default=None, help="Workflow run ID")
    approve_parser.add_argument("pos_run_id", nargs="?", default=None, help="Optional positional run ID")
    approve_parser.add_argument("--decision", default="approve", choices=["approve", "reject"], help="Human reviewer decision")
    approve_parser.add_argument("--reviewer", default="human-reviewer", help="Reviewer identifier")
    approve_parser.add_argument("--notes", default="", help="Review notes or feedback")

    # Command: resume
    resume_parser = subparsers.add_parser("resume", help="Resume a paused workflow from its saved checkpoint")
    resume_parser.add_argument("--run-id", default=None, help="Workflow run ID")
    resume_parser.add_argument("pos_run_id", nargs="?", default=None, help="Optional positional run ID")

    # Command: demo
    subparsers.add_parser("demo", help="Run an end-to-end demonstration showing pause, restart, resume, and idempotency")

    # Command: reset
    reset_parser = subparsers.add_parser("reset", help="Reset and clear state for a specific run ID")
    reset_parser.add_argument("--run-id", default=None, help="Workflow run ID")
    reset_parser.add_argument("pos_run_id", nargs="?", default=None, help="Optional positional run ID")

    parsed = parser.parse_args(args)
    if not parsed.command:
        parser.print_help()
        return 1

    engine = WorkflowEngine(db_path=parsed.db)

    try:
        if parsed.command == "run":
            run_id = parsed.run_id or parsed.pos_run_id or "demo-001"
            content = load_content_item(parsed.content)
            print(f"\nStarting workflow '{run_id}' with scenario '{parsed.scenario}'...")
            state = engine.run(
                run_id=run_id,
                content=content,
                scenario=parsed.scenario,
                revision_limit=parsed.limit,
            )
            print(format_state(state))
            if state.status.value == "in_review":
                print("\n[PAUSED]: Workflow paused at human-review boundary. State saved to persistent checkpoint.")
                print(f"To approve: python -m workflow approve --run-id {run_id}")
                print(f"To resume:  python -m workflow resume --run-id {run_id}")
            elif state.status.value == "retry_limit_reached":
                print("\n[STOPPED]: Workflow reached maximum revision limit without passing evaluation.")
            elif state.status.value == "completed":
                print("\n[COMPLETED]: Workflow completed successfully.")

        elif parsed.command == "status":
            run_id = parsed.run_id or parsed.pos_run_id
            if not run_id:
                print("Error: --run-id is required.")
                return 1
            state = engine.status(run_id)
            if not state:
                print(f"No workflow found for run ID '{run_id}'.")
                return 1
            print(format_state(state))

        elif parsed.command == "events":
            run_id = parsed.run_id or parsed.pos_run_id
            if not run_id:
                print("Error: --run-id is required.")
                return 1
            events = engine.events(run_id)
            if getattr(parsed, "json", False):
                print(json.dumps([e.model_dump() for e in events], indent=2))
            else:
                print(f"\nAudit Event Log for '{run_id}':")
                print(format_events(events))

        elif parsed.command == "approve":
            run_id = parsed.run_id or parsed.pos_run_id
            if not run_id:
                print("Error: --run-id is required.")
                return 1
            res = engine.approve(
                run_id=run_id,
                decision=parsed.decision,
                reviewer=parsed.reviewer,
                notes=parsed.notes,
            )
            if res.get("status") == "already_completed":
                print(f"[IDEMPOTENT]: {res.get('message')}")
            else:
                print(f"[SUCCESS]: Workflow '{run_id}' recorded approval ({parsed.decision}) by {parsed.reviewer}.")
                print(f"Run 'python -m workflow resume --run-id {run_id}' to advance to completion.")

        elif parsed.command == "resume":
            run_id = parsed.run_id or parsed.pos_run_id
            if not run_id:
                print("Error: --run-id is required.")
                return 1
            print(f"\nResuming workflow '{run_id}' from checkpoint...")
            state = engine.resume(run_id=run_id)
            print(format_state(state))
            if state.status.value == "completed":
                print(f"\n[SUCCESS]: Workflow '{run_id}' completed and verified.")

        elif parsed.command == "reset":
            run_id = parsed.run_id or parsed.pos_run_id
            if not run_id:
                print("Error: --run-id is required.")
                return 1
            engine.reset(run_id)
            print(f"Successfully reset workflow '{run_id}'.")

        elif parsed.command == "demo":
            print("\n============================================================")
            print(" Starting End-to-End Demonstration")
            print("============================================================")
            demo_run_id = "demo-e2e-001"
            engine.reset(demo_run_id)
            content_path = "fixtures/content.json"
            content = load_content_item(content_path)

            print(f"\n[Step 1]: Running workflow '{demo_run_id}' with 'revise_then_review'...")
            state1 = engine.run(demo_run_id, content, scenario="revise_then_review", revision_limit=2)
            print(format_state(state1))
            assert state1.status.value == "in_review", "Workflow should pause at human review!"

            print("\n[Step 2]: Simulating process exit. Closing engine connection...")
            engine.close()

            print("\n[Step 3]: Restarting new process. Reloading from persistent SQLite database...")
            restarted_engine = WorkflowEngine(db_path=parsed.db)
            state2 = restarted_engine.status(demo_run_id)
            print(f"Reloaded state: status={state2.status.value}, revision_count={state2.revision_count}")
            assert state2.status.value == "in_review"

            print("\n[Step 4]: Approving and resuming workflow...")
            restarted_engine.approve(demo_run_id, decision="approve", notes="Signed off in demonstration")
            completed_state = restarted_engine.resume(demo_run_id)
            print(format_state(completed_state))
            assert completed_state.status.value == "completed"

            print("\n[Step 5]: Demonstrating Idempotency (Duplicate Approval)...")
            dup_res = restarted_engine.approve(demo_run_id, decision="approve")
            print(f"Duplicate approval response: {dup_res}")
            dup_resume = restarted_engine.resume(demo_run_id)
            print(f"Duplicate resume status: {dup_resume.status.value}")

            print("\n[Step 6]: Audit Event Log...")
            events = restarted_engine.events(demo_run_id)
            print(format_events(events))

            completed_events = [e for e in events if e.event_type == "workflow_completed"]
            print(f"\nTotal 'workflow_completed' events in database: {len(completed_events)} (Must be exactly 1)")
            assert len(completed_events) == 1, "Duplicate completion event detected!"

            print("\n============================================================")
            print(" Demonstration Finished Successfully! All criteria verified.")
            print("============================================================")
            restarted_engine.close()

        return 0

    except Exception as exc:
        print(f"\n[ERROR]: {exc}", file=sys.stderr)
        return 1
    finally:
        engine.close()


if __name__ == "__main__":
    sys.exit(main())
