---
schema_version: 2
id: al_content_0001
type: section
section: introduction_and_goals
title: Introduction and Goals
order: 10
status: accepted
date: "2026-05-22"
body_format: markdown
created_at: "2026-05-22T20:27:22Z"
updated_at: "2026-05-22T20:27:22Z"
---

toktrail is a local-first CLI and library for tracking token usage from AI coding harnesses.
It reads source data (SQLite databases, JSONL files, JSON files) produced by harnesses like
OpenCode, Pi, GitHub Copilot CLI, Codex, Goose, Droid, Amp, Claude Code, Vibe, Code, and Harnessbridge — normalizes
each billable model response into a durable `UsageEvent`, imports events idempotently into a
local SQLite database, and reports token/cost breakdowns by run, harness, model, area, and agent.

### Goals

- Provide accurate, deduplicated token accounting across multiple coding harnesses.
- Store all state locally in SQLite; no network calls, no telemetry, no cloud sync.
- Support multi-machine tracking with per-machine identity, area-based session grouping, and git-backed sync.
- Offer both CLI and optional Textual TUI for inspecting usage.
- Compute costs from user-configurable pricing; never invent estimates.
- Maintain import idempotence: repeated imports never duplicate accounting rows.
- Provide shell statusline integration for at-a-glance usage visibility.
- Track usage against subscription quotas.
- Analyze cache efficiency to help developers optimize prompt caching.

See child records for individual requirements.
