"""
Domain models, schemas, and typed contracts for Atomity Review Workflow.
"""

from __future__ import annotations

from datetime import datetime, timezone
from enum import Enum
import re
from typing import Any, Dict, List, Optional
from pydantic import BaseModel, Field, field_validator


class EvaluationDecision(str, Enum):
    """Possible outcomes from the deterministic evaluator."""
    REVISE = "revise"
    HUMAN_REVIEW = "human_review"
    REJECT = "reject"


class WorkflowStatus(str, Enum):
    """High-level status of the review workflow."""
    INITIALIZED = "initialized"
    IN_REVIEW = "in_review"
    COMPLETED = "completed"
    REJECTED = "rejected"
    RETRY_LIMIT_REACHED = "retry_limit_reached"
    FAILED = "failed"


class WorkflowStage(str, Enum):
    """Inspectable lifecycle stages of the review workflow."""
    INITIALIZED = "initialized"
    EVALUATING = "evaluating"
    REVISING = "revising"
    HUMAN_REVIEW = "human_review"
    APPROVAL = "approval"
    COMPLETED = "completed"
    REJECTED = "rejected"
    RETRY_LIMIT_REACHED = "retry_limit_reached"
    FAILED = "failed"


class ContentItem(BaseModel):
    """
    Typed content item subject to review and revision.
    
    Treated strictly as passive data. Executable code or active content
    is never executed.
    """
    id: str = Field(..., min_length=1, description="Unique identifier for the content item")
    title: str = Field(..., min_length=1, description="Title of the content piece")
    body: str = Field(..., min_length=1, description="Body text of the content")
    author: str = Field(default="Atomity Contributor", description="Author or generating agent")
    tags: List[str] = Field(default_factory=list, description="Categorization tags")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Metadata and simulated attributes")
    version: int = Field(default=1, ge=1, description="Version counter incremented upon revision")

    @field_validator("id", "title", "body")
    @classmethod
    def check_non_empty(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("Field cannot be empty or whitespace only")
        return v.strip()

    @field_validator("body")
    @classmethod
    def validate_content_safety(cls, v: str) -> str:
        # Passive data enforcement: flag suspicious execution payloads
        dangerous_patterns = [r"<script\b[^>]*>", r"javascript:", r"__import__\b"]
        for pattern in dangerous_patterns:
            if re.search(pattern, v, re.IGNORECASE):
                # We sanitize / disarm any executable payload
                v = re.sub(pattern, "[DISARMED]", v, flags=re.IGNORECASE)
        return v


class EvaluationResult(BaseModel):
    """Outcome and feedback from the evaluator component."""
    decision: EvaluationDecision = Field(..., description="Decision: revise, human_review, or reject")
    score: float = Field(default=0.0, ge=0.0, le=1.0, description="Quality or confidence score")
    reason: str = Field(default="", description="High-level reason code for decision")
    feedback: str = Field(default="", description="Actionable critique for reviser")
    metadata: Dict[str, Any] = Field(default_factory=dict, description="Evaluator run metadata")


class ReviewDecision(BaseModel):
    """Input contract for human reviewer approval or rejection."""
    decision: str = Field(..., pattern="^(approve|reject)$", description="'approve' or 'reject'")
    reviewer: str = Field(default="human-reviewer", description="Identity of reviewer")
    notes: str = Field(default="", description="Reviewer feedback or sign-off notes")
    timestamp: str = Field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat(),
        description="UTC ISO-8601 timestamp"
    )


class WorkflowEvent(BaseModel):
    """Audit log entry for workflow execution milestones."""
    event_id: str = Field(..., description="Unique event identifier")
    run_id: str = Field(..., description="Workflow execution run ID")
    event_type: str = Field(..., description="Event type name")
    timestamp: str = Field(..., description="UTC ISO-8601 timestamp")
    stage: WorkflowStage = Field(..., description="Workflow stage when event occurred")
    details: Dict[str, Any] = Field(default_factory=dict, description="Structured event payload")


class WorkflowState(BaseModel):
    """Inspectable workflow state snapshot."""
    run_id: str
    content: ContentItem
    status: WorkflowStatus
    current_stage: WorkflowStage
    evaluation_result: Optional[EvaluationResult] = None
    revision_count: int = 0
    revision_limit: int = 2
    approval_state: Optional[Dict[str, Any]] = None
    rejection_reason: Optional[str] = None
    error_message: Optional[str] = None
    checkpoint_version: int = 0
    updated_at: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
