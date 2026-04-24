"""ApproveGate - decides whether to auto-approve or flag for human review.

Based on:
- Test results (pass/fail, pass rate)
- Review results (score, findings)
- Configurable rules
"""

from dataclasses import dataclass
from enum import Enum
from typing import Any, Dict, List, Optional

from company_agent.agents.base import TaskResult
from company_agent.config import ApproveGateConfig
from company_agent.task_queue import Task, TaskStatus


class ApprovalDecision(str, Enum):
    """Possible approval decisions."""
    AUTO_APPROVED = "auto_approved"
    APPROVED = "approved"           # Human approved
    REJECTED = "rejected"
    FLAGGED = "flagged"             # Needs human review
    BLOCKED = "blocked"            # Critical issues found


@dataclass
class ApprovalCriteria:
    """Results from workflow stages to evaluate."""
    test_result: Optional[TaskResult] = None
    review_score: float = 0.0      # 0-10
    review_approved: bool = False
    has_critical_issues: bool = False
    issues: List[str] = None       # List of issues found

    def __post_init__(self):
        if self.issues is None:
            self.issues = []


class ApproveGate:
    """Decides whether to auto-approve or flag for human review.

    Approval rules (configurable):
    - Auto-approve: tests pass AND review score >= threshold
    - Flag for review: tests pass but review score < threshold
    - Reject: tests fail OR critical issues found
    """

    def __init__(self, config: Optional[ApproveGateConfig] = None):
        self.config = config or ApproveGateConfig()

    def evaluate(self, criteria: ApprovalCriteria) -> ApprovalDecision:
        """Evaluate criteria and make approval decision.

        Args:
            criteria: Results from test and review stages

        Returns:
            ApprovalDecision
        """
        issues = []

        # Check for critical issues
        if criteria.has_critical_issues:
            issues.extend(criteria.issues)
            return ApprovalDecision.REJECTED

        # Check if tests were required and passed
        if self.config.require_tests:
            if criteria.test_result is None:
                return ApprovalDecision.FLAGGED  # No test results

            if not criteria.test_result.success:
                issues.append("Tests failed")
                if self.config.min_test_pass_rate < 1.0:
                    # Check if pass rate meets minimum
                    metadata = criteria.test_result.metadata
                    passed = metadata.get("passed", 0)
                    failed = metadata.get("failed", 0)
                    if failed > 0:
                        pass_rate = passed / (passed + failed)
                        if pass_rate >= self.config.min_test_pass_rate:
                            # Still acceptable
                            pass
                        else:
                            return ApprovalDecision.REJECTED
                else:
                    return ApprovalDecision.REJECTED

        # Check review results
        if self.config.require_review:
            if criteria.review_score > 0:
                if criteria.review_score < self.config.min_review_score:
                    issues.append(f"Review score {criteria.review_score} below threshold {self.config.min_review_score}")
                    return ApprovalDecision.FLAGGED

                if not criteria.review_approved:
                    issues.append("Review did not approve")
                    return ApprovalDecision.FLAGGED

        # All criteria passed - auto-approve
        if self.config.auto_approve:
            return ApprovalDecision.AUTO_APPROVED

        # Auto-approve disabled - flag for human review
        return ApprovalDecision.FLAGGED

    def get_decision_summary(self, decision: ApprovalDecision, criteria: ApprovalCriteria) -> str:
        """Get a human-readable summary of the decision."""
        lines = [f"**Decision: {decision.value.upper()}**"]

        if criteria.test_result:
            status = "✓" if criteria.test_result.success else "✗"
            lines.append(f"{status} Tests: {'PASSED' if criteria.test_result.success else 'FAILED'}")

        if criteria.review_score > 0:
            score_indicator = "✓" if criteria.review_score >= self.config.min_review_score else "✗"
            lines.append(f"{score_indicator} Review: {criteria.review_score}/10")

        if criteria.issues:
            lines.append("\n**Issues:**")
            for issue in criteria.issues:
                lines.append(f"- {issue}")

        if decision == ApprovalDecision.AUTO_APPROVED:
            lines.append("\n✅ *Auto-approved by ApproveGate*")
        elif decision == ApprovalDecision.FLAGGED:
            lines.append("\n👤 *Flagged for human review*")
        elif decision == ApprovalDecision.REJECTED:
            lines.append("\n❌ *Rejected*")

        return "\n".join(lines)


def extract_review_score(result: TaskResult) -> tuple:
    """Extract score and approval status from a review result.

    Returns: (score: float, approved: bool)
    """
    if not result or not result.metadata:
        return 0.0, False

    score = result.metadata.get("score", 0.0)
    approved = result.metadata.get("approved", False)

    return score, approved


def extract_test_pass_rate(result: TaskResult) -> float:
    """Extract pass rate from a test result.

    Returns: 0.0 to 1.0
    """
    if not result or not result.metadata:
        return 0.0

    passed = result.metadata.get("passed", 0)
    failed = result.metadata.get("failed", 0)
    errors = result.metadata.get("errors", 0)

    total = passed + failed + errors
    if total == 0:
        return 0.0

    return passed / total
