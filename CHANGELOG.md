# Changelog

## [0.2.0] - 2026-05-30

### Added

- **Statusline**: top-level `toktrail statusline` CLI with config-driven harness/session selection, burn-rate calculation, output caching, and automatic refresh gating.
- **Harnessbridge adapter**: import, source discovery, and statusline support for Harnessbridge session ledger files.
- **Code harness**: adapter wrapping Codex scanning with dedicated path resolution (`TOKTRAIL_CODE_SESSIONS`, `CODE_HOME`) and distinct `code-` prefixed dedup identity.
- **Git-backed sync**: `toktrail sync git` command family (init, status, pull, push, sync) for sharing toktrail state through a Git repository. Includes record-level text-state files with manifest validation, snapshot export, branch robustness, and pending-import detection.
- **Git-backed pricing and subscriptions**: `sync.git.track` config option to version-control pricing and subscription files alongside toktrail state.
- **Git sync cleanup**: `toktrail sync git cleanup` for analysis, working-tree cleanup, and history reset of sync repositories, with legacy v1 repo compatibility.
- **Usage areas**: hierarchical area management with create, activate, assign, unassign, bulk-assign, move, archive, rename, and merge commands. Areas carry stable sync IDs for cross-machine portability and support suffix shortcuts and leaf filters.
- **Area-scoped subscriptions**: provider subscriptions can be scoped to a specific area, enabling separate quota tracking for different work contexts.
- **Session metadata**: durable `source_session_metadata` storage (CWD, source paths, git root/remote, session title) persisted during imports and exposed in `usage sessions` output and JSON.
- **Session digests**: `toktrail analyze session` produces deterministic session summaries with token breakdowns, model/provider usage, cache ratios, and tool health metrics. Pi and Codex harnesses extract failed tool/command counts.
- **Compact session reports**: `analyze session` now defaults to a compact backend report with digest artifacts behind `--details`. Verbose cache analysis output is no longer the default.
- **Tool call analysis**: `analyze session --bad-calls` inspects Codex tool calls for failures, timeouts, and status classification.
- **Deterministic insights**: `toktrail insights report` generates a Markdown or JSON report with at-a-glance metrics, period-over-period deltas, anomaly detection, and deterministic suggestions. No LLM or network calls.
- **Quota reset countdown**: subscription status, statusline, and TUI now show relative quota reset times (e.g. "in 2h 19m") and shortened window labels.
- **Provider aliases**: configurable `[costing.provider_aliases]` map raw provider IDs to canonical names for consistent costing and quota accounting.
- **TUI (Textual-based terminal UI)**: `toktrail tui` launches an interactive dashboard with panes for dashboard, sessions, areas, prices, subscriptions, and config. Features responsive auto/full/compact/micro display modes, Termux small-screen support, keyboard navigation, clipboard export, and inline price editing.
- **TUI dashboard views**: `t`/`d`/`w` keys switch between today, daily, and weekly usage views in the TUI dashboard.
- **TUI subscriptions tab**: read-only subscription plan display with quota windows, reset countdowns, and scope information.
- **TUI session day navigation**: left/right arrows navigate sessions and areas day-by-day with status bar date display. `?`/`h` keys show a mode-aware help overlay.
- **Incremental imports**: directory-based harnesses (Codex, Copilot, Pi, Harnessbridge, Claude, Amp, Droid, Vibe) now skip unchanged files and resume from saved offsets during quick refreshes.
- **Area selectors**: unique suffix matching (`--area myproject`) and explicit leaf filters (`--area-leaf`) for usage reporting and area sessions.
- **Config file status in sync**: `sync git status` shows the source of pricing and subscription config files (local, Git-backed, or override).
- **Usage day/week aliases**: `toktrail usage day` and `toktrail usage week` as shortcuts for today and this-week views.

### Changed

- Git sync state format: migrated from archive payloads (tar.gz) to record-level text-state files (`state/*.jsonl`) with manifest validation and deterministic filenames.
- Git sync grouped usage-event files into JSONL format to reduce file count and improved legacy v3/v4 import compatibility.
- `analyze session` default output is now a compact session report instead of a verbose cache analysis. Detailed digests require `--details`.
- Quota window text shortened: active windows show relative reset countdowns, expired windows show shorter status labels, and same-day windows use deduplicated time ranges.
- Subscription quota table column headers and layout updated for clarity.
- Manual pricing API (`toktrail/api/prices.py`) supports list, upsert, and delete operations with parser/render validation.

### Fixed

- Fixed Harnessbridge statusline import failing on directory-backed sources and restored inner-harness reporting.
- Removed implicit Git auto-export from `refresh` and `report` command paths. Git sync is now explicit only.
- Fixed Codex and Code harnesses not parsing rollout event envelope `token_count` payloads.
- Fixed OpenCode source-session display using DB parent path instead of the actual session working directory.
- Fixed `statusline --provider` showing wrong token counts and quota when no `--harness` was specified. Auto-refresh now imports all configured harnesses when filtering by provider.
- Fixed `mypy toktrail` baseline: all type errors resolved across insights, costing, and shared modules.

### Internal

- Architecture refactor: extracted internal implementations into `cli_parts/`, `_db/`, `config_parts/`, `api/model_parts/`, `sync_parts/`, and `git_sync_parts/` with import-stable public facades preserved.
- Removed `cli_legacy` test module and `legacy_cli.py` compatibility facade. CLI tests split into explicit `tests/cli/test_*.py` modules.
- Extracted shared adapter helpers into `toktrail/adapters/_common.py` to reduce duplication across ten harness adapters.
- Extracted clipboard/export helpers, centralized pane metadata, shared table selection helpers, and Textual worker refresh in the TUI layer.
- Schema migrations through v16: added `source_session_metadata`, `import_source_files`, `source_session_digests`, area identity fields, sync import registry, and area-scoped subscription support.
- TUI code quality review completed with prioritized findings recorded.

## [0.1.1] - 2026-05-12

### Added

- Period selectors for `usage sessions`: `--period daily`, `--period weekly`, `--period monthly`, and `--period all-time` with `--timezone` support.
- Default line output for `usage sessions` (human-readable summary). Use `--table` for the legacy table view.
- `usage statusline` command for a quick single-line token and cost summary (human and JSON).
- `stats` command with JSON output for aggregated usage statistics.
- Quick refresh mode for imports. `refresh` now uses quick mode by default; pass `--full` to re-scan from scratch.
- `sources skipped` command to inspect cached skipped sources.
- Harness registry metadata populated for all supported harnesses, exposed via `sources --json`.
- `StatsReport` API model and stats v1 report endpoint.
- Harness metadata fields in public API models.
- Schema v8: `skipped_sources` table for caching sources that failed to import.

### Changed

- `usage sessions` now outputs a line summary by default instead of a table. Use `--table` for the previous behavior.
- `usage_sessions_report` API now accepts period and timezone parameters with conflict validation for incompatible options.

### Fixed

- Period option conflict detection in `usage sessions` rejects incompatible flag combinations.

### Documentation

- Documented period-based usage sessions commands and table fallback in `docs/usage.rst`.
- Added `docs/harnesses.rst` documenting harness registry metadata.
- Documented statusline, stats, refresh modes, and skipped-source commands in `docs/usage.rst`.
- Refreshed README usage sessions examples for period and table options.

## [0.1.0] - 2026-05-12

- Initial release.
