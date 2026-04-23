# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Telegram bot that queues Claude Code tasks, executes them locally, streams progress back to Telegram, and runs KiloCode post-task reviews with retry logic.

## Commands

```bash
# Setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run the bot
PYTHONPATH=src python -m poc_dev_flow_agent.bot

# Run tests
PYTHONPATH=src pytest -m unit tests/           # unit tests only (90%)
PYTHONPATH=src pytest -m integration tests/   # integration tests only (10%)
PYTHONPATH=src pytest tests/                   # all tests
```

## TDD Workflow

Every task follows Test-Driven Development:

1. **Red** — Write a failing unit test in `tests/unit/test_*.py`
2. **Green** — Write minimal code to make the test pass
3. **Refactor** — Improve code structure without breaking tests

### Test Organization
- `tests/` — Test suite root
  - `test_bot.py` — Core component tests (TaskQueue, StreamHandler, KaizenScanner, Bot)
  - `unit/` — Fast, isolated unit tests (90%)
  - `integration/` — Slower integration tests with mocked external systems (10%)
- All unit test classes must be marked with `@pytest.mark.unit`
- All integration test classes must be marked with `@pytest.mark.integration`

### Test Commands
```bash
pytest -m unit tests/           # unit only (fast)
pytest -m integration tests/    # integration only (slow)
```

## Configuration

**Environment variables:**
```bash
export TELEGRAM_BOT_TOKEN="your_bot_token_here"
export CLAUDE_CODE_PROJECT_PATH="/path/to/your/codebase"
export ALLOWED_TELEGRAM_USERNAMES="user1,user2"  # comma-separated, no @
export KILOCODE_REVIEW_ENABLED="true"             # enable/disable post-task review
export KILOCODE_REVIEW_MAX_RETRIES="3"
export KILOCODE_REVIEW_TIMEOUT="300"
```

Or create `config.yaml` (copy from `config.yaml.example`):
```yaml
telegram_bot_token: "your_bot_token_here"
claude_code_project_path: "/path/to/your/codebase"
poll_interval_seconds: 5
task_timeout_minutes: 30

# Usernames (without @) allowed to execute tasks. Empty list = allow anyone.
allowed_telegram_usernames: []

# Kaizen scanner settings
kaizen:
  scan_interval_hours: 6
  categories:
    reliability:
      min_results: 2
      signals:
        - high_failure_rate
        - task_timeout
        - queue_stall
    performance:
      min_results: 2
      signals:
        - queue_backlog
        - slow_tasks
    maintainability:
      min_results: 2
      signals:
        - code_complexity
        - large_file
    code_quality:
      min_results: 2
      signals:
        - missing_tests
        - missing_types
    feature_requests:
      min_results: 2
      signals:
        - requested_feature
        - repeated_pattern

# KiloCode review settings (runs after Claude Code completes a task)
review:
  enabled: true
  max_retries: 3
  timeout_seconds: 300
  pass_keywords:
    - approved
    - lgtm
    - looks good
    - no issues
    - pass
  fail_keywords:
    - issue
    - problem
    - warning
    - error
    - bug
    - security
```

## Project Structure

```
poc-dev-flow-agent/
├── src/poc_dev_flow_agent/   # Source package
│   ├── __init__.py           # Package init
│   ├── bot.py                # Main entry: Bot, KaizenScanner, KiloCodeReviewer, load_config
│   ├── task_queue.py         # Task queue with JSON persistence
│   ├── claude_subprocess.py  # Claude Code subprocess wrapper
│   └── stream_handler.py     # Output streaming handler
├── data/                     # Runtime data (gitignored)
│   ├── tasks.json            # Persisted task queue state
│   └── kaizen_recommendations.json  # Kaizen scan results
├── tests/                    # Test suite
│   ├── conftest.py           # Shared pytest fixtures
│   ├── test_bot.py           # Core component tests
│   ├── unit/                 # Fast, isolated unit tests
│   │   ├── __init__.py
│   │   ├── test_stream_handler.py
│   │   └── test_task_queue.py
│   └── integration/          # Slower integration tests
│       ├── __init__.py
│       └── test_subprocess_flow.py
├── docs/                     # Design documents
│   └── kaizen-design.md      # Kaizen scanner architecture
├── config.yaml.example       # Configuration template
├── requirements.txt
└── CLAUDE.md                 # This file
```

## Architecture

### Core Components

**Bot** (`bot.py`)
- Telegram bot with polling loop and command handlers (`/start`, `/kaizen`)
- Async task execution with semaphore (single concurrent task)
- Real-time streaming via `edit_message` (chunks of 30 lines)
- Session recovery: clears stale `running` state on startup
- Bot command menu configured via `set_my_commands`

**KaizenScanner** (`bot.py`)
- Signal-based analyzer registry (`SIGNAL_REGISTRY`)
- 12 signals across 5 categories: reliability, performance, maintainability, code_quality, feature_requests
- Periodic background scan every 6 hours (configurable)
- `/kaizen` command shows ranked recommendation cards
- User can reply with number to execute a recommendation as a task

**KiloCodeReviewer** (`bot.py`)
- Runs after successful Claude Code task
- Retry loop with configurable max retries
- Pass/fail evaluated via keyword matching + exit code
- Review output sent in chunks to Telegram

**TaskQueue** (`task_queue.py`)
- JSON file persistence to `data/tasks.json`
- Three states: `pending`, `running`, `completed`
- Dirty-flag caching for performance

**ClaudeSubprocess** (`claude_subprocess.py`)
- Spawns `claude --print --dangerously-skip-permissions --no-session-persistence`
- Line-by-line streaming via callback
- Configurable timeout

**StreamHandler** (`stream_handler.py`)
- Buffers lines, yields chunks of N lines
- Used for batched `edit_message` calls

### Data Flow

```
Telegram message
  → authorization check
  → if running task: enqueue
  → else: set_running + _execute_task async

_execute_task:
  → send initial streaming message
  → run ClaudeSubprocess with callbacks
  → stream output via edit_message (30-line chunks)
  → queue.complete
  → if success + KiloCode enabled:
      → review loop (retry up to max_retries)
      → retry Claude with full review feedback if failed
  → _process_next (pick up pending task)
```

## Bot Commands

| Command | Description |
|---------|-------------|
| `/start` | Show welcome message |
| `/kaizen` | Scan for improvement recommendations |
| `/help` | Show help (configured, not implemented) |
| `/status` | Show queue status (configured, not implemented) |

## Signal Reference

| Signal ID | Category | Trigger |
|-----------|----------|---------|
| `high_failure_rate` | reliability | ≥30% tasks failed (5+ completed) |
| `task_timeout` | reliability | Task exceeded timeout |
| `queue_stall` | reliability | Running task stale |
| `queue_backlog` | performance | >3 pending tasks |
| `slow_tasks` | performance | Long-running task detected |
| `code_complexity` | maintainability | File >400 lines |
| `large_file` | maintainability | File >500 lines |
| `missing_tests` | code_quality | No tests directory |
| `missing_types` | code_quality | No type hints in first 50 lines |
| `requested_feature` | feature_requests | User requested via message patterns |
| `repeated_pattern` | feature_requests | User asked same thing multiple times |