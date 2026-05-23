---
title: "Architecture Documentation"
date: "2026-05-23"
generator: "archledger 0.1.1.dev1+g15fa163cd"
arc42_template_version: "9.0-EN"
---

# Architecture Documentation

Generated from archledger records. Do not edit this generated file directly.

# Introduction and Goals

toktrail is a local-first CLI and library for tracking token usage from AI coding harnesses.
It reads source data (SQLite databases, JSONL files, JSON files) produced by harnesses like
OpenCode, Pi, GitHub Copilot CLI, Codex, Goose, Droid, Amp, Claude Code, and Vibe — normalizes
each billable model response into a durable `UsageEvent`, imports events idempotently into a
local SQLite database, and reports token/cost breakdowns by run, harness, model, area, and agent.

### Goals

- Provide accurate, deduplicated token accounting across multiple coding harnesses.
- Store all state locally in SQLite; no network calls, no telemetry, no cloud sync.
- Support multi-machine tracking with per-machine identity and area-based session grouping.
- Offer both CLI and optional Textual TUI for inspecting usage.
- Compute costs from user-configurable pricing; never invent estimates.
- Maintain import idempotence: repeated imports never duplicate accounting rows.

See child records for individual requirements.

## Requirements Overview

| Title                                     | Priority | Source | Stakeholders | Quality goals |
| ----------------------------------------- | -------- | ------ | ------------ | ------------- |
| Local-first token usage tracking          | must     |        |              |               |
| Read-only multi-harness source ingestion  | must     |        |              |               |
| Idempotent imports with stable dedup      | must     |        |              |               |
| Cost estimation from configurable pricing | must     |        |              |               |
| CLI and TUI reporting interfaces          | must     |        |              |               |
| Multi-machine tracking                    | must     |        |              |               |
| Area-based session grouping               | must     |        |              |               |

## Quality Goals

<!-- archledger: no accepted records for this section yet -->

## Stakeholders

<!-- archledger: no accepted records for this section yet -->

# Architecture Constraints

- **Local SQLite only.** The only state backend is a local SQLite database (default `~/.local/state/toktrail/toktrail.db`). No server, no cloud.
- **Python 3.10+.** Minimum Python version is 3.10; uses `tomllib` when available (3.11+), falls back to `tomli`.
- **Minimal runtime dependencies.** Only `typer`, `PyYAML`, `tzdata` (plus `tomli` on Python <3.11). Optional: `rich` for enhanced output, `textual` for TUI.
- **Read-only source access.** toktrail never modifies harness source databases or JSONL files. SQLite sources are opened read-only where possible.
- **No network calls.** No telemetry, no pricing lookups, no external API calls. All pricing is user-configured locally.
- **Single-user, single-machine default.** Multi-machine support exists via machine identity, but the canonical deployment is a single developer workstation.
- **WAL mode SQLite.** Uses WAL journal mode and NORMAL synchronous mode for concurrent read safety.
- **Schema migrations are explicit and versioned.** Current schema version is 15 (`toktrail/_db/core_db.py`).

<!-- archledger: no accepted records for this section yet -->

# Context and Scope

toktrail sits between AI coding harnesses (which produce token-usage data in heterogeneous
formats) and the developer (who needs a unified view of token consumption and cost).

### External systems (data sources)

| System             | Format     | Default path                                |
| ------------------ | ---------- | ------------------------------------------- |
| OpenCode           | SQLite     | `~/.local/share/opencode/opencode*.db`      |
| Pi                 | JSONL      | `~/.pi/agent/sessions`                      |
| GitHub Copilot CLI | OTEL JSONL | `~/.copilot/otel`                           |
| Codex              | JSONL      | `~/.codex/sessions`                         |
| Goose              | SQLite     | `~/.local/share/goose/sessions/sessions.db` |
| Droid              | JSON       | `~/.factory/sessions`                       |
| Amp                | JSON       | `~/.local/share/amp/threads`                |
| Claude Code        | JSONL      | `~/.claude/projects`                        |
| Vibe               | directory  | `~/.vibe/logs/session`                      |
| Code               | JSONL      | `~/.code/sessions`                          |
| Harnessbridge      | JSONL      | `~/.harnessbridge/sessions`                 |

