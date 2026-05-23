---
schema_version: 2
id: al_block_0039
type: black_box
title: "Statusline"
status: proposed
section: building_block_view
level: 1
parent: al_block_0018
order: 80
date: "2026-05-23"
interfaces: []
location: []
fulfilled_requirements: []
risks: []
tags: []
body_format: markdown
created_at: "2026-05-23T12:07:40Z"
updated_at: "2026-05-23T12:07:40Z"
---

## Purpose

Shell prompt integration for displaying live token usage, cost burn rate,
cache hit rate, subscription quota, and active run context in the user's
terminal prompt or status bar. Computes compact single-line summaries from
the SQLite state database.

## Interfaces

- `toktrail.statusline:render_statusline_report()` — main entrypoint
- `toktrail.api.statusline` — statusline API facade
- `toktrail.cli_parts.statusline.py` — CLI `statusline` command

## Key types

- `StatuslineReport` — top-level report with context, burn, cache, and quota sections
- `StatuslineContext` — active run, harness, model summary
- `StatuslineBurn` — token/cost burn rate over recent time windows
- `StatuslineCache` — cache hit/miss statistics
- `StatuslineQuota` — subscription quota consumption

## Source

- `toktrail/statusline.py` (~1044 lines) — rendering and data assembly
- `toktrail/api/statusline.py` — statusline API
- `toktrail/cli_parts/statusline.py` — CLI command
