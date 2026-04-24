# AI Company Agent - MVP Specification

## Overview
Multi-agent AI system that simulates a software company: receives requests, codes, tests, reviews, and approves changes automatically.

## Architecture

```
Telegram → RequestHandler → EventBus → TaskQueue
                                       ↓
                          ┌────────────┼────────────┐
                          ↓            ↓            ↓
                     CodeAgent    TestAgent   ReviewAgent
                          ↓            ↓            ↓
                          └────────────┼────────────┘
                                       ↓
                                  ApproveGate
                                       ↓
                                  TelegramReport
```

## Implementation Status: MVP BUILT ✓

### Core Components

| Component | File | Status |
|-----------|------|--------|
| EventBus | `src/company_agent/event_bus.py` | ✅ pub/sub, pattern matching |
| TaskQueue | `src/company_agent/task_queue.py` | ✅ JSON-persisted, deps, async |
| AgentAdapter (base) | `src/company_agent/agents/base.py` | ✅ Abstract + registry |
| ClaudeAdapter | `src/company_agent/agents/claude_adapter.py` | ✅ async, streaming via PTY |
| PytestAdapter | `src/company_agent/agents/pytest_adapter.py` | ✅ async, streaming |
| KiloCodeAdapter | `src/company_agent/agents/kilocode_adapter.py` | ✅ review with scoring |
| Planner | `src/company_agent/workflow/planner.py` | ✅ request → task graph |
| ApproveGate | `src/company_agent/workflow/approve_gate.py` | ✅ auto-approve rules |
| Orchestrator | `src/company_agent/workflow/orchestrator.py` | ✅ executes full pipeline |
| TelegramReporter | `src/company_agent/reporters/telegram_reporter.py` | ✅ event → Telegram |

### Test Coverage: 55 tests passing

## Event Bus Design

Events use dot-separated type namespace inspired by OpenAgents ONM:
- `workflow.started`, `workflow.task.created`, `workflow.task.completed`
- `task.started`, `task.completed`, `task.failed`, `task.output`
- Pattern matching via fnmatch (`workflow.task.*`)

## Task Queue

Tasks have: `id`, `type`, `agent`, `status`, `deps`, `payload`, `result`
- States: `pending`, `waiting`, `running`, `completed`, `failed`, `cancelled`, `skipped`
- Dependency-based execution (task waits for deps)

## Agent Adapters

```python
class AgentAdapter(ABC):
    @property
    def supported_types(self) -> list[str]: ...
    async def execute(self, task: Task) -> TaskResult: ...
    def supports_streaming(self) -> bool: ...
    async def execute_streaming(self, task: Task, callback): ...
```

## Workflow Pipeline

```
Request → [Planner] → Plan (tasks + deps)
                   ↓
         [CodeAgent] → writes code
                   ↓
         [TestAgent] → runs tests
                   ↓
         [ReviewAgent] → code review
                   ↓
         [ApproveGate] → auto-approve if rules pass, else human review
                   ↓
         [TelegramReport] → send results to user
```

## Approval Rules

- **Auto-approve**: test passed AND review score >= 7/10
- **Flag**: tests pass but review score < 7
- **Reject**: tests fail OR critical issues found

## File Structure

```
src/company_agent/
├── __init__.py
├── config.py
├── event_bus.py
├── task_queue.py
├── main.py                    # Entry point
├── agents/
│   ├── __init__.py
│   ├── base.py
│   ├── claude_adapter.py
│   ├── kilocode_adapter.py
│   └── pytest_adapter.py
├── workflow/
│   ├── __init__.py
│   ├── planner.py
│   ├── orchestrator.py
│   └── approve_gate.py
├── handlers/
│   ├── __init__.py
│   └── telegram_handler.py
└── reporters/
    ├── __init__.py
    └── telegram_reporter.py
```