### Users

- **Developer**: runs `toktrail` CLI commands to import, inspect, and report token usage.
- **Automation**: `toktrail watch` for continuous import; shell statusline integration.

toktrail does not expose any network service or API endpoint.

## System context

```textdiagram
┌─────────────────────────────────────────────────────────┐
│                       Developer                          │
│              (CLI commands, TUI, statusline)             │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                      toktrail                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │   CLI    │ │   TUI    │ │ Scanner  │ │  Costing   │ │
│  └────┬─────┘ └──────────┘ └────┬─────┘ └────────────┘ │
│       │                         │                        │
│       ▼                         ▼                        │
│  ┌──────────┐           ┌──────────────┐                │
│  │ API/DB   │           │   Adapters   │                │
│  └──────────┘           └──────┬───────┘                │
└────────────────────────────────┼─────────────────────────┘
                                 │ read-only
         ┌───────────┬───────────┼───────────┬────────────┐
         ▼           ▼           ▼           ▼            ▼
    ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
    │ OpenCode│ │   Pi    │ │ Copilot │ │  Codex  │ │  Goose  │
    │ (SQLite)│ │ (JSONL) │ │ (JSONL) │ │ (JSONL) │ │ (SQLite)│
    └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘
         ...       ...         ...         ...         ...
    ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
    │  Droid  │ │   Amp   │ │ Claude  │ │  Vibe   │ │  Code   │
    │  (JSON) │ │  (JSON) │ │ (JSONL) │ │  (dir)  │ │ (JSONL) │
    └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘
```

toktrail reads from 11 harness sources (left) and presents data to the developer (top) via CLI, TUI, and shell statusline. All source access is read-only.

**Caption:** System context

## Business Context

<!-- archledger: no accepted records for this section yet -->

## Technical Context

<!-- archledger: no accepted records for this section yet -->

# Solution Strategy

1. **Per-harness adapters** normalize heterogeneous source formats into a canonical `UsageEvent` model. Each adapter implements `scan()`, `parse()`, and `list_sessions()` against a shared protocol (`toktrail/adapters/base.py:HarnessAdapter`).

2. **Central SQLite ledger** stores normalized events with stable dedup keys (`global_dedup_key` uniqueness). Schema version is 15 with explicit migrations in `toktrail/_db/core_db.py`.

3. **Idempotent import loop**: `import` and `watch` commands discover sources, scan for events, insert new rows, and skip duplicates. File-level state tracking enables incremental resume for JSONL sources.

4. **Configurable costing**: user-defined pricing in TOML (`prices.toml`) drives actual and virtual cost computation via `toktrail/costing.py`. No external pricing lookups.

5. **Stable CLI and API facades**: `toktrail/cli.py`, `toktrail/db.py`, `toktrail/config.py`, and `toktrail/api/models.py` are stable entry points; implementation lives in `cli_parts/`, `_db/`, `config_parts/`, and `api/model_parts/`.

6. **Optional TUI**: Textual-based dashboard (`toktrail/tui/`) for interactive browsing, gated behind `[tui]` optional dependency.

## Strategy Items

<!-- archledger: no accepted records for this section yet -->

# Building Block View

The system is decomposed into seven primary building blocks. See child records for details.

- **al_0019 CLI Layer** — Typer-based command-line interface
- **al_0020 Adapter Layer** — Per-harness parsers and scanners
- **al_0021 Database Layer** — SQLite schema, migrations, CRUD, aggregation
- **al_0022 Reporting & Costing** — Cost computation and report generation
- **al_0023 Scanner & Discovery** — Source path discovery and fingerprinting
- **al_0024 TUI** — Optional Textual terminal UI
- **al_0025 API Facade** — Stable public API for programmatic access

