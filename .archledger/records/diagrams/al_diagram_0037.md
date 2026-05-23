---
schema_version: 2
id: al_diagram_0037
type: diagram
title: "Building block decomposition"
status: proposed
section: building_block_view
order: 20
date: "2026-05-23"
diagram_type: "text"
caption: "Building block decomposition"

related_records: []

tags: []
body_format: markdown
created_at: "2026-05-23T05:59:51Z"
updated_at: "2026-05-23T05:59:51Z"
---

```textdiagram
toktrail
├── CLI Layer (al_block_0019)
│   ├── toktrail/cli_parts/main_cli.py     — commands, import/watch flows
│   ├── toktrail/cli_parts/watch.py        — watch loop
│   ├── toktrail/cli_parts/run.py          — run lifecycle commands
│   ├── toktrail/cli_parts/area.py         — area management commands
│   ├── toktrail/cli_parts/machines.py     — machine identity commands
│   ├── toktrail/cli_parts/sources.py      — source inspection commands
│   ├── toktrail/cli_parts/prices.py       — pricing commands
│   ├── toktrail/cli_parts/usage.py        — usage reporting commands
│   ├── toktrail/cli_parts/subscriptions.py — subscription commands
│   ├── toktrail/cli_parts/statusline.py   — statusline command
│   ├── toktrail/cli_parts/config.py       — config commands
│   ├── toktrail/cli_parts/filters.py      — shared CLI filters
│   ├── toktrail/cli_parts/formatting.py   — human output
│   ├── toktrail/cli_parts/table.py        — table rendering
│   └── toktrail/cli_parts/types.py        — shared CLI types
│
├── API Facade (al_block_0025)
│   ├── toktrail/api/models.py             — stable API model facade
│   ├── toktrail/api/model_parts/          — internal model implementations
│   ├── toktrail/api/workflow.py           — run lifecycle
│   ├── toktrail/api/imports.py            — import operations
│   ├── toktrail/api/reports.py            — report generation
│   ├── toktrail/api/sessions.py           — session queries
│   ├── toktrail/api/sources.py            — source discovery
│   ├── toktrail/api/prices.py             — pricing management
│   ├── toktrail/api/events.py             — event queries
│   ├── toktrail/api/areas.py              — area management
│   ├── toktrail/api/analysis.py           — cache analysis API
│   ├── toktrail/api/sync.py               — state sync API
│   ├── toktrail/api/machines.py           — machine identity API
│   ├── toktrail/api/statusline.py         — statusline API
│   ├── toktrail/api/config.py             — config API
│   ├── toktrail/api/paths.py              — path API
│   └── toktrail/api/_conversions.py       — shared type conversions
│
├── Adapter Layer (al_block_0020)
│   ├── toktrail/adapters/base.py          — HarnessAdapter protocol
│   ├── toktrail/adapters/registry.py      — HARNESS_REGISTRY
│   └── toktrail/adapters/<name>.py        — per-harness parsers (11 harnesses)
│
├── Database Layer (al_block_0021)
│   ├── toktrail/_db/core_db.py            — schema, CRUD, aggregation
│   ├── toktrail/_db/schema.py             — schema definitions
│   ├── toktrail/_db/migrations.py         — schema migrations
│   ├── toktrail/_db/connection.py         — connection management
│   ├── toktrail/_db/usage_events.py       — event insertion
│   ├── toktrail/_db/runs.py               — run CRUD
│   ├── toktrail/_db/areas.py              — area CRUD
│   ├── toktrail/_db/source_sessions.py    — source session queries
│   ├── toktrail/_db/reports_usage.py      — usage report queries
│   ├── toktrail/_db/reports_sessions.py   — session report queries
│   ├── toktrail/_db/reports_runs.py       — run report queries
│   ├── toktrail/_db/reports_areas.py      — area report queries
│   ├── toktrail/_db/reports_series.py     — time series queries
│   └── toktrail/_db/reports_subscriptions.py — subscription queries
│
├── Reporting & Costing (al_block_0022)
│   ├── toktrail/reporting.py              — report dataclasses
│   ├── toktrail/costing.py                — cost computation
│   ├── toktrail/price_parser.py           — pricing TOML parsing
│   ├── toktrail/periods.py                — time period definitions
│   └── toktrail/config_parts/             — config parsing
│
├── Scanner & Discovery (al_block_0023)
│   ├── toktrail/scanner.py                — source discovery
│   └── toktrail/paths.py                  — path resolution
│
├── Statusline (al_block_0039)
│   ├── toktrail/statusline.py             — statusline rendering (~1044 lines)
│   └── toktrail/api/statusline.py         — statusline API
│
├── Source Cache & Analysis (al_block_0040)
│   ├── toktrail/cache.py                  — source file cache
│   └── toktrail/analysis.py               — cache efficiency analysis
│
├── Sync & State Sharing (al_block_0041)
│   ├── toktrail/cli_sync.py               — sync CLI commands (~1180 lines)
│   ├── toktrail/git_sync_parts/core.py    — git operations
│   └── toktrail/sync_parts/core.py        — sync compatibility layer
│
└── TUI (al_block_0024)  [optional]
    ├── toktrail/tui/app.py                — Textual app
    ├── toktrail/tui/panes/                — UI panes (dashboard, sessions, areas, prices, subscriptions, config)
    └── toktrail/tui/screens/              — forms (area, price, confirm)
```
