---
schema_version: 2
id: al_quality_0030
type: quality_requirement
title: "Import idempotence"
status: proposed
section: quality_requirements
order: 10
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

Given a source database with 100 events, when `toktrail import` is run 5 times, then
the database contains exactly 100 usage events with zero duplicates.

## Quality attribute

Correctness

## Measurement

- `INSERT OR IGNORE` on `global_dedup_key` uniqueness constraint.
- Parser tests verify stable dedup key generation.
- CLI tests verify repeated imports produce same event count.

## Source

- `toktrail/_db/core_db.py:insert_usage_events()`
- `tests/test_db.py` — dedup tests