## Building block decomposition

```textdiagram
toktrail
├── CLI Layer (al_0019)
│   ├── toktrail/cli_parts/main_cli.py    — commands, import/watch flows
│   ├── toktrail/cli_parts/watch.py       — watch loop
│   ├── toktrail/cli_parts/formatting.py  — human output
│   └── toktrail/cli_parts/table.py       — table rendering
│
├── API Facade (al_0025)
│   ├── toktrail/api/workflow.py          — run lifecycle
│   ├── toktrail/api/imports.py           — import operations
│   ├── toktrail/api/reports.py           — report generation
│   └── toktrail/api/model_parts/         — internal models
│
├── Adapter Layer (al_0020)
│   ├── toktrail/adapters/base.py         — HarnessAdapter protocol
│   ├── toktrail/adapters/registry.py     — HARNESS_REGISTRY
│   └── toktrail/adapters/<name>.py       — per-harness parsers
│
├── Database Layer (al_0021)
│   ├── toktrail/_db/core_db.py           — schema, CRUD, aggregation
│   ├── toktrail/_db/migrations.py        — schema migrations
│   └── toktrail/_db/usage_events.py      — event insertion
│
├── Reporting & Costing (al_0022)
│   ├── toktrail/reporting.py             — report dataclasses
│   ├── toktrail/costing.py               — cost computation
│   └── toktrail/config_parts/            — config parsing
│
├── Scanner & Discovery (al_0023)
│   ├── toktrail/scanner.py               — source discovery
│   └── toktrail/paths.py                 — path resolution
│
└── TUI (al_0024)  [optional]
    ├── toktrail/tui/app.py               — Textual app
    ├── toktrail/tui/panes/               — UI panes
    └── toktrail/tui/screens/             — forms
```

**Caption:** Building block decomposition

## Whitebox Overall System

## Purpose

toktrail is a single-process Python application that reads token usage data from
multiple AI coding harness sources, normalizes it into a canonical model, stores it
in a local SQLite database, and provides CLI and TUI interfaces for reporting.

## Components

- **al_0019 CLI Layer** — Typer-based command interface
- **al_0020 Adapter Layer** — Harness-specific parsers
- **al_0021 Database Layer** — SQLite state management
- **al_0022 Reporting & Costing** — Aggregation and cost computation
- **al_0023 Scanner & Discovery** — Source file discovery
- **al_0024 TUI** — Optional Textual terminal UI
- **al_0025 API Facade** — Stable public API

## Source

- `toktrail/` package structure
- `pyproject.toml` — entrypoint and dependencies

### Level 1

#### CLI Layer

**Parent:** al_0018
**Interfaces:**
**Location:**

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
- `tui` — launch Textual TUI

## Source

- `toktrail/cli.py` — facade
- `toktrail/cli_parts/main_cli.py` — implementation (~7800 lines)
- `toktrail/cli_parts/` — extracted CLI sub-modules

source_refs:

- toktrail/cli.py
- toktrail/cli_parts/

#### Adapter Layer

**Parent:** al_0018
**Interfaces:**
**Location:**

## Purpose

Per-harness adapters that read source data (SQLite, JSONL, JSON, directory) and produce
canonical `UsageEvent` objects. Each adapter implements the `HarnessAdapter` protocol.

## Registered harnesses

amp, opencode, pi, copilot, codex, code, goose, harnessbridge, droid, claude, vibe

## Interfaces

- `scan(source_path, ...) -> ScanResult` — read source and produce events
- `parse(source_path) -> list[UsageEvent]` — parse without scan state
- `list_sessions(source_path) -> list[SourceSessionSummary]` — enumerate sessions

## Source

- `toktrail/adapters/base.py` — protocol and shared types
- `toktrail/adapters/registry.py` — `HARNESS_REGISTRY` and discovery
- `toktrail/adapters/<name>.py` — per-harness implementation

