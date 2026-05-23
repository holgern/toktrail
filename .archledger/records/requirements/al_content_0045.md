---
schema_version: 2
id: al_content_0045
type: requirement
title: "Shell statusline integration"
status: proposed
section: introduction_and_goals
order: 110
date: "2026-05-23"
source: ""
priority: must
stakeholders: []
quality_goals: []
body_format: markdown
created_at: "2026-05-23T12:08:45Z"
updated_at: "2026-05-23T12:08:45Z"
---

## Requirement

toktrail must provide a shell statusline output that renders live token usage,
cost burn rate, cache efficiency, and subscription quota in a compact format
suitable for embedding in terminal prompts (bash, zsh, fish) or status bars.

## Rationale

- Developers want at-a-glance usage visibility without running full report commands.
- Prompt integration provides continuous awareness during coding sessions.
- Compact single-line format is required for shell prompt embedding.

## Source

- `toktrail/statusline.py` (~1044 lines) — statusline rendering and data assembly
- `toktrail/api/statusline.py` — statusline API
- `toktrail/cli_parts/statusline.py` — CLI command

## Priority

should
