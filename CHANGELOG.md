# Changelog

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
