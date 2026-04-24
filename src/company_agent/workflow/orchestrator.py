"""Workflow orchestrator - executes task plans with dependency management.

The orchestrator is the heart of the workflow system:
1. Takes a TaskPlan from the Planner
2. Creates tasks in the TaskQueue
3. Executes tasks in dependency order
4. Streams output via EventBus
5. Handles retries, failures, and escalation
"""

import asyncio
import logging
from datetime import datetime
from typing import Any, Callable, Coroutine, Dict, List, Optional

from company_agent.agents import AgentRegistry, AgentAdapter
from company_agent.event_bus import Event, EventBus, EventTypes
from company_agent.task_queue import Task, TaskQueue, TaskResult, TaskStatus, Workflow
from company_agent.workflow.approve_gate import (
    ApprovalCriteria,
    ApprovalDecision,
    ApproveGate,
    extract_review_score,
    extract_test_pass_rate,
)
from company_agent.workflow.planner import Planner, TaskPlan
from company_agent.workflow.clarification import ClarificationManager, ClarificationRequested


logger = logging.getLogger(__name__)


class WorkflowOrchestrator:
    """Orchestrates workflow execution.

    Key responsibilities:
    - Manage workflow lifecycle (start, progress, complete, fail)
    - Execute tasks in dependency order
    - Stream output via EventBus
    - Handle retries and failures
    - Call ApproveGate for final decision
    - Publish events for external listeners (e.g., Telegram reporter)
    """

    def __init__(
        self,
        task_queue: TaskQueue,
        event_bus: EventBus,
        agent_registry: AgentRegistry,
        planner: Optional[Planner] = None,
        approve_gate: Optional[ApproveGate] = None,
        clarification_manager: Optional[ClarificationManager] = None,
    ):
        self.task_queue = task_queue
        self.event_bus = event_bus
        self.agent_registry = agent_registry
        self.planner = planner or Planner()
        self.approve_gate = approve_gate or ApproveGate()
        self.clarification_manager = clarification_manager or ClarificationManager(event_bus=event_bus)

        # Current workflow state
        self._current_workflow: Optional[Workflow] = None
        self._current_plan: Optional[TaskPlan] = None
        self._running_tasks: Dict[str, asyncio.Task] = {}

        # Stream callback for external listeners (e.g., Telegram)
        self._stream_callback: Optional[Callable[[str], Coroutine]] = None

    def set_stream_callback(self, callback: Optional[Callable[[str], Coroutine]]):
        """Set a callback for streaming output to external consumers (e.g., Telegram)."""
        self._stream_callback = callback

    async def create_and_execute(
        self,
        request: str,
        context: Optional[Dict[str, Any]] = None,
    ) -> Workflow:
        """Create a workflow from request and execute it.

        This is the main entry point - takes a user request, creates a plan,
        and executes it.

        Args:
            request: The user's request
            context: Additional context

        Returns:
            The completed Workflow
        """
        context = context or {}

        # Create workflow
        workflow = Workflow(
            name=self.planner._extract_name(request),
            context=context,
        )

        # Publish workflow started event
        await self.event_bus.publish(Event(
            type=EventTypes.WORKFLOW_STARTED,
            source="orchestrator",
            payload={
                "workflow_id": workflow.id,
                "request": request,
                "context": context,
            },
        ))

        # Create plan (sync operation)
        plan = self.planner.create_plan(request, {
            **context,
            "workflow_id": workflow.id,
        })

        print(f"[ORCH DEBUG] Plan created: {len(plan.tasks)} tasks, first task type={plan.tasks[0].type if plan.tasks else 'none'}", flush=True)

        # Create tasks in queue
        for task in plan.tasks:
            task.workflow_id = workflow.id
            await self.task_queue.create_task(task)
            workflow.tasks.append(task.id)

        workflow = await self.task_queue.create_workflow(workflow)
        self._current_workflow = workflow
        self._current_plan = plan

        # Execute
        try:
            await self._execute_workflow(workflow, plan)
        except Exception as e:
            logger.error(f"Workflow failed: {e}", exc_info=True)
            workflow.status = TaskStatus.FAILED
            workflow.result = TaskResult(success=False, error=str(e))
            await self.task_queue.update_workflow(workflow)

            await self.event_bus.publish(Event(
                type=EventTypes.WORKFLOW_FAILED,
                source="orchestrator",
                payload={
                    "workflow_id": workflow.id,
                    "error": str(e),
                },
            ))

        return workflow

    async def _execute_workflow(self, workflow: Workflow, plan: TaskPlan):
        """Execute a workflow's tasks in dependency order."""
        workflow.status = TaskStatus.RUNNING
        await self.task_queue.update_workflow(workflow)

        # Get tasks in topological order
        ordered_tasks = plan.topological_order()
        print(f"[ORCH DEBUG] Ordered tasks: {[t.id[:8] for t in ordered_tasks]}, types: {[t.type.value for t in ordered_tasks]}", flush=True)

        # Track task results for review task
        task_results: Dict[str, TaskResult] = {}

        for task in ordered_tasks:
            print(f"[ORCH DEBUG] Processing task {task.id[:8]}, type={task.type.value}, deps={task.deps}", flush=True)

            # Wait for dependencies
            deps_met = False
            while not deps_met:
                await asyncio.sleep(0.1)
                task_obj = await self.task_queue.get_task(task.id)
                if task_obj:
                    deps_completed = all(
                        tid in task_results for tid in task_obj.deps
                    )
                    print(f"[ORCH DEBUG]   Task {task.id[:8]} waiting deps: deps={task_obj.deps}, completed={ {tid: tid in task_results for tid in task_obj.deps} }", flush=True)
                    if deps_completed:
                        deps_met = True
                    elif any(
                        tid in task_results and not task_results[tid].success
                        for tid in task_obj.deps
                    ):
                        # A dependency failed, skip this task
                        task.status = TaskStatus.SKIPPED
                        await self.task_queue.update_task(task)
                        break
                else:
                    print(f"[ORCH DEBUG]   Task {task.id[:8]} not found in queue!", flush=True)
                    break

            if task.status == TaskStatus.SKIPPED:
                continue

            # Execute the task
            result = await self._execute_task(task)
            task_results[task.id] = result

            if not result.success:
                # Task failed - check if we should retry
                task_obj = await self.task_queue.get_task(task.id)
                if task_obj and task_obj.retry_count < task_obj.max_retries:
                    # Retry once
                    task_obj.status = TaskStatus.PENDING
                    task_obj.retry_count += 1
                    await self.task_queue.update_task(task_obj)

                    # Re-execute
                    result = await self._execute_task(task)
                    task_results[task.id] = result

                if not result.success:
                    # Continue with other tasks but mark this failed
                    workflow.status = TaskStatus.FAILED

        # Aggregate outputs from all tasks
        all_outputs = []
        for tid, result in task_results.items():
            task = next((t for t in ordered_tasks if t.id == tid), None)
            if task and result.output:
                all_outputs.append(f"[{task.type.value.upper()}] {result.output[:500]}")

        # Determine approval decision
        decision = await self._make_approval_decision(task_results)

        # Update workflow status
        if workflow.status != TaskStatus.FAILED:
            if decision == ApprovalDecision.AUTO_APPROVED:
                workflow.status = TaskStatus.COMPLETED
            elif decision == ApprovalDecision.FLAGGED:
                workflow.status = TaskStatus.PENDING  # Awaiting human review
            else:
                workflow.status = TaskStatus.FAILED

        workflow.completed_at = datetime.utcnow().timestamp()
        workflow.result = TaskResult(
            success=workflow.status == TaskStatus.COMPLETED,
            output="\n\n".join(all_outputs) if all_outputs else None,
        )
        await self.task_queue.update_workflow(workflow)

        # Publish completion event
        await self.event_bus.publish(Event(
            type=EventTypes.WORKFLOW_COMPLETED if workflow.status == TaskStatus.COMPLETED else EventTypes.WORKFLOW_FAILED,
            source="orchestrator",
            payload={
                "workflow_id": workflow.id,
                "status": workflow.status.value,
                "decision": decision.value if decision else None,
                "task_results": {
                    tid: {"success": r.success, "output": r.output[:200]}
                    for tid, r in task_results.items()
                },
            },
        ))

        # Publish approval events
        if decision == ApprovalDecision.AUTO_APPROVED:
            await self.event_bus.publish(Event(
                type=EventTypes.WORKFLOW_APPROVED,
                source="approve_gate",
                payload={"workflow_id": workflow.id},
            ))
        elif decision in (ApprovalDecision.REJECTED, ApprovalDecision.FLAGGED):
            await self.event_bus.publish(Event(
                type=EventTypes.WORKFLOW_REJECTED,
                source="approve_gate",
                payload={
                    "workflow_id": workflow.id,
                    "decision": decision.value,
                },
            ))

    async def _execute_task(self, task: Task) -> TaskResult:
        """Execute a single task with clarification support."""
        # Publish task started event
        await self.event_bus.publish(Event(
            type=EventTypes.TASK_STARTED,
            source="orchestrator",
            payload={
                "task_id": task.id,
                "workflow_id": task.workflow_id,
                "agent": task.agent,
                "type": task.type.value,
            },
        ))

        # Mark as running
        task.status = TaskStatus.RUNNING
        task.started_at = datetime.utcnow().timestamp()
        await self.task_queue.update_task(task)

        # Get the adapter
        adapter = self.agent_registry.get(task.agent)
        if not adapter:
            error_result = TaskResult(
                success=False,
                error=f"No adapter found for agent: {task.agent}",
            )
            await self._complete_task(task, error_result)
            return error_result

        # Execute with clarification support
        try:
            print(f"[ORCH DEBUG] Executing task {task.id} with agent={task.agent}, type={task.type}", flush=True)
            if adapter.supports_streaming() and self._stream_callback:
                result = await adapter.execute_streaming(task, self._stream_callback)
            else:
                result = await adapter.execute(task)
            print(f"[ORCH DEBUG] Task {task.id} result: success={result.success}, output_len={len(result.output) if result.output else 0}, error={result.error}", flush=True)
        except ClarificationRequested as e:
            # Agent needs user input - pause and ask
            result = await self._handle_clarification(task, e, adapter)

        await self._complete_task(task, result)
        return result

    async def _handle_clarification(
        self,
        task: Task,
        e: ClarificationRequested,
        adapter: AgentAdapter,
    ) -> TaskResult:
        """Handle a clarification request from an agent."""
        # Create clarification record
        clar = self.clarification_manager.create(
            question=e.question,
            options=e.options,
            task_id=task.id,
            asker=task.agent,
            clarification_type=e.clarification_type,
            default=e.default,
            context=e.context,
            timeout_seconds=e.timeout_seconds,
        )

        # Publish event so TelegramReporter can ask the user
        await self.event_bus.publish(Event(
            type=EventTypes.CLARIFICATION_ASKED,
            source="orchestrator",
            payload={
                "clarification_id": clar.id,
                "task_id": task.id,
                "workflow_id": task.workflow_id,
                "question": e.question,
                "options": e.options,
                "clarification_type": e.clarification_type.value,
                "asker": task.agent,
            },
        ))

        # Wait for user answer
        answer = await self.clarification_manager.wait_for_answer(clar.id, timeout=e.timeout_seconds)

        if answer is None:
            # Timeout - use default if available
            if e.default:
                answer = e.default
            else:
                return TaskResult(
                    success=False,
                    error=f"Clarification timeout: {e.question}",
                    metadata={"clarification_id": clar.id},
                )

        # Publish answer event
        await self.event_bus.publish(Event(
            type=EventTypes.CLARIFICATION_ANSWERED,
            source="orchestrator",
            payload={
                "clarification_id": clar.id,
                "answer": answer,
            },
        ))

        # Resume task with the answer - store in task payload
        task.payload["clarification_answer"] = answer
        task.payload["clarification_id"] = clar.id
        try:
            result = await adapter.execute(task)
        except Exception as ex:
            return TaskResult(success=False, error=str(ex))

        return result

    async def _complete_task(self, task: Task, result: TaskResult):
        """Mark a task as completed and publish events."""
        if result.success:
            task.status = TaskStatus.COMPLETED
        else:
            task.status = TaskStatus.FAILED

        task.result = result
        task.completed_at = datetime.utcnow().timestamp()
        task.updated_at = task.completed_at
        await self.task_queue.update_task(task)

        # Publish task output event
        await self.event_bus.publish(Event(
            type=EventTypes.TASK_COMPLETED if result.success else EventTypes.TASK_FAILED,
            source="orchestrator",
            payload={
                "task_id": task.id,
                "workflow_id": task.workflow_id,
                "success": result.success,
                "output": result.output[:500] if result.output else None,
                "error": result.error,
            },
        ))

        # Also publish output for streaming consumers
        if result.output and self._stream_callback:
            await self._stream_callback(f"\n--- {task.type.value.upper()} OUTPUT ---\n{result.output[:1000]}")

    async def _make_approval_decision(self, task_results: Dict[str, TaskResult]) -> ApprovalDecision:
        """Make approval decision based on task results."""
        criteria = ApprovalCriteria()

        # Find test and review results
        tasks = await self.task_queue.get_tasks(workflow_id=self._current_workflow.id)

        for task in tasks:
            result = task_results.get(task.id)
            if not result:
                continue

            if task.type.value == "test":
                criteria.test_result = result
            elif task.type.value == "review":
                criteria.review_score, criteria.review_approved = extract_review_score(result)
                # Check for critical issues in review output
                if not result.success or criteria.review_score < 5.0:
                    criteria.has_critical_issues = True
                    if result.output:
                        criteria.issues.append(result.output[:200])

        return self.approve_gate.evaluate(criteria)

    async def get_workflow_status(self, workflow_id: str) -> Optional[Dict[str, Any]]:
        """Get current status of a workflow."""
        workflow = await self.task_queue.get_workflow(workflow_id)
        if not workflow:
            return None

        tasks = await self.task_queue.get_tasks(workflow_id=workflow_id)

        return {
            "workflow": workflow,
            "tasks": tasks,
            "stats": await self.task_queue.get_task_stats(),
        }

    async def cancel_workflow(self, workflow_id: str) -> bool:
        """Cancel a running workflow."""
        workflow = await self.task_queue.get_workflow(workflow_id)
        if not workflow:
            return False

        if workflow.status not in (TaskStatus.PENDING, TaskStatus.RUNNING):
            return False

        # Cancel all pending tasks
        tasks = await self.task_queue.get_tasks(workflow_id=workflow_id)
        for task in tasks:
            if task.status in (TaskStatus.PENDING, TaskStatus.WAITING):
                task.status = TaskStatus.CANCELLED
                await self.task_queue.update_task(task)

        workflow.status = TaskStatus.CANCELLED
        await self.task_queue.update_workflow(workflow)

        await self.event_bus.publish(Event(
            type=EventTypes.WORKFLOW_FAILED,
            source="orchestrator",
            payload={
                "workflow_id": workflow_id,
                "reason": "cancelled",
            },
        ))

        return True
