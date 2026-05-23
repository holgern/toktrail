---
schema_version: 2
id: al_quality_0031
type: quality_requirement
title: "Token accounting accuracy"
status: proposed
section: quality_requirements
order: 20
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

Given a source event reporting 1500 input tokens and 500 output tokens, when imported,
then the `usage_events` row stores exactly `input=1500`, `output=500`, `total=2000`.

## Quality attribute

Accuracy

## Measurement

- Adapter tests assert exact token counts from known source fixtures.
- `TokenBreakdown.total` is always `input + output`.
- Negative token values are rejected.
- Cache tokens remain distinct from input tokens.

## Source

- `toktrail/models.py:TokenBreakdown`
- `tests/test_*_parser.py` — per-harness accuracy tests
