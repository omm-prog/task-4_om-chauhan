"""
Structured audit event logging and approval staging backed by SQLite.
"""

from __future__ import annotations

from datetime import datetime, timezone
import json
import sqlite3
from typing import Any, Dict, List, Optional
import uuid

from workflow.models import WorkflowEvent, WorkflowStage


class EventStore:
    """
    Persistent, structured audit event log and approval state manager.
    
    Guarantees ordering, inspectability, and idempotent completion events.
    """

    def __init__(self, db_path_or_conn: Any):
        if isinstance(db_path_or_conn, sqlite3.Connection):
            self.conn = db_path_or_conn
            self._should_close = False
        else:
            self.conn = sqlite3.connect(str(db_path_or_conn), check_same_thread=False)
            self._should_close = True
        self.init_schema()

    def init_schema(self) -> None:
        """Create the events and approvals tables and indexes if they do not exist."""
        with self.conn:
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS workflow_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    event_id TEXT UNIQUE NOT NULL,
                    run_id TEXT NOT NULL,
                    event_type TEXT NOT NULL,
                    stage TEXT NOT NULL,
                    timestamp TEXT NOT NULL,
                    details TEXT NOT NULL
                );
            """)
            self.conn.execute("""
                CREATE INDEX IF NOT EXISTS idx_workflow_events_run_id 
                ON workflow_events(run_id);
            """)
            # Strict database constraint: exactly one workflow_completed per run_id
            self.conn.execute("""
                CREATE UNIQUE INDEX IF NOT EXISTS uq_run_workflow_completed 
                ON workflow_events(run_id, event_type) 
                WHERE event_type = 'workflow_completed';
            """)
            # Staged approval records for resume coordination
            self.conn.execute("""
                CREATE TABLE IF NOT EXISTS workflow_approvals (
                    run_id TEXT PRIMARY KEY,
                    decision TEXT NOT NULL,
                    reviewer TEXT NOT NULL,
                    notes TEXT,
                    created_at TEXT NOT NULL
                );
            """)

    def append_event(
        self,
        run_id: str,
        event_type: str,
        stage: WorkflowStage | str,
        details: Optional[Dict[str, Any]] = None,
        event_id: Optional[str] = None,
    ) -> Optional[WorkflowEvent]:
        """
        Append a structured event to the audit log.
        
        Guarantees idempotency for terminal completion events.
        """
        stage_str = stage.value if isinstance(stage, WorkflowStage) else str(stage)
        details_dict = details or {}
        now_iso = datetime.now(timezone.utc).isoformat()
        evt_id = event_id or f"evt-{uuid.uuid4().hex[:12]}"

        # Prevent duplicate completion events
        if event_type == "workflow_completed" and self.has_event(run_id, "workflow_completed"):
            # Return existing event rather than duplicating
            events = self.get_events(run_id)
            for e in events:
                if e.event_type == "workflow_completed":
                    return e
            return None

        details_json = json.dumps(details_dict, sort_keys=True)
        try:
            with self.conn:
                self.conn.execute(
                    """
                    INSERT INTO workflow_events (event_id, run_id, event_type, stage, timestamp, details)
                    VALUES (?, ?, ?, ?, ?, ?)
                    """,
                    (evt_id, run_id, event_type, stage_str, now_iso, details_json),
                )
            return WorkflowEvent(
                event_id=evt_id,
                run_id=run_id,
                event_type=event_type,
                timestamp=now_iso,
                stage=WorkflowStage(stage_str),
                details=details_dict,
            )
        except sqlite3.IntegrityError:
            # Handles race conditions or duplicate IDs safely
            return None

    def get_events(self, run_id: str) -> List[WorkflowEvent]:
        """Retrieve all events for a given run ID ordered by sequence."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT event_id, run_id, event_type, stage, timestamp, details
            FROM workflow_events
            WHERE run_id = ?
            ORDER BY id ASC
            """,
            (run_id,),
        )
        rows = cursor.fetchall()
        events = []
        for row in rows:
            event_id, r_id, event_type, stage_str, ts, details_json = row
            try:
                details = json.loads(details_json)
            except Exception:
                details = {}
            events.append(
                WorkflowEvent(
                    event_id=event_id,
                    run_id=r_id,
                    event_type=event_type,
                    timestamp=ts,
                    stage=WorkflowStage(stage_str),
                    details=details,
                )
            )
        return events

    def has_event(self, run_id: str, event_type: str) -> bool:
        """Check whether a specific event type was recorded for this run ID."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT 1 FROM workflow_events
            WHERE run_id = ? AND event_type = ?
            LIMIT 1
            """,
            (run_id, event_type),
        )
        return cursor.fetchone() is not None

    def count_events(self, run_id: str, event_type: Optional[str] = None) -> int:
        """Count events matching criteria."""
        cursor = self.conn.cursor()
        if event_type:
            cursor.execute(
                "SELECT COUNT(*) FROM workflow_events WHERE run_id = ? AND event_type = ?",
                (run_id, event_type),
            )
        else:
            cursor.execute(
                "SELECT COUNT(*) FROM workflow_events WHERE run_id = ?",
                (run_id,),
            )
        row = cursor.fetchone()
        return row[0] if row else 0

    def stage_approval(
        self, run_id: str, decision: str = "approve", reviewer: str = "human-reviewer", notes: str = ""
    ) -> None:
        """Stage an approval decision in the database."""
        now_iso = datetime.now(timezone.utc).isoformat()
        with self.conn:
            self.conn.execute(
                """
                INSERT INTO workflow_approvals (run_id, decision, reviewer, notes, created_at)
                VALUES (?, ?, ?, ?, ?)
                ON CONFLICT(run_id) DO UPDATE SET
                    decision=excluded.decision,
                    reviewer=excluded.reviewer,
                    notes=excluded.notes,
                    created_at=excluded.created_at
                """,
                (run_id, decision, reviewer, notes, now_iso),
            )

    def get_staged_approval(self, run_id: str) -> Optional[Dict[str, Any]]:
        """Retrieve staged approval decision for a workflow run."""
        cursor = self.conn.cursor()
        cursor.execute(
            """
            SELECT decision, reviewer, notes, created_at
            FROM workflow_approvals
            WHERE run_id = ?
            """,
            (run_id,),
        )
        row = cursor.fetchone()
        if row:
            return {
                "decision": row[0],
                "reviewer": row[1],
                "notes": row[2],
                "timestamp": row[3],
            }
        return None

    def clear_approval(self, run_id: str) -> None:
        """Clear staged approval for a workflow run."""
        with self.conn:
            self.conn.execute("DELETE FROM workflow_approvals WHERE run_id = ?", (run_id,))

    def clear_events(self, run_id: Optional[str] = None) -> None:
        """Clear events for a given run ID or all events."""
        with self.conn:
            if run_id:
                self.conn.execute("DELETE FROM workflow_events WHERE run_id = ?", (run_id,))
                self.conn.execute("DELETE FROM workflow_approvals WHERE run_id = ?", (run_id,))
            else:
                self.conn.execute("DELETE FROM workflow_events")
                self.conn.execute("DELETE FROM workflow_approvals")

    def close(self) -> None:
        if self._should_close and self.conn:
            self.conn.close()