source_refs:

- toktrail/adapters/base.py
- toktrail/adapters/registry.py
- toktrail/adapters/

#### Database Layer

**Parent:** al_0018
**Interfaces:**
**Location:**

## Purpose

SQLite state management: schema creation, migrations, CRUD operations, aggregation
queries, and cost computation. Current schema version is 15.

## Core tables

- `runs` — tracking windows (start/end timestamps, scope)
- `machines` — per-installation identity
- `areas` — named session groupings
- `area_session_assignments` — area ↔ source session links
- `source_sessions` — harness session references
- `source_session_metadata` — session metadata (git root, cwd, title)
- `usage_events` — normalized accounting ledger
- `run_events` — run ↔ event links
- `import_sources` — per-source import state (fingerprinting)
- `import_source_files` — per-file incremental state

## Interfaces

- `connect(path) -> Connection` — open with WAL + foreign keys
- `migrate(conn)` — run schema migrations
- `insert_usage_events(conn, events, run_id)` — idempotent event insertion
- `create_tracking_session(conn, ...)` — start a new run
- `get_active_tracking_session(conn)` — find current run
- Aggregation functions for reports (by harness, model, provider, area, time series)

## Source

- `toktrail/db.py` — facade
- `toktrail/_db/core_db.py` — implementation (~6600 lines)

source_refs:

- toktrail/db.py
- toktrail/\_db/core_db.py
- toktrail/\_db/

#### Reporting & Costing

**Parent:** al_0018
**Interfaces:**
**Location:**

## Purpose

Report dataclasses for structuring query results, and the costing engine for computing
actual/virtual costs from user-defined pricing.

## Key types

- `CostTotals` — source/actual/virtual cost aggregation
- `RunReport` — complete run status with grouped summaries
- `UsageReportFilter` — query parameters for usage queries
- `UsageSessionsReport`, `UsageRunsReport`, `UsageSeriesReport` — time-series and listing reports
- `CostBreakdown`, `PriceResolution`, `UsageCostAtom` — costing primitives

## Interfaces

- `toktrail/reporting.py` — report dataclasses
- `toktrail/costing.py` — cost computation engine (760 lines)
- `toktrail/config_parts/parse_pricing.py` — pricing config parsing

## Source

source_refs:

- toktrail/reporting.py
- toktrail/costing.py
- toktrail/config_parts/parse_pricing.py

#### Scanner & Discovery

**Parent:** al_0018
**Interfaces:**
**Location:**

## Purpose

Central source discovery: finds source files for each harness using default paths,
environment variables, and config overrides. Computes fingerprints for incremental
import and caching.

## Key types

- `SourceFingerprint` — file identity (size, mtime, inode, sha256)
- `SourceFile` — a discovered file ready for import
- `ScanWarning` — non-fatal discovery warnings
- `DiscoverSourcesResult` — sources + warnings

## Interfaces

- `discover_sources(config, harnesses) -> DiscoverSourcesResult`
- `_compute_fingerprint(path) -> SourceFingerprint`

## Source

source_refs:

- toktrail/scanner.py
- toktrail/paths.py
- toktrail/adapters/registry.py

#### TUI

**Parent:** al_0018
**Interfaces:**
**Location:**

## Purpose

Optional Textual-based terminal UI for interactive browsing of usage data, areas,
sessions, prices, and subscriptions.

## Structure

- `toktrail/tui/app.py` — Textual application
- `toktrail/tui/layout.py` — screen layout
- `toktrail/tui/state.py` — shared state
- `toktrail/tui/services.py` — data services
- `toktrail/tui/panes/` — dashboard, sessions, areas, prices, subscriptions, config
- `toktrail/tui/screens/` — forms (area, price, confirm)
- `toktrail/tui/styles/toktrail.tcss` — CSS styles

## Dependency

Gated behind `[tui]` optional dependency (`textual>=3,<4`, `tomlkit>=0.13`).

