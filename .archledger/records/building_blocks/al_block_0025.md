---
schema_version: 2
id: al_block_0025
type: black_box
title: "API Facade"
status: proposed
section: building_block_view
level: 1
parent: al_block_0018
order: 70
date: "2026-05-23"
interfaces: []
location: []
fulfilled_requirements: []
risks: []
tags: []
body_format: markdown
created_at: "2026-05-23T05:59:26Z"
updated_at: "2026-05-23T05:59:26Z"
source_refs:
  - path: toktrail/api/
    reason: "Directory-wide ownership"
  - toktrail/api/models.py
  - path: toktrail/api/model_parts/
    reason: "Internal model implementations"
---

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
- `toktrail/api/analysis.py` — cache efficiency analysis
- `toktrail/api/sync.py` — state synchronization
- `toktrail/api/machines.py` — machine identity
- `toktrail/api/statusline.py` — statusline data
- `toktrail/api/config.py` — configuration
- `toktrail/api/paths.py` — path resolution
- `toktrail/api/_conversions.py` — shared type conversions

## Source

source_refs:

- toktrail/api/
- toktrail/api/models.py
- toktrail/api/model_parts/
