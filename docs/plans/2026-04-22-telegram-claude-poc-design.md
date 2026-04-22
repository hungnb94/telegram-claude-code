# Telegram → Claude Code POC Design

## Overview

A system where users send messages via Telegram, which are processed and forwarded to Claude Code CLI running locally on the user's machine, which then works on a local codebase and streams progress back to the user via Telegram.

## Architecture

```
Telegram ←→ Telegram Bot (Python script)
                    ↓
            Claude Code CLI (subprocess)
                    ↓
              Codebase (local)
                    ↓
            Streaming progress → Telegram
```

## Components

1. `telegram_poller.py` - Poll Telegram for messages, send commands to Claude Code, stream output back
2. `claude_subprocess.py` - Wrapper to spawn Claude Code CLI with project context
3. `message_queue.py` - In-memory queue to handle queued tasks
4. `stream_handler.py` - Parse Claude Code output, send chunks to Telegram

## Data Flow

1. User sends message via Telegram
2. Poller receives the message
3. Check queue/worker status
4. Spawn `claude code` subprocess
5. Stream stdout/stderr back to Telegram as it runs
6. Task completes → notify user

## Concurrent Task Handling

```
User sends "task A" → enqueued → returns " queued"
User sends "task B" → enqueued → returns " queued"

Worker picks "task A" → runs Claude Code → streams progress
Task A completes → Worker picks "task B" → runs Claude Code → streams progress
```

State is persisted to `tasks.json` so queue survives script restarts.

## Configuration

```yaml
telegram_bot_token: "your_token_here"
claude_code_project_path: "/path/to/codebase"
poll_interval_seconds: 5
task_timeout_minutes: 30
```

## Claude Code Subprocess Launch

```python
subprocess.run([
    "claude", "code",
    "--dangerously-skip-permissions",
    f"Your task: {task_description}"
], cwd=CODEBASE_PATH, stdout=PIPE, stderr=STDOUT)
```

## Streaming Output to Telegram

- Parse Claude Code output line by line
- Send message to Telegram every ~20-30 lines or on tool call events
- Use `edit_message` to update the running message instead of spamming new messages

## Error Handling

| Scenario | Response |
|----------|----------|
| Claude Code crash mid-task | Restart worker, check if task should be retried or marked failed |
| Invalid task description | Reply "I don't understand, please rephrase" |
| No codebase access | Reply "Cannot access project path" |
| Rate limit (Telegram) | Exponential backoff, queue remaining |
| Claude Code permissions denied | Show specific permission that was denied |

## Recovery

- `tasks.json` is persisted to survive script restarts
- If running task exists when restart occurs → mark as failed, notify user

## Testing Plan

1. **Unit tests:**
   - `test_queue.py` - queue operations (enqueue/dequeue/peek)
   - `test_stream_handler.py` - output parsing

2. **Integration tests:**
   - Manual test: send message from personal Telegram → verify response

3. **Smoke test:**
   - Run script → verify bot responds to "/start" command

## Deliverables

- `telegram_claude_poc.py` (single file, <300 lines)
- `config.yaml`
- `requirements.txt`
- Updated `CLAUDE.md`
