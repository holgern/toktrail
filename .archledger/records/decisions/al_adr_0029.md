---
schema_version: 2
id: al_adr_0029
type: adr
title: "Facade modules during staged refactor"
status: proposed
section: architecture_decisions
order: 40
date: "2026-05-23"
deciders: ["project-author"]
supersedes: []
related: []
tags: []
body_format: markdown
created_at: "2026-05-23T05:59:34Z"
updated_at: "2026-05-23T05:59:34Z"
---

## Context

`toktrail/db.py`, `toktrail/config.py`, `toktrail/cli.py`, and `toktrail/api/models.py`
grew into very large modules. A direct split would break public imports.

## Decision

Use facade modules that re-export from internal packages:

- `toktrail/db.py` → `toktrail/_db/core_db.py`
- `toktrail/config.py` → `toktrail/config_parts/core_config.py`
- `toktrail/cli.py` → `toktrail/cli_parts/`
- `toktrail/api/models.py` → `toktrail/api/model_parts/`

Facades use `__getattr__` for lazy re-export. Public imports remain stable.

## Consequences

- **Positive**: Public API contracts preserved. Refactoring proceeds incrementally.
- **Negative**: Indirection layer adds cognitive overhead. `__getattr__` may confuse
  type checkers (mypy ignore-errors on facades).
- **Risks**: Incomplete refactoring leaves some functionality in facades.

## Source

- `toktrail/db.py`, `toktrail/config.py`, `toktrail/cli.py` — facades
- `toktrail/_db/`, `toktrail/config_parts/`, `toktrail/cli_parts/`, `toktrail/api/model_parts/`
