"""
Pytest fixtures and configuration for review workflow tests.
"""

from pathlib import Path
import sys
import tempfile
from typing import Generator
import pytest

# Ensure src is on sys.path
SRC_DIR = Path(__file__).resolve().parent.parent / "src"
if str(SRC_DIR) not in sys.path:
    sys.path.insert(0, str(SRC_DIR))

from workflow.models import ContentItem


@pytest.fixture
def tmp_db() -> Generator[str, None, None]:
    """Provide a path to a temporary isolated SQLite database file."""
    with tempfile.NamedTemporaryFile(suffix=".db", delete=False) as f:
        db_path = f.name
    yield db_path
    # Cleanup after test
    try:
        Path(db_path).unlink(missing_ok=True)
        # WAL and SHM files
        Path(f"{db_path}-wal").unlink(missing_ok=True)
        Path(f"{db_path}-shm").unlink(missing_ok=True)
    except Exception:
        pass


@pytest.fixture
def sample_content() -> ContentItem:
    """Standard valid mock content item."""
    return ContentItem(
        id="content-test-001",
        title="Agentic Systems Engineering",
        body="Stateful agentic architectures require explicit persistence and review boundaries.",
        author="Om Chauhan",
        tags=["ai", "agents", "architecture"],
        metadata={"priority": "high"},
        version=1,
    )


@pytest.fixture
def sample_revise_content() -> ContentItem:
    """Mock content item for revision workflows."""
    return ContentItem(
        id="content-revise-001",
        title="Draft Architecture Review",
        body="Preliminary draft requiring evidence backing and expanded technical depth.",
        author="Om Chauhan",
        tags=["draft", "architecture"],
        version=1,
    )
