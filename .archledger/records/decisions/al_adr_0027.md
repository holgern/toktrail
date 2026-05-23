---
schema_version: 2
id: al_adr_0027
type: adr
title: "Adapter protocol for harness normalization"
status: proposed
section: architecture_decisions
order: 20
date: "2026-05-23"
deciders: []
supersedes: []
related: []
tags: []
body_format: markdown
created_at: "2026-05-23T05:59:34Z"
updated_at: "2026-05-23T05:59:34Z"
---

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
