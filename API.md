# toktrail stable Python API

`toktrail` exposes a stable public Python API for automation from the
`toktrail.api` root facade, while preserving the documented `toktrail.api.*`
submodules and canonical errors in `toktrail.errors`.

Downstream code should use this surface instead of importing `toktrail.db`,
`toktrail.models`, `toktrail.reporting`, `toktrail.paths`, `toktrail.config`,
`toktrail.cli`, or `toktrail.adapters.*`.

## Import boundary

Preferred public imports:

```python
from toktrail.errors import ToktrailError

from toktrail.api import (
    ActivitySummaryRow,
    CostTotals,
    FinalizedManualRun,
    HarnessDefinition,
    HarnessEnvironment,
    HarnessSummaryRow,
    ImportUsageResult,
    ModelSummaryRow,
    PreparedManualRun,
    ScanUsageResult,
    SessionTotals,
    SourceSessionDiff,
    SourceSessionSnapshot,
    SourceSessionSummary,
    StateExportResult,
    StateImportConflict,
    StateImportResult,
    TokenBreakdown,
    RunScope,
    Run,
    RunReport,
    UnconfiguredModelRow,
    UsageEvent,
)

from toktrail.api.config import (
    config_exists,
    config_summary,
    init_config,
    render_config_template,
)
from toktrail.api.harnesses import (
    get_harness_definition,
    is_supported_harness,
    normalize_harness_name,
    supported_harnesses,
)
from toktrail.api.paths import (
    default_amp_threads_path,
    default_codex_sessions_path,
    default_droid_sessions_path,
    default_vibe_logs_path,
    default_source_path,
    default_toktrail_config_path,
    default_toktrail_db_path,
    resolve_source_path,
    resolve_toktrail_config_path,
    resolve_toktrail_db_path,
)
from toktrail.api.sessions import (
    archive_run,
    get_active_run,
    get_run,
    init_state,
    list_runs,
    require_active_run,
    start_run,
    stop_run,
    unarchive_run,
)
from toktrail.api.sources import (
    capture_source_snapshot,
    diff_source_snapshots,
    list_source_sessions,
    scan_usage,
)
from toktrail.api.imports import import_configured_usage, import_usage
from toktrail.api.sync import default_archive_name, export_state_archive, import_state_archive
from toktrail.api.reports import session_report, subscription_usage_report, usage_report
from toktrail.api.environment import prepare_environment
from toktrail.api.workflow import finalize_manual_run, prepare_manual_run
```

The root facade is the preferred import style. The documented submodules remain
valid for callers that want narrower imports.

## Public modules

- `toktrail.errors`
- `toktrail.api`
- `toktrail.api.models`
- `toktrail.api.paths`
- `toktrail.api.config`
- `toktrail.api.harnesses`
- `toktrail.api.sessions`
- `toktrail.api.sources`
- `toktrail.api.imports`
- `toktrail.api.sync`
- `toktrail.api.reports`
- `toktrail.api.environment`
- `toktrail.api.workflow`

All toktrail-state-database-bound functions take `db_path: Path | None` as the
first positional argument. `db_path=None` means use toktrail's default path
resolution rules.

Public functions never print, never parse CLI arguments, never call `sys.exit`
or `typer.Exit`, and return dataclasses or plain values.

Supported harness names across the public API are `opencode`, `pi`, `copilot`,
`codex`, `goose`, `droid`, `amp`, `claude`, and `vibe`.

Runnable examples for manually measuring OpenCode, Pi, Copilot, Codex, Goose,
Droid, Amp, Claude, and Vibe runs are documented in
[`docs/stable_api_examples.md`](docs/stable_api_examples.md).

## Models

Key public models:

- `TokenBreakdown`: `input`, `output`, `reasoning`, `cache_read`,
  `cache_write`, `total`
- `CostTotals`: `source_cost_usd`, `actual_cost_usd`, `virtual_cost_usd`,
  `savings_usd`, `unpriced_count`
- `Run`: includes local integer `id`, durable cross-machine `sync_id`,
  `started_at_ms` / `ended_at_ms`, persisted `scope`, and `archived_at_ms`
- `RunScope`: persisted run membership filters (`harnesses`, `provider_ids`,
  `model_ids`, `source_session_ids`, `thinking_levels`, `agents`)
- `SourceSessionSummary`, `SourceSessionSnapshot`, `SourceSessionDiff`
- `ImportUsageResult`
- `RunReport`
- `UnconfiguredModelRow`
- `HarnessEnvironment`
- `PreparedManualRun`, `FinalizedManualRun`

`UsageEvent` and `ModelSummaryRow` expose `thinking_level` when the source
harness provides it. This is reporting metadata only; pricing still keys on
provider and model identity.

`RunReport` now includes `by_provider` and `unconfigured_models` so callers can audit provider-level usage and missing pricing rows.

All public dataclasses are frozen.

## Errors

Canonical public errors:

- `ToktrailError`
- `StateDatabaseError`
- `UnsupportedHarnessError`
- `SourcePathError`
- `ConfigurationError`
- `RunNotFoundError`
- `NoActiveRunError`
- `ActiveRunExistsError`
- `RunAlreadyEndedError`
- `UsageImportError`
- `AmbiguousSourceSessionError`
- `InvalidAPIUsageError`

## Privacy defaults

- Public APIs do not expose raw source JSON by default.
- `scan_usage(..., include_raw_json=False)` returns public `UsageEvent`s with
  `raw_json=None`.
- `import_usage(..., include_raw_json=False)` stores no raw JSON in toktrail
  state.

Raw JSON is opt-in only.

