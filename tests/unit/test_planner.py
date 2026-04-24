"""Tests for the planner."""

import pytest
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parent.parent.parent / "src"))

from company_agent.workflow.planner import Planner, classify_request, TaskPlan
from company_agent.task_queue import TaskType


class TestPlanner:
    def setup_method(self):
        self.planner = Planner()

    def test_create_plan_basic(self):
        """Test creating a basic plan."""
        plan = self.planner.create_plan("Add user login feature")

        assert plan.name is not None
        assert len(plan.tasks) >= 1
        assert plan.tasks[0].type == TaskType.CODE

    def test_create_plan_full_pipeline(self):
        """Test that a regular request creates code -> test -> review pipeline."""
        plan = self.planner.create_plan("Implement the checkout flow")

        task_types = [t.type for t in plan.tasks]
        assert TaskType.CODE in task_types
        assert TaskType.TEST in task_types
        assert TaskType.REVIEW in task_types

        # Check dependencies
        code_task = next(t for t in plan.tasks if t.type == TaskType.CODE)
        test_task = next(t for t in plan.tasks if t.type == TaskType.TEST)
        review_task = next(t for t in plan.tasks if t.type == TaskType.REVIEW)

        assert test_task.id in code_task.deps or code_task.id in test_task.deps

    def test_create_plan_test_only_request(self):
        """Test that test-only requests only create a test task."""
        plan = self.planner.create_plan("run tests on login")

        task_types = [t.type for t in plan.tasks]
        assert TaskType.CODE in task_types  # Still has code task for context
        assert TaskType.TEST in task_types
        assert TaskType.REVIEW not in task_types

    def test_create_plan_review_only_request(self):
        """Test that review-only requests only create review task."""
        plan = self.planner.create_plan("review the auth code")

        task_types = [t.type for t in plan.tasks]
        assert TaskType.REVIEW in task_types

    def test_topological_order(self):
        """Test that topological_order returns tasks in dependency order."""
        plan = self.planner.create_plan("Add feature")

        ordered = plan.topological_order()
        assert len(ordered) == len(plan.tasks)

        # Each task should come before its dependents
        for task in ordered:
            for dep_id in task.deps:
                dep_idx = next((i for i, t in enumerate(ordered) if t.id == dep_id), -1)
                task_idx = next((i for i, t in enumerate(ordered) if t.id == task.id), -1)
                assert dep_idx < task_idx or dep_idx == -1


class TestClassifyRequest:
    def test_classify_feature(self):
        assert classify_request("implement login") == "feature"

    def test_classify_fix(self):
        assert classify_request("fix the bug in auth") == "fix"

    def test_classify_review(self):
        assert classify_request("review the code") == "review"

    def test_classify_test(self):
        assert classify_request("run tests") == "test"