## Source

source_refs:

- toktrail/tui/

#### API Facade

**Parent:** al_0018
**Interfaces:**
**Location:**

## Purpose

Stable public API for programmatic access to toktrail's import, reporting, and
session management capabilities. Intended for integration by other tools and scripts.

## Modules

- `toktrail/api/models.py` — facade for stable API models
- `toktrail/api/model_parts/` — internal model implementations
- `toktrail/api/workflow.py` — run lifecycle operations
- `toktrail/api/imports.py` — import operations
- `toktrail/api/reports.py` — report generation
- `toktrail/api/sessions.py` — session queries
- `toktrail/api/sources.py` — source discovery
- `toktrail/api/prices.py` — pricing management
- `toktrail/api/events.py` — event queries
- `toktrail/api/areas.py` — area management

## Source

source_refs:

- toktrail/api/
- toktrail/api/models.py
- toktrail/api/model_parts/

# Runtime View

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

See diagram record al_0038 for the visual import flow.

## Import runtime flow

```textdiagram
User: toktrail import --harness pi
  │
  ▼
CLI resolves active run (or --run override)
  │
  ▼
Scanner discovers source files
  │  paths.py → default + env + config
  │  registry.py → HARNESS_REGISTRY["pi"]
  ▼
Adapter scans source files
  │  pi adapter → scan_pi_path()
  │  reads JSONL → parses messages
  │  produces ScanResult(events=[UsageEvent, ...])
  ▼
DB inserts events (idempotent)
  │  insert_usage_events(conn, events, run_id)
  │  INSERT OR IGNORE on global_dedup_key
  │  returns InsertUsageResult(rows_seen, rows_inserted, rows_skipped)
  ▼
CLI reports: "Seen N, imported M, skipped K"
```

**Caption:** Import runtime flow

<!-- archledger: no accepted records for this section yet -->

# Deployment View

### Installation

```bash
pip install toktrail              # core CLI
pip install "toktrail[tui]"       # with Textual TUI
pip install "toktrail[rich]"      # with Rich-enhanced output
```

### File layout

```
~/.local/state/toktrail/toktrail.db    # SQLite state database (default)
~/.config/toktrail/config.toml         # User configuration
~/.config/toktrail/machine.toml        # Machine identity
~/.config/toktrail/prices.toml         # Pricing definitions
~/.config/toktrail/subscriptions.toml  # Subscription tracking
```

Path overrides: `TOKTRAIL_DB`, `TOKTRAIL_CONFIG`, `TOKTRAIL_MACHINE_CONFIG`, `TOKTRAIL_PRICES`, `TOKTRAIL_SUBSCRIPTIONS`.

### Dependencies

- Runtime: `typer`, `PyYAML`, `tzdata`, `tomli` (Python <3.11)
- Optional: `textual>=3,<4`, `tomlkit`, `rich`
- Dev: `pytest`, `ruff`, `mypy`, `build`, `twine`

### Entrypoint

```
toktrail = "toktrail.cli:cli_main"
```

Single-node, single-user deployment. No server component.

<!-- archledger: no accepted records for this section yet -->

# Cross-cutting Concepts

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

<!-- archledger: no accepted records for this section yet -->

# Architecture Decisions

Key architecture decisions documented as ADR records:

- **al_0026**: SQLite as sole state backend
- **al_0027**: Adapter protocol for harness normalization
- **al_0028**: Global dedup key for import idempotence
- **al_0029**: Facade modules during staged refactor

## SQLite as sole state backend

**Status:** proposed
**Date:** 2026-05-23
**Deciders:**
**Supersedes:**
**Related:**

## Context

toktrail needs a durable local store for tracking sessions, imported events, and
aggregated reports. Options include flat files, embedded databases, or client-server
databases.

## Decision

Use SQLite as the sole state backend. Single file at `~/.local/state/toktrail/toktrail.db`
with WAL journal mode and NORMAL synchronous mode.

