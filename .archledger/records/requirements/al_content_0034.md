---
schema_version: 2
id: al_content_0034
type: requirement
title: "Multi-machine tracking"
status: proposed
section: introduction_and_goals
order: 60
date: "2026-05-23"
source: ""
priority: must
stakeholders: []
quality_goals: []
body_format: markdown
created_at: "2026-05-23T05:59:43Z"
updated_at: "2026-05-23T05:59:43Z"
---

## Requirement

toktrail must support tracking token usage across multiple machines. Each installation
generates a unique machine identity, and events carry `origin_machine_id` for attribution.

## Rationale

- Developers may use multiple workstations (desktop, laptop, remote server).
- Aggregate reporting should distinguish usage per machine.
- Machine identity enables area assignment per machine.

## Source

- `toktrail/_db/core_db.py` — `machines` table, machine CRUD
- `toktrail/cli_parts/machines.py` — machine commands
- `toktrail/config_parts/parse_runtime.py` — machine config

## Priority

should
