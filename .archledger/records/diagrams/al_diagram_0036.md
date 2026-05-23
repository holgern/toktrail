---
schema_version: 2
id: al_diagram_0036
type: diagram
title: "System context"
status: proposed
section: context_and_scope
order: 10
date: "2026-05-23"
diagram_type: "text"
caption: "System context"

related_records: []

tags: []
body_format: markdown
created_at: "2026-05-23T05:59:51Z"
updated_at: "2026-05-23T05:59:51Z"
---

```textdiagram
┌─────────────────────────────────────────────────────────┐
│                       Developer                          │
│              (CLI commands, TUI, statusline)             │
└────────────────────────┬────────────────────────────────┘
                         │
                         ▼
┌─────────────────────────────────────────────────────────┐
│                      toktrail                            │
│  ┌──────────┐ ┌──────────┐ ┌──────────┐ ┌────────────┐ │
│  │   CLI    │ │   TUI    │ │ Scanner  │ │  Costing   │ │
│  └────┬─────┘ └──────────┘ └────┬─────┘ └────────────┘ │
│       │                         │                        │
│       ▼                         ▼                        │
│  ┌──────────┐           ┌──────────────┐                │
│  │ API/DB   │           │   Adapters   │                │
│  └──────────┘           └──────┬───────┘                │
└────────────────────────────────┼─────────────────────────┘
                                 │ read-only
         ┌───────────┬───────────┼───────────┬────────────┐
         ▼           ▼           ▼           ▼            ▼
    ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
    │ OpenCode│ │   Pi    │ │ Copilot │ │  Codex  │ │  Goose  │
    │ (SQLite)│ │ (JSONL) │ │ (JSONL) │ │ (JSONL) │ │ (SQLite)│
    └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘
         ...       ...         ...         ...         ...
    ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐ ┌─────────┐
    │  Droid  │ │   Amp   │ │ Claude  │ │  Vibe   │ │  Code   │
    │  (JSON) │ │  (JSON) │ │ (JSONL) │ │  (dir)  │ │ (JSONL) │
    └─────────┘ └─────────┘ └─────────┘ └─────────┘ └─────────┘
```

toktrail reads from 11 harness sources (left) and presents data to the developer (top) via CLI, TUI, and shell statusline. All source access is read-only.
