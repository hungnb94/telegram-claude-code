# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

POC: Telegram user sends message → Claude Code CLI runs locally → streams progress back to Telegram.

## Commands

```bash
# Setup
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# Run the bot
python telegram_claude_poc.py

# Run tests
pytest -m unit tests/           # unit tests only (90%)
pytest -m integration tests/   # integration tests only (10%)
pytest tests/                   # all tests
```

## TDD Workflow

Every task follows Test-Driven Development:

1. **Red** — Write a failing unit test in `tests/unit/test_*.py`
2. **Green** — Write minimal code to make the test pass
3. **Refactor** — Improve code structure without breaking tests

### Test Organization
- `tests/unit/` — Fast, isolated unit tests (90%)
- `tests/integration/` — Slower integration tests with mocked external systems (10%)
- All unit test classes must be marked with `@pytest.mark.unit`
- All integration test classes must be marked with `@pytest.mark.integration`

### Test Commands
```bash
pytest -m unit tests/           # unit only (fast)
pytest -m integration tests/    # integration only (slow)
```

## Configuration

**Environment variables (required):**
```bash
export TELEGRAM_BOT_TOKEN="your_bot_token_here"
export CLAUDE_CODE_PROJECT_PATH="/path/to/your/codebase"
```

Or create `config.yaml` (copy from `config.yaml.example`):
```yaml
telegram_bot_token: "your_bot_token_here"
claude_code_project_path: "/path/to/your/codebase"
poll_interval_seconds: 5
task_timeout_minutes: 30
```

## Architecture

- `telegram_claude_poc.py` - Main entry point (poller + Claude Code wrapper + queue + stream handler)
- `config.yaml.example` - Configuration template
- `tasks.json` - Persisted task queue state (gitignored)

## Key Design Decisions

- Claude Code runs as subprocess on local machine (same machine as bot)
- Messages are queued in-memory, processed sequentially by a single worker
- Claude Code output is streamed line-by-line back to Telegram via `edit_message`
- Task state persists to `tasks.json` for crash recovery
- Project path is configurable per deployment (not hardcoded)
