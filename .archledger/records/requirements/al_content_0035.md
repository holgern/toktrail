---
schema_version: 2
id: al_content_0035
type: requirement
title: "Area-based session grouping"
status: proposed
section: introduction_and_goals
order: 70
date: "2026-05-23"
source: ""
priority: must
stakeholders: []
quality_goals: []
body_format: markdown
created_at: "2026-05-23T05:59:44Z"
updated_at: "2026-05-23T05:59:44Z"
---

## Requirement

toktrail must support grouping source sessions into named areas (e.g., projects,
workspaces) with per-area token usage reporting.

## Rationale

- Developers work on multiple projects; per-project usage tracking is valuable.
- Areas enable cost allocation across different work contexts.
- Area auto-detection from git roots reduces manual overhead.

## Source

- `toktrail/_db/core_db.py` — `areas`, `area_session_assignments` tables
- `toktrail/cli_parts/main_cli.py` — area commands
- `toktrail/api/areas.py` — area API

## Priority

should
