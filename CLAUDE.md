# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

POC: Telegram user sends message → Claude Code CLI runs locally → streams progress back to Telegram.

## Commands

```bash
# Setup
pip install python-telegram-bot pyyaml

# Run the bot
python telegram_claude_poc.py

# Run tests
pytest tests/
```

## Architecture

- `telegram_claude_poc.py` - Main entry point (poller + Claude Code wrapper + queue + stream handler)
- `config.yaml` - Configuration (Telegram token, project path, timeouts)
- `tasks.json` - Persisted task queue state

## Key Design Decisions

- Claude Code runs as subprocess on local machine (same machine as bot)
- Messages are queued in-memory, processed sequentially by a single worker
- Claude Code output is streamed line-by-line back to Telegram via `edit_message`
- Task state persists to `tasks.json` for crash recovery
- Project path is configurable per deployment (not hardcoded)
