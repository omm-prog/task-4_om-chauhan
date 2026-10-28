"""
Persistence management: SQLite database connections and LangGraph checkpointer.
"""

from __future__ import annotations

from pathlib import Path
import sqlite3
from typing import Tuple

from langgraph.checkpoint.sqlite import SqliteSaver
from workflow.event_store import EventStore


def get_sqlite_connection(db_path: str | Path, timeout: float = 30.0) -> sqlite3.Connection:
    """
    Open an SQLite connection configured with WAL mode, busy timeout,
    and multi-thread execution support.
    """
    path = Path(db_path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(path), check_same_thread=False, timeout=timeout)
    if str(path) != ":memory:":
        try:
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA busy_timeout=30000;")
        except Exception:
            pass
    return conn


def init_storage(db_path: str | Path) -> Tuple[sqlite3.Connection, SqliteSaver, EventStore]:
    """
    Initialize storage with dedicated connections for checkpointing and event logging.
    
    Using separate connection handles prevents transaction nesting deadlocks
    between LangGraph checkpointer threads and the event store.
    """
    # 1. Connection dedicated to LangGraph SqliteSaver
    saver_conn = get_sqlite_connection(db_path)
    saver = SqliteSaver(saver_conn)
    saver.setup()

    # 2. Connection dedicated to EventStore & Approvals
    event_conn = get_sqlite_connection(db_path)
    event_store = EventStore(event_conn)

    return saver_conn, saver, event_store
