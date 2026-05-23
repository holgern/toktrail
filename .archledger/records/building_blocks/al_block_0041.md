---
schema_version: 2
id: al_block_0041
type: black_box
title: "Sync \u0026 State Sharing"
status: proposed
section: building_block_view
level: 1
parent: al_block_0018
order: 100
date: "2026-05-23"
interfaces: []
location: []
fulfilled_requirements: []
risks: []
tags: []
body_format: markdown
created_at: "2026-05-23T12:08:00Z"
updated_at: "2026-05-23T12:08:00Z"
---

## Purpose

Multi-machine state synchronization via git-backed repositories and archive
import/export. Enables toktrail databases on different machines to share state
through push/pull to a git remote, or through portable archive files.

## Interfaces

- `toktrail.cli_sync` — CLI commands: `sync push/pull/status`, archive import/export
- `toktrail.git_sync` / `git_sync_parts/core.py` — git repository management, worktree operations, hooks
- `toktrail.sync` / `sync_parts/core.py` — sync compatibility facade
- `toktrail.api.sync` — sync API facade

## Key capabilities

- `sync push` — commit and push local state to a configured git remote
- `sync pull` — pull and merge remote state into local database
- `sync status` — show divergence between local and remote
- Archive export/import — portable SQLite snapshots for offline sharing
- Git hooks integration — pre-commit, post-merge hooks for automatic sync
- Worktree-based merge — safe merge of multi-machine state without conflicts

## Source

- `toktrail/cli_sync.py` (~1180 lines) — sync CLI commands
- `toktrail/git_sync.py` — facade for git_sync_parts
- `toktrail/git_sync_parts/core.py` — git operations (repo init, push, pull, hooks, worktree, cleanup)
- `toktrail/sync.py` — facade for sync_parts
- `toktrail/sync_parts/core.py` — sync compatibility layer
- `toktrail/api/sync.py` — sync API
