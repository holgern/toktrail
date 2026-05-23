---
schema_version: 2
id: al_block_0019
type: black_box
title: "CLI Layer"
status: proposed
section: building_block_view
level: 1
parent: al_block_0018
order: 10
date: "2026-05-23"
interfaces: []
location: []
fulfilled_requirements: []
risks: []
tags: []
body_format: markdown
created_at: "2026-05-23T05:59:24Z"
updated_at: "2026-05-23T05:59:24Z"
source_refs:
  - toktrail/cli.py
  - path: toktrail/cli_parts/
    reason: "Directory-wide ownership: app, area, config, copilot, filters, formatting, machines, main_cli, prices, refresh, run, sources, statusline, subscriptions, table, types, usage, watch"
---

## Purpose

Typer-based CLI providing commands for run management, import, watch, status, usage
reporting, source inspection, pricing, and machine/area management.

## Interfaces

- `toktrail.cli:cli_main` — console entrypoint
- `toktrail.cli:app` — Typer application instance

## Key commands

- `run start/stop/status/list` — tracking run lifecycle
- `import/watch` — source data ingestion
- `status` — current run summary
- `usage daily/weekly/monthly/sessions/runs` — usage reporting
- `sources list/sessions/session` — source inspection
- `prices list/parse` — pricing management
- `area create/list/use/assign` — area management
- `machine status/list/set-name` — machine identity
- `sync push/pull/status` — git-backed multi-machine sync
- `subscription list` — subscription quota tracking
- `statusline` — shell statusline output
- `tui` — launch Textual TUI

## Source

- `toktrail/cli.py` — facade
- `toktrail/cli_parts/main_cli.py` — implementation (~7800 lines)
- `toktrail/cli_parts/` — extracted CLI sub-modules (18 modules)
- `toktrail/cli_sync.py` — sync CLI commands (~1180 lines)

source_refs:

- toktrail/cli.py
- toktrail/cli_parts/
