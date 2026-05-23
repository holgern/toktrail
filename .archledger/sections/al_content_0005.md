---
schema_version: 2
id: al_content_0005
type: section
section: building_block_view
title: Building Block View
order: 50
status: accepted
date: "2026-05-22"
body_format: markdown
created_at: "2026-05-22T20:27:22Z"
updated_at: "2026-05-22T20:27:22Z"
---

The system is decomposed into seven primary building blocks. See child records for details.

- **al_block_0019 CLI Layer** — Typer-based command-line interface
- **al_block_0020 Adapter Layer** — Per-harness parsers and scanners
- **al_block_0021 Database Layer** — SQLite schema, migrations, CRUD, aggregation
- **al_block_0022 Reporting & Costing** — Cost computation and report generation
- **al_block_0023 Scanner & Discovery** — Source path discovery and fingerprinting
- **al_block_0024 TUI** — Optional Textual terminal UI
- **al_block_0025 API Facade** — Stable public API for programmatic access
