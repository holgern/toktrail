---
schema_version: 2
id: al_block_0018
type: white_box
title: "Overall System"
status: proposed
section: building_block_view
level: 1
parent: null
order: 10
date: "2026-05-23"
diagram: null
quality_characteristics: []
tags: []
body_format: markdown
created_at: "2026-05-23T05:59:17Z"
updated_at: "2026-05-23T05:59:17Z"
source_refs:
  - path: toktrail/
    reason: "Directory-wide ownership"
  - pyproject.toml
---

## Purpose

toktrail is a single-process Python application that reads token usage data from
multiple AI coding harness sources, normalizes it into a canonical model, stores it
in a local SQLite database, and provides CLI and TUI interfaces for reporting.

## Components

- **al_block_0019 CLI Layer** — Typer-based command interface
- **al_block_0020 Adapter Layer** — Harness-specific parsers
- **al_block_0021 Database Layer** — SQLite state management
- **al_block_0022 Reporting & Costing** — Aggregation and cost computation
- **al_block_0023 Scanner & Discovery** — Source file discovery
- **al_block_0024 TUI** — Optional Textual terminal UI
- **al_block_0025 API Facade** — Stable public API

## Source

- `toktrail/` package structure
- `pyproject.toml` — entrypoint and dependencies
