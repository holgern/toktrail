---
schema_version: 2
id: al_content_0044
type: requirement
title: "Subscription quota tracking"
status: proposed
section: introduction_and_goals
order: 100
date: "2026-05-23"
source: ""
priority: must
stakeholders: []
quality_goals: []
body_format: markdown
created_at: "2026-05-23T12:08:29Z"
updated_at: "2026-05-23T12:08:29Z"
---

## Requirement

toktrail must track token usage against user-defined subscription quotas.
Users configure subscription definitions in `subscriptions.toml` with token
allowances and billing periods. toktrail reports consumption relative to those limits.

## Rationale

- Developers on subscription plans (e.g., Anthropic Max, GitHub Copilot Enterprise) need to monitor usage against quotas.
- Proactive quota tracking prevents unexpected overages.
- Subscription-aware reporting provides cost context beyond per-token pricing.

## Source

- `toktrail/cli_parts/subscriptions.py` — subscription CLI commands
- `toktrail/config_parts/parse_subscriptions.py` — subscription config parsing
- `toktrail/api/_conversions.py` — subscription types and conversions
- `toktrail/_db/reports_subscriptions.py` — subscription DB queries
- `toktrail/tui/panes/subscriptions.py` — TUI subscription pane

## Priority

should