## Costs

Public reporting preserves the current cost model:

- `source_cost_usd`
- `actual_cost_usd`
- `virtual_cost_usd`
- `savings_usd = virtual_cost_usd - actual_cost_usd`
- `unpriced_count`

The API does not collapse these into a single cost field.

Report and source-summary APIs accept `config_path`. The runtime loader merges
`config.toml` with sibling `prices.toml` and `subscriptions.toml` when present.

Provider identity is strict: when a usage event already has an explicit provider,
toktrail does not fall back to inferred provider aliases from the model name.

Pricing rows can include context-tier metadata
(`context_min_tokens`, `context_max_tokens`, `context_label`,
`context_basis="prompt_like"`). Tier resolution is per usage event using
prompt-like tokens (`input + cache_read + cache_write`).

`session_cache_analysis()` call rows expose context/tier selection metadata and
missing price kinds through `CacheCallRow`.

## Session and import APIs

```python
from pathlib import Path

from toktrail.api.imports import import_configured_usage, import_usage
from toktrail.api.sources import capture_source_snapshot, list_source_sessions
from toktrail.api.reports import session_report, subscription_usage_report, usage_report
from toktrail.api.sessions import init_state, start_run

db_path = Path(".toktrail/toktrail.db")
source_path = Path("~/.codex/sessions").expanduser()

init_state(db_path)
snapshot = capture_source_snapshot("codex", source_path=source_path)
session = start_run(
    db_path,
    name="benchmark-1",
    scope=RunScope(harnesses=("codex",), provider_ids=("openai",)),
)
result = import_usage(db_path, "codex", session_id=session.id, source_path=source_path)
source_sessions = list_source_sessions("codex", source_path=source_path, limit=5)
report = session_report(db_path, session.id)
window = usage_report(db_path, period="today", timezone="UTC")
quotas = subscription_usage_report(db_path, provider_id="opencode-go")
archived = archive_run(db_path, session.id)
restored = unarchive_run(db_path, session.id)
```

`import_usage()` can import canonical usage rows without an active session. A
later import into a specific tracking session links existing canonical rows to
that session idempotently instead of duplicating them.

`capture_source_snapshot()` and `list_source_sessions()` use the same supported
harness set, so Codex, Goose, Droid, and Amp source sessions can be inspected
before import with the same public API used for OpenCode, Pi, and Copilot.

`usage_report()` no longer requires `session_id`. When used for canonical
period/time-range reporting, it returns `RunReport(session=None, ...)`.

### Subscription usage report

`subscription_usage_report()` returns subscription quota usage for configured
`[[subscriptions]]` from `subscriptions.toml` (keyed by `id` with
`usage_providers` coverage), including
configured windows (`5h`, `daily`, `weekly`, `monthly`, `yearly`) with
per-window `reset_mode` (`fixed` or `first_use`), `reset_at`, status
(`active`, `waiting_for_first_use`, `expired_waiting_for_next_use`), and
used/remaining/over-limit cost values based on each subscription
`quota_cost_basis` (`source`, `actual`, or `virtual`).

When configured, subscription rows also expose a `billing` object with
`fixed_cost_usd`, `value_usd`, `billing_basis`, `net_savings_usd`, and
break-even metrics.

### Sync archive API

Use `toktrail.api.sync` to export/import normalized toktrail state archives:

```python
from pathlib import Path

from toktrail.api.sync import default_archive_name, export_state_archive, import_state_archive

archive = Path(default_archive_name())
exported = export_state_archive(Path(".toktrail/toktrail.db"), archive)
preview = import_state_archive(Path(".toktrail/other.db"), archive, dry_run=True)
```

Import validates archive member paths, manifest checksums, schema version, and
usage-event fingerprint conflicts before merge.

### Configured import

Use `import_configured_usage()` when the caller wants the same behavior as plain
`toktrail refresh`: read enabled harnesses from `[imports].harnesses`, source
paths from `[imports.sources]` (each configured value may be a string or list
of strings), raw JSON behavior from `[imports].include_raw_json`, and
missing-source handling from
`[imports].missing_source`.

```python
from pathlib import Path

from toktrail.api.imports import import_configured_usage

results = import_configured_usage(
    Path(".toktrail/toktrail.db"),
    config_path=Path("~/.config/toktrail/config.toml").expanduser(),
    use_active_session=False,
)
```

Use `import_usage()` when the caller already selected one harness and source
path.

## Manual workflow API

This is the preferred public integration point for the before/after manual-agent
workflow:

```python
from pathlib import Path

from toktrail.api.workflow import finalize_manual_run, prepare_manual_run

prepared = prepare_manual_run(
    Path(".toktrail/solvecost.db"),
    "codex",
    name="solvecost:benchmark-1:problem-001:attempt-1",
    source_path=Path("~/.codex/sessions").expanduser(),
)

# The caller asks the user to run the agent manually here.

finalized = finalize_manual_run(
    Path(".toktrail/solvecost.db"),
    prepared,
    include_raw_json=False,
    stop_after_run=True,
)

record = {
    "toktrail_run_id": finalized.run.id,
    "harness": finalized.source_session.harness,
    "source_session_id": finalized.source_session.source_session_id,
    "tokens": finalized.report.totals.tokens.as_dict(),
    "costs": finalized.report.totals.costs.as_dict(),
}
```

For Copilot, use `prepare_environment()` or `prepare_manual_run(..., shell="nu")`
to get the OTEL environment variables and shell exports without launching any
processes. For Codex, `prepare_environment("codex", ...)` returns the selected
source path with an empty environment because Codex writes session logs
natively.
