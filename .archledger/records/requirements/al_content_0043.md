---
schema_version: 2
id: al_content_0043
type: requirement
title: "Cache efficiency analysis"
status: proposed
section: introduction_and_goals
order: 90
date: "2026-05-23"
source: ""
priority: must
stakeholders: []
quality_goals: []
body_format: markdown
created_at: "2026-05-23T12:08:12Z"
updated_at: "2026-05-23T12:08:12Z"
---

## Requirement

toktrail must provide cache efficiency analysis for imported usage events,
computing hit/miss rates, warm-up timelines, and per-session cache statistics.
This enables developers to understand how effectively their prompts leverage
provider-side caching and optimize cache reuse.

## Rationale

- Cache hit rates directly impact cost; understanding cache efficiency helps reduce spend.
- Warm-up analysis shows how many requests are needed before cache stabilizes.
- Per-session breakdown helps identify which workflows have poor cache utilization.

## Source

- `toktrail/analysis.py` — cache call analysis and session analytics
- `toktrail/cache.py` — source file caching to avoid reparsing
- `toktrail/api/analysis.py` — analysis API

## Priority

should
