# Kaizen Scanner Design

## Overview

KaizenScanner analyzes task history and codebase to generate ranked improvement recommendations organized by category.

## Categories

Each category has:
- `max_results` — maximum recommendations to return for this category
- `signals` — list of signal IDs this category listens to

### Signal Registry

| Signal ID | Category | Description |
|-----------|----------|-------------|
| `high_failure_rate` | reliability | ≥30% tasks failed (of 5+ completed) |
| `task_timeout` | reliability | Task exceeded timeout |
| `queue_stall` | reliability | Queue not progressing |
| `queue_backlog` | performance | >3 pending tasks |
| `slow_tasks` | performance | Long-running tasks detected |
| `memory_leak` | performance | Memory patterns detected |
| `code_complexity` | maintainability | File >400 lines |
| `large_file` | maintainability | File >500 lines |
| `code_duplication` | maintainability | Repeated code patterns |
| `missing_tests` | code_quality | No tests/ directory |
| `missing_types` | code_quality | No type hints in first 50 lines |
| `lint_issues` | code_quality | Linter warnings |
| `requested_feature` | feature_requests | User requested via message patterns |
| `repeated_pattern` | feature_requests | User asked same thing multiple times |

## Config Format

```yaml
kaizen:
  scan_interval_hours: 6
  categories:
    reliability:
      max_results: 2
      signals:
        - high_failure_rate
        - task_timeout
        - queue_stall
    performance:
      max_results: 2
      signals:
        - queue_backlog
        - slow_tasks
    maintainability:
      max_results: 2
      signals:
        - code_complexity
        - large_file
    code_quality:
      max_results: 2
      signals:
        - missing_tests
        - missing_types
    feature_requests:
      max_results: 2
      signals:
        - requested_feature
        - repeated_pattern
```

## Adding New Signals

1. Add analyzer method `_analyze_<signal_id>()` returning list of `{id, title, description, priority, impact, category, action}`
2. Register in `SIGNAL_REGISTRY` dict mapping signal_id → method
3. Signal auto-runs on every scan; results filtered by category config

## Data Flow

```
scan()
  → runs all registered analyzers
  → each recommendation tagged with category
  → group by category, apply max_results cap
  → merge all categories, sort by priority/impact
  → save to kaizen_recommendations.json
  → return sorted list
```

## Files

- `KaizenScanner` class in `telegram_claude_poc.py`
- `SIGNAL_REGISTRY` — signal ID to analyzer method mapping
- `kaizen_recommendations.json` — cached scan results (gitignored)
