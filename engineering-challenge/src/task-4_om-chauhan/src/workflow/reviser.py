"""
Deterministic reviser and protocol for content modification.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Protocol, runtime_checkable
from workflow.models import ContentItem


@runtime_checkable
class ContentReviser(Protocol):
    """Protocol for revising content based on evaluation feedback."""
    def revise(self, content: ContentItem, feedback: str, revision_count: int) -> ContentItem:
        """
        Revise a content item given evaluator feedback.
        
        Args:
            content: Current content item.
            feedback: Feedback or critique from evaluator.
            revision_count: Current revision attempt number (0-indexed before revision).
            
        Returns:
            New revised ContentItem with incremented version and updated body.
        """
        ...


class DeterministicReviser:
    """
    Deterministic reviser that applies predictable, inspectable updates to content.
    """

    def __init__(self):
        self.call_count = 0

    def revise(self, content: ContentItem, feedback: str, revision_count: int) -> ContentItem:
        self.call_count += 1
        new_version = content.version + 1
        revision_note = f"\n\n[Revision {revision_count + 1} Applied]: Addressed '{feedback}'"
        updated_body = content.body + revision_note

        updated_metadata = dict(content.metadata)
        updated_metadata["last_revision_attempt"] = revision_count + 1
        updated_metadata["last_revision_feedback"] = feedback
        updated_metadata["revised_at"] = datetime.now(timezone.utc).isoformat()

        return ContentItem(
            id=content.id,
            title=content.title,
            body=updated_body,
            author=content.author,
            tags=list(content.tags) + [f"revised-v{new_version}"],
            metadata=updated_metadata,
            version=new_version
        )
