---
schema_version: 2
id: al_content_0042
type: requirement
title: "Git-backed state synchronization"
status: proposed
section: introduction_and_goals
order: 80
date: "2026-05-23"
source: ""
priority: must
stakeholders: []
quality_goals: []
body_format: markdown
created_at: "2026-05-23T12:08:11Z"
updated_at: "2026-05-23T12:08:11Z"
---

## Requirement

toktrail must support synchronizing state databases across multiple machines
using git-backed repositories. Users can push local state to a git remote and pull
remote state to merge with local data. Archive import/export provides an offline alternative.

## Rationale

- Developers with multiple workstations need aggregated usage across all machines.
- Git is already familiar to the target audience and provides conflict-free merge for append-only event data.
- Archive export enables air-gapped or offline sharing scenarios.

## Source

- `toktrail/cli_sync.py` — sync CLI commands
- `toktrail/git_sync_parts/core.py` — git operations
- `toktrail/api/sync.py` — sync API

## Priority

should
