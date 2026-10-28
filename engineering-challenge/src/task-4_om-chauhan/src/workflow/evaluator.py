"""
Deterministic fake evaluators and evaluator protocol.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable
from workflow.models import ContentItem, EvaluationDecision, EvaluationResult


@runtime_checkable
class ContentEvaluator(Protocol):
    """Protocol for evaluating content quality and compliance."""
    def evaluate(self, content: ContentItem, revision_count: int) -> EvaluationResult:
        """
        Evaluate a content item.
        
        Args:
            content: The content item to inspect.
            revision_count: Number of revisions already attempted.
            
        Returns:
            EvaluationResult with decision ('revise', 'human_review', or 'reject') and feedback.
        """
        ...


class DeterministicFakeEvaluator:
    """
    Deterministic fake evaluator designed for reliable, reproducible testing.
    
    Supports predefined scenarios ('immediate_review', 'revise_then_review',
    'retry_limit', 'malformed') or explicit decision sequences.
    """

    def __init__(
        self,
        scenario: str = "revise_then_review",
        decision_sequence: Optional[List[EvaluationDecision]] = None,
    ):
        self.scenario = scenario
        self.decision_sequence = decision_sequence
        self.call_count = 0

    def evaluate(self, content: ContentItem, revision_count: int) -> EvaluationResult:
        self.call_count += 1

        # 1. Custom decision sequence override
        if self.decision_sequence is not None:
            if revision_count < len(self.decision_sequence):
                decision = self.decision_sequence[revision_count]
            else:
                decision = self.decision_sequence[-1]

            score = 0.95 if decision == EvaluationDecision.HUMAN_REVIEW else 0.45
            return EvaluationResult(
                decision=decision,
                score=score,
                reason=f"SEQUENCE_STEP_{revision_count}",
                feedback=f"Evaluator decision from custom sequence at step {revision_count}.",
                metadata={"call_count": self.call_count, "scenario": "custom_sequence"}
            )

        # 2. Scenario-based deterministic behavior
        if self.scenario == "immediate_review":
            return EvaluationResult(
                decision=EvaluationDecision.HUMAN_REVIEW,
                score=0.96,
                reason="EXCELLENT_QUALITY",
                feedback="All checks passed. Content is cleared for human sign-off.",
                metadata={"call_count": self.call_count, "scenario": self.scenario}
            )

        elif self.scenario == "revise_then_review":
            if revision_count == 0:
                return EvaluationResult(
                    decision=EvaluationDecision.REVISE,
                    score=0.48,
                    reason="CLARITY_AND_CITATIONS_NEEDED",
                    feedback="Content needs evidence citations and expanded technical rationale.",
                    metadata={"call_count": self.call_count, "scenario": self.scenario}
                )
            else:
                return EvaluationResult(
                    decision=EvaluationDecision.HUMAN_REVIEW,
                    score=0.93,
                    reason="REVISIONS_VERIFIED",
                    feedback="Revisions addressed prior critique. Cleared for human review.",
                    metadata={"call_count": self.call_count, "scenario": self.scenario}
                )

        elif self.scenario in ("retry_limit", "always_revise"):
            return EvaluationResult(
                decision=EvaluationDecision.REVISE,
                score=0.40,
                reason="INSUFFICIENT_QUALITY",
                feedback=f"Deterministic revision request on attempt {revision_count + 1}.",
                metadata={"call_count": self.call_count, "scenario": self.scenario}
            )

        elif self.scenario == "malformed":
            # Return an invalid structure / missing required fields to trigger validation failure
            raise ValueError("Evaluator returned malformed or corrupted response payload")

        else:
            # Fallback default
            return EvaluationResult(
                decision=EvaluationDecision.HUMAN_REVIEW,
                score=0.90,
                reason="DEFAULT_PASS",
                feedback="Default evaluation pass.",
                metadata={"call_count": self.call_count, "scenario": self.scenario}
            )
