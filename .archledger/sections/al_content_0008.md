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