## Consequences

- **Positive**: Zero-config, single-file deployment, excellent read concurrency via WAL,
  strong SQL aggregation for reporting, no external service dependencies.
- **Negative**: Single-writer constraint (acceptable for single-user CLI), schema migrations
  require careful version management (currently at version 15).
- **Risks**: Database file corruption on unclean shutdown (mitigated by WAL + NORMAL sync).

## Source

- `toktrail/_db/core_db.py` — `connect()`, `migrate()`, schema definitions
- `toktrail/paths.py` — `resolve_toktrail_db_path()`

## Adapter protocol for harness normalization

**Status:** proposed
**Date:** 2026-05-23
**Deciders:**
**Supersedes:**
**Related:**

## Context

toktrail must ingest data from multiple harnesses with heterogeneous formats (SQLite,
JSONL, JSON, directory-based). Each harness has different schemas, field names, and
session models.

## Decision

Define a `HarnessAdapter` protocol (`toktrail/adapters/base.py`) with `scan()`,
`parse()`, and `list_sessions()` methods. Each harness gets a dedicated adapter module
under `toktrail/adapters/`. A central `HARNESS_REGISTRY` maps harness names to
`HarnessDefinition` objects that include scan functions, path resolution, and metadata.

## Consequences

- **Positive**: Adding a new harness requires only a new adapter module and registry entry.
  All harnesses produce the same `UsageEvent` type, enabling uniform reporting.
- **Negative**: Each adapter duplicates some boilerplate for file scanning. Normalization
  differences between adapters could introduce accounting inconsistencies.
- **Risks**: Upstream harness format changes break adapters. Mitigated by parser tests
  per harness.

## Source

- `toktrail/adapters/base.py:HarnessAdapter`
- `toktrail/adapters/registry.py:HARNESS_REGISTRY`

## Global dedup key for import idempotence

**Status:** proposed
**Date:** 2026-05-23
**Deciders:**
**Supersedes:**
**Related:**

## Context

Users run `toktrail import` and `toktrail watch` repeatedly. The same source events
appear in every scan. Duplicate events would inflate token counts.

## Decision

Each `UsageEvent` carries a `global_dedup_key` (string). The `usage_events` table has
a `UNIQUE` constraint on this column. `insert_usage_events()` uses `INSERT OR IGNORE`
to skip duplicates. Additionally, `source_dedup_key` provides traceability to the
source record, and `fingerprint_hash` detects content drift.

## Consequences

- **Positive**: Repeated imports are safe. Watch can run continuously without double-counting.
- **Negative**: Dedup key stability must be maintained across parser changes. Changing
  key computation requires migration planning.
- **Risks**: If a harness changes its source format, old dedup keys may become stale.

## Source

- `toktrail/models.py:UsageEvent.global_dedup_key`
- `toktrail/_db/core_db.py:insert_usage_events()`

## Facade modules during staged refactor

**Status:** proposed
**Date:** 2026-05-23
**Deciders:**
**Supersedes:**
**Related:**

## Context

`toktrail/db.py`, `toktrail/config.py`, `toktrail/cli.py`, and `toktrail/api/models.py`
grew into very large modules. A direct split would break public imports.

## Decision

Use facade modules that re-export from internal packages:

- `toktrail/db.py` → `toktrail/_db/core_db.py`
- `toktrail/config.py` → `toktrail/config_parts/core_config.py`
- `toktrail/cli.py` → `toktrail/cli_parts/`
- `toktrail/api/models.py` → `toktrail/api/model_parts/`

Facades use `__getattr__` for lazy re-export. Public imports remain stable.

## Consequences

- **Positive**: Public API contracts preserved. Refactoring proceeds incrementally.
- **Negative**: Indirection layer adds cognitive overhead. `__getattr__` may confuse
  type checkers (mypy ignore-errors on facades).
- **Risks**: Incomplete refactoring leaves some functionality in facades.

