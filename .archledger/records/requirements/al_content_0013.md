---
schema_version: 2
id: al_content_0013
type: requirement
title: "Local-first token usage tracking"
status: proposed
section: introduction_and_goals
order: 10
date: "2026-05-23"
source: ""
priority: must
stakeholders: []
quality_goals: []
body_format: markdown
created_at: "2026-05-23T05:54:26Z"
updated_at: "2026-05-23T05:54:26Z"
---

## Requirement

toktrail must provide a local-first tool for tracking token usage from AI coding
harnesses, storing all data in a local SQLite database with no network dependencies.

## Rationale

- Developers using multiple AI coding tools need a single place to see aggregate token usage.
- Token usage data is sensitive; it should never leave the developer's machine unless explicitly exported.
- No existing tool provides unified multi-harness token tracking with local-only storage.

## Source

- `pyproject.toml` description
- `toktrail/paths.py` — default local paths
- `toktrail/_db/core_db.py` — SQLite backend

## Priority

must
