---
schema_version: 2
id: al_content_0004
type: section
section: solution_strategy
title: Solution Strategy
order: 40
status: accepted
date: "2026-05-22"
body_format: markdown
created_at: "2026-05-22T20:27:22Z"
updated_at: "2026-05-22T20:27:22Z"
---

1. **Per-harness adapters** normalize heterogeneous source formats into a canonical `UsageEvent` model. Each adapter implements `scan()`, `parse()`, and `list_sessions()` against a shared protocol (`toktrail/adapters/base.py:HarnessAdapter`).

2. **Central SQLite ledger** stores normalized events with stable dedup keys (`global_dedup_key` uniqueness). Schema version is 15 with explicit migrations in `toktrail/_db/core_db.py`.

3. **Idempotent import loop**: `import` and `watch` commands discover sources, scan for events, insert new rows, and skip duplicates. File-level state tracking enables incremental resume for JSONL sources.

4. **Configurable costing**: user-defined pricing in TOML (`prices.toml`) drives actual and virtual cost computation via `toktrail/costing.py`. No external pricing lookups.

5. **Stable CLI and API facades**: `toktrail/cli.py`, `toktrail/db.py`, `toktrail/config.py`, and `toktrail/api/models.py` are stable entry points; implementation lives in `cli_parts/`, `_db/`, `config_parts/`, and `api/model_parts/`.

6. **Optional TUI**: Textual-based dashboard (`toktrail/tui/`) for interactive browsing, gated behind `[tui]` optional dependency.
