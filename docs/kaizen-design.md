# Kaizen Scanner Design

## Overview

KaizenScanner analyzes task history and codebase to generate ranked improvement recommendations organized by category. Implemented as a class in `src/telegram_claude_agent/bot.py`.

## Categories

Each category has:
- `min_results` — minimum recommendations to return for this category
- `signals` — list of signal IDs this category listens to

### Signal Registry

| Signal ID | Category | Description |
|-----------|----------|-------------|
| `high_failure_rate` | reliability | ≥30% tasks failed (of 5+ completed) |
| `task_timeout` | reliability | Task exceeded timeout |
| `queue_stall` | reliability | Queue not progressing (stale running timestamp) |
| `queue_backlog` | performance | >3 pending tasks |
| `slow_tasks` | performance | Long-running tasks detected |
| `code_complexity` | maintainability | File >400 lines |
| `large_file` | maintainability | File >500 lines |
| `missing_tests` | code_quality | No tests directory |
| `missing_types` | code_quality | No type hints in first 50 lines |
| `requested_feature` | feature_requests | User requested via message patterns |
| `repeated_pattern` | feature_requests | User asked same thing multiple times |

## Config Format

```yaml
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
```

## Adding New Signals

1. Add analyzer method `_analyze_<signal_id>()` decorated with `@register_signal("signal_id")`
2. Return list of `{id, title, description, priority, impact, category, action}`
3. Signal auto-runs on every scan; results filtered by category config

## Data Flow

```
scan()
  → runs all registered analyzers (via SIGNAL_REGISTRY)
  → each recommendation tagged with category
  → group by category, apply min_results cap
  → merge all categories, sort by priority/impact
  → save to kaizen_recommendations.json
  → return sorted list
```

## Key Classes

- `KaizenConfig` — default config with category defaults, supports deep-merge from user config
- `KaizenScanner` — main scanner with `scan()`, `should_rescan()`, `get_latest()`
- Signal methods use `@register_signal` decorator to populate `SIGNAL_REGISTRY`

## Files

- `KaizenScanner`, `KaizenConfig`, `SIGNAL_REGISTRY` — in `src/telegram_claude_agent/bot.py`
- `kaizen_recommendations.json` — cached scan results in `data/` (gitignored)