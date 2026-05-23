---
schema_version: 2
id: al_content_0014
type: requirement
title: "Read-only multi-harness source ingestion"
status: proposed
section: introduction_and_goals
order: 20
date: "2026-05-23"
source: ""
priority: must
stakeholders: []
quality_goals: []
body_format: markdown
created_at: "2026-05-23T05:54:27Z"
updated_at: "2026-05-23T05:54:27Z"
---

## Requirement

toktrail must read source data from multiple harness formats (SQLite, JSONL, JSON)
without ever modifying the source data. Each harness adapter normalizes source records
into a canonical `UsageEvent`.

## Rationale

- Source databases belong to the harness; modifying them risks corruption.
- JSONL files are append-only logs; toktrail must not alter them.
- Normalization into a single model enables cross-harness aggregation.

## Source

- `toktrail/adapters/base.py:HarnessAdapter` — adapter protocol
- `toktrail/adapters/registry.py:HARNESS_REGISTRY` — 11 registered harnesses
- `toktrail/adapters/opencode.py` — SQLite read-only mode
- `toktrail/adapters/pi.py` — JSONL file scanning

## Priority

must
