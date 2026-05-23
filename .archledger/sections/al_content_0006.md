---
schema_version: 2
id: al_content_0006
type: section
section: runtime_view
title: Runtime View
order: 60
status: accepted
date: "2026-05-22"
body_format: markdown
created_at: "2026-05-22T20:27:22Z"
updated_at: "2026-05-22T20:27:22Z"
---

### Canonical workflow

```
init -> run start -> import/watch -> status -> run stop
```

### Import flow

1. User invokes `toktrail import --harness <name>` or `toktrail watch`.
2. CLI resolves the active run from SQLite (or uses `--run`).
3. Scanner discovers source files via `HARNESS_REGISTRY` and path resolution.
4. Adapter scans source files, producing `ScanResult` with `UsageEvent` list.
5. DB layer inserts events, using `global_dedup_key` to skip duplicates.
6. Import result: rows seen, imported, skipped.

### Watch flow

Same as import, but runs in a loop with configurable interval.

### Report flow

1. User invokes `toktrail status`, `toktrail usage daily`, etc.
2. DB layer queries aggregated data from `usage_events` joined with `runs`, `source_sessions`.
3. Costing module computes actual and virtual costs from pricing config.
4. Reporting dataclasses shape the response.
5. CLI renders human or JSON output.

See diagram record al_diagram_0038 for the visual import flow.
