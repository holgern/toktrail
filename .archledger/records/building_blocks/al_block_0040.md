---
schema_version: 2
id: al_block_0040
type: black_box
title: "Source Cache \u0026 Analysis"
status: proposed
section: building_block_view
level: 1
parent: al_block_0018
order: 90
date: "2026-05-23"
interfaces: []
location: []
fulfilled_requirements: []
risks: []
tags: []
body_format: markdown
created_at: "2026-05-23T12:07:40Z"
updated_at: "2026-05-23T12:07:40Z"
---

## Purpose

Source file caching to avoid reparsing large event files on repeated imports,
and cache efficiency analysis that computes hit/miss rates, warm-up timelines,
and per-session cache statistics for imported usage events.

## Interfaces

- `toktrail.cache:SourceFileCache` — file-level event cache keyed by harness, path, parser version, and fingerprint
- `toktrail.analysis` — cache call analysis, session-level analytics, warm-up curves

## Key types

- `CacheCallAnalysis` — per-call cache hit/miss record with ordinal, harness, model, thinking level
- `CacheSessionAnalysis` — per-session aggregate cache statistics
- `CacheWarmupPoint` — point in a session where cache reaches a hit threshold
- `SourceFileCache`/`CachedEvents` — cached parse results with fingerprint validation

## Source

- `toktrail/cache.py` (~70 lines) — source file cache
- `toktrail/analysis.py` (~473 lines) — cache efficiency analysis
- `toktrail/api/analysis.py` — analysis API