## Source

- `toktrail/db.py`, `toktrail/config.py`, `toktrail/cli.py` — facades
- `toktrail/_db/`, `toktrail/config_parts/`, `toktrail/cli_parts/`, `toktrail/api/model_parts/`

# Quality Requirements

Quality requirements documented as child records:

- **al_0030**: Import idempotence — repeated imports must not duplicate accounting
- **al_0031**: Token accounting accuracy — counts must match source data exactly
- **al_0032**: Source read-only guarantee — never mutate harness source data
- **al_0033**: Local-only privacy — no telemetry, no network calls

## Quality Requirements Overview

| Title                      | Category    | Measure | Scenarios |
| -------------------------- | ----------- | ------- | --------- |
| Import idempotence         | reliability |         |           |
| Token accounting accuracy  | reliability |         |           |
| Source read-only guarantee | reliability |         |           |
| Local-only privacy         | reliability |         |           |

## Quality Scenarios

<!-- archledger: no accepted records for this section yet -->

# Risks and Technical Debt

### Risks

- **Schema migration complexity**: Schema is at version 15 with extensive migration chains. Future changes must carefully handle upgrade paths.
- **Large monolithic modules**: `toktrail/_db/core_db.py` (6600 lines) and `toktrail/cli_parts/main_cli.py` (7800 lines) are difficult to navigate. The staged refactor is extracting internals into sub-packages.
- **Adapter divergence**: Each harness adapter implements its own parsing logic. Inconsistent normalization could introduce accounting drift.
- **Harness source format changes**: Upstream harnesses may change their output format without notice, breaking adapters.

### Technical Debt

- The staged refactor (facade pattern) is in progress: `toktrail/db.py`, `toktrail/config.py`, `toktrail/cli.py` currently re-export from `_db/`, `config_parts/`, `cli_parts/`. Completion requires moving all remaining implementation.
- Some adapters share copy-paste boilerplate for JSONL scanning that could be extracted into shared utilities.
- Test coverage is strong for parsers and DB, but some CLI integration paths are less exercised.

## Risk Overview

<!-- archledger: no accepted records for this section yet -->

# Glossary

| Term                 | Definition                                                                                                                                        |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Harness**          | An AI coding tool that produces token-usage data (e.g., OpenCode, Pi, Copilot, Codex, Goose, Droid, Amp, Claude Code, Vibe, Code, Harnessbridge). |
| **Run**              | A named tracking window with start/end timestamps. Replaces the earlier "tracking session" concept.                                               |
| **Source session**   | A session within a harness's source data (e.g., one OpenCode database session, one Pi JSONL file).                                                |
| **UsageEvent**       | The canonical normalized record of one billable model response, including token counts, model, provider, dedup keys, and optional raw JSON.       |
| **Adapter**          | A module that reads a specific harness's source format and produces `UsageEvent` objects.                                                         |
| **Scan**             | The process of reading source files and producing `ScanResult` with events.                                                                       |
| **Import**           | Inserting scanned events into the SQLite database with deduplication.                                                                             |
| **Watch**            | Continuous repeated import at a configurable interval.                                                                                            |
| **Area**             | A named grouping of source sessions for organizational purposes (project, workspace, etc.).                                                       |
| **Machine**          | A unique installation identity for multi-machine tracking.                                                                                        |
| **Provider**         | The AI model provider (e.g., `anthropic`, `openai`, `google`). Inferred from model ID when not in source data.                                    |
| **Thinking level**   | A model's reasoning mode (e.g., `low`, `medium`, `high`).                                                                                         |
| **Dedup key**        | A stable hash that uniquely identifies a source event to prevent duplicate imports.                                                               |
| **Fingerprint hash** | A hash of accounting-relevant fields that changes when source content drifts.                                                                     |
| **Costing**          | Computing actual and virtual costs from user-defined pricing configuration.                                                                       |

<!-- archledger: no accepted records for this section yet -->
