"""Tests for the approval gate."""

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from company_agent.workflow.approve_gate import (
    ApproveGate, ApprovalCriteria, ApprovalDecision,
    extract_review_score, extract_test_pass_rate,
)
from company_agent.agents.base import TaskResult
from company_agent.config import ApproveGateConfig


class TestApproveGate:
    def setup_method(self):
        self.gate = ApproveGate()

    def test_auto_approve_when_all_pass(self):
        """Test auto-approval when tests pass and review approves."""
        criteria = ApprovalCriteria(
            test_result=TaskResult(success=True, metadata={"passed": 10, "failed": 0}),
            review_score=8.0,
            review_approved=True,
        )

        decision = self.gate.evaluate(criteria)
        assert decision == ApprovalDecision.AUTO_APPROVED

    def test_reject_when_tests_fail(self):
        """Test rejection when tests fail."""
        criteria = ApprovalCriteria(
            test_result=TaskResult(success=False, metadata={"passed": 5, "failed": 5}),
        )

        decision = self.gate.evaluate(criteria)
        assert decision == ApprovalDecision.REJECTED

    def test_flag_when_review_score_low(self):
        """Test flagging when review score is below threshold."""
        criteria = ApprovalCriteria(
            test_result=TaskResult(success=True, metadata={"passed": 10, "failed": 0}),
            review_score=5.0,  # Below 7.0 threshold
            review_approved=False,
        )

        decision = self.gate.evaluate(criteria)
        assert decision == ApprovalDecision.FLAGGED

    def test_flag_when_review_approved_false(self):
        """Test flagging when review doesn't approve."""
        criteria = ApprovalCriteria(
            test_result=TaskResult(success=True, metadata={"passed": 10, "failed": 0}),
            review_score=8.0,
            review_approved=False,
        )

        decision = self.gate.evaluate(criteria)
        assert decision == ApprovalDecision.FLAGGED

    def test_critical_issues_reject(self):
        """Test rejection when there are critical issues."""
        criteria = ApprovalCriteria(
            test_result=TaskResult(success=True),
            has_critical_issues=True,
            issues=["Security vulnerability found"],
        )

        decision = self.gate.evaluate(criteria)
        assert decision == ApprovalDecision.REJECTED

    def test_no_test_results_flags(self):
        """Test that missing test results are flagged."""
        gate = ApproveGate(ApproveGateConfig(require_tests=True))
        criteria = ApprovalCriteria(review_score=9.0, review_approved=True)

        decision = gate.evaluate(criteria)
        assert decision == ApprovalDecision.FLAGGED


class TestExtractHelpers:
    def test_extract_review_score(self):
        result = TaskResult(success=True, metadata={"score": 8.5, "approved": True})
        score, approved = extract_review_score(result)
        assert score == 8.5
        assert approved is True

    def test_extract_review_score_default(self):
        result = TaskResult(success=True, metadata={})
        score, approved = extract_review_score(result)
        assert score == 0.0
        assert approved is False

    def test_extract_test_pass_rate(self):
        result = TaskResult(success=True, metadata={"passed": 8, "failed": 2})
        rate = extract_test_pass_rate(result)
        assert rate == 0.8

    def test_extract_test_pass_rate_all_pass(self):
        result = TaskResult(success=True, metadata={"passed": 10, "failed": 0})
        rate = extract_test_pass_rate(result)
        assert rate == 1.0
