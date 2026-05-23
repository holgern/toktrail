---
schema_version: 2
id: al_content_0008
type: section
section: cross_cutting_concepts
title: Cross-cutting Concepts
order: 80
status: accepted
date: "2026-05-22"
body_format: markdown
created_at: "2026-05-22T20:27:22Z"
updated_at: "2026-05-22T20:27:22Z"
---

### Token breakdown model

All token accounting uses the canonical `TokenBreakdown` dataclass (`toktrail/models.py`):

| Field          | Meaning                   |
| -------------- | ------------------------- |
| `input`        | Non-cached prompt tokens  |
| `output`       | Generated response tokens |
| `reasoning`    | Reasoning tokens          |
| `cache_read`   | Cached input reused       |
| `cache_write`  | Input written to cache    |
| `cache_output` | Cached output tokens      |

Computed totals: `total = input + output`, `prompt_total = input + cache_read + cache_write`, `accounting_total` = sum of all six.

### Deduplication

Every `UsageEvent` carries `global_dedup_key` (unique in SQLite) and `source_dedup_key` (traceable to source). Fingerprint hash detects content drift in source records.

### Provider inference

`toktrail/provider_identity.py` infers provider from model ID prefixes (e.g., `claude-*` → `anthropic`, `gpt-*` → `openai`). Conservative: returns `None` for unknown patterns.

### Configuration

TOML-based config in `~/.config/toktrail/config.toml` with layered resolution: defaults → config file → environment variables → CLI flags.

### Machine identity

Each installation generates a machine UUID stored in `machine.toml`. Events carry `origin_machine_id` for multi-machine tracking.

### State synchronization

Multi-machine sync uses git-backed repositories. Local state is committed and pushed to a configured remote;
remote state is pulled and merged via worktree-based conflict-free merge. Archive import/export provides an offline alternative.
See `toktrail/git_sync_parts/core.py` and `toktrail/cli_sync.py`.

### Source file caching

The source file cache (`toktrail/cache.py`) avoids reparsing large event files on repeated imports.
Cache is keyed by harness, path, parser version, and fingerprint. Invalidated on any mismatch.

### Error handling

Public error types in `toktrail/errors.py` provide stable exit codes and error messages for CLI and API consumers.

### Time periods

`toktrail/periods.py` defines time period boundaries (daily, weekly, monthly) used across usage reports,
statusline, and subscription tracking. Timezone-aware via the configured local timezone.

Each installation generates a machine UUID stored in `machine.toml`. Events carry `origin_machine_id` for multi-machine tracking.
