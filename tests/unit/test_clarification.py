
import pytest, asyncio
from company_agent.workflow.clarification import ClarificationManager, Clarification, ClarificationType

class TestClarificationManager:
    def test_create_clarification(self):
        cm = ClarificationManager()
        q = cm.create(
            question="Which database?",
            options=["PostgreSQL", "SQLite"],
            task_id="task-1",
            asker="claude"
        )
        assert q.id is not None
        assert q.question == "Which database?"
        assert q.options == ["PostgreSQL", "SQLite"]
        assert q.status == "pending"
    
    def test_answer_clarification(self):
        cm = ClarificationManager()
        q = cm.create("Pick one", ["A", "B"], "t1", "claude")
        result = asyncio.run(cm.answer(q.id, "A", "user"))
        assert result is True
        assert q.answer == "A"
        assert q.answered_by == "user"
        assert q.status == "answered"
    
    def test_wait_for_answer(self):
        cm = ClarificationManager()
        q = cm.create("Which?", ["X", "Y"], "t1", "claude")
        async def get_answer():
            return await cm.wait_for_answer(q.id, timeout=1.0)
        # Should timeout since no answer comes
        result = asyncio.run(get_answer())
        assert result is None  # timeout
    
    def test_pending_clarifications(self):
        cm = ClarificationManager()
        cm.create("Q1", None, "t1", "a")
        cm.create("Q2", None, "t2", "a")
        cm.create("Q3", None, "t3", "b")
        pending = cm.get_pending()
        assert len(pending) == 3
        pending_for_t1 = cm.get_pending_for_task("t1")
        assert len(pending_for_t1) == 1
