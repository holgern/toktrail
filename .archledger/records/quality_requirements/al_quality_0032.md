---
schema_version: 2
id: al_quality_0032
type: quality_requirement
title: "Source read-only guarantee"
status: proposed
section: quality_requirements
order: 30
date: "2026-05-23"
category: reliability
source: ""
measure: ""
scenarios: []
body_format: markdown
created_at: "2026-05-23T05:59:43Z"
updated_at: "2026-05-23T05:59:43Z"
---

## Scenario

Given an OpenCode SQLite database, when `toktrail import --harness opencode` runs,
then the source database file's SHA-256 hash is unchanged afterward.

## Quality attribute

Safety

## Measurement

- SQLite sources opened with `immutable=1` or read-only mode where possible.
- No `UPDATE`, `INSERT`, or `DELETE` issued against source databases.
- JSONL files are only read, never appended or modified.

## Source

- `toktrail/adapters/opencode.py` — read-only SQLite access
- `AGENTS.md` section 4.2 — adapter invariants
