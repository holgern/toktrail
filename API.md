# toktrail stable Python API

`toktrail` exposes a stable public Python API for automation under `toktrail.api.*`
plus canonical errors in `toktrail.errors`.

Downstream code should use this surface instead of importing `toktrail.db`,
`toktrail.models`, `toktrail.reporting`, `toktrail.paths`, `toktrail.config`,
`toktrail.cli`, or `toktrail.adapters.*`.

## Import boundary

Supported public imports:

```python
from toktrail.errors import ToktrailError

from toktrail.api.models import (
    AgentSummaryRow,
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
    TokenBreakdown,
    TrackingSession,
    TrackingSessionReport,
    UnconfiguredModelRow,
    UsageEvent,
)

from toktrail.api.sessions import (
    get_active_session,
    get_session,
    init_state,
    list_sessions,
    require_active_session,
    start_session,
    stop_session,
)
from toktrail.api.sources import (
    capture_source_snapshot,
    diff_source_snapshots,
    list_source_sessions,
    scan_usage,
)
from toktrail.api.imports import import_usage
from toktrail.api.reports import session_report, usage_report
from toktrail.api.environment import prepare_environment
from toktrail.api.workflow import finalize_manual_run, prepare_manual_run
```

## Public modules

- `toktrail.errors`
- `toktrail.api.models`
- `toktrail.api.paths`
- `toktrail.api.config`
- `toktrail.api.harnesses`
- `toktrail.api.sessions`
- `toktrail.api.sources`
- `toktrail.api.imports`
- `toktrail.api.reports`
- `toktrail.api.environment`
- `toktrail.api.workflow`

All toktrail-state-database-bound functions take `db_path: Path | None` as the
first positional argument. `db_path=None` means use toktrail's default path
resolution rules.

Public functions never print, never parse CLI arguments, never call `sys.exit`
or `typer.Exit`, and return dataclasses or plain values.

Supported harness names across the public API are `opencode`, `pi`, `codex`,
and `copilot`.

## Models

Key public models:

- `TokenBreakdown`: `input`, `output`, `reasoning`, `cache_read`,
  `cache_write`, `total`
- `CostTotals`: `source_cost_usd`, `actual_cost_usd`, `virtual_cost_usd`,
  `savings_usd`, `unpriced_count`
- `TrackingSession`: uses `started_at_ms` / `ended_at_ms` as the primary field
  names
- `SourceSessionSummary`, `SourceSessionSnapshot`, `SourceSessionDiff`
- `ImportUsageResult`
- `TrackingSessionReport`
- `UnconfiguredModelRow`
- `HarnessEnvironment`
- `PreparedManualRun`, `FinalizedManualRun`

`UsageEvent` and `ModelSummaryRow` expose `thinking_level` when the source
harness provides it. This is reporting metadata only; pricing still keys on
provider and model identity.

`TrackingSessionReport` now includes `unconfigured_models` so callers can audit
which harness/provider/model combinations still need configured pricing rows.

All public dataclasses are frozen.

## Errors

Canonical public errors:

- `ToktrailError`
- `StateDatabaseError`
- `UnsupportedHarnessError`
- `SourcePathError`
- `ConfigurationError`
- `SessionNotFoundError`
- `NoActiveSessionError`
- `ActiveSessionExistsError`
- `SessionAlreadyEndedError`
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

Report and source-summary APIs accept `config_path` so callers can choose the
pricing config used for actual and virtual costs.

Provider identity is strict: when a usage event already has an explicit provider,
toktrail does not fall back to inferred provider aliases from the model name.

## Session and import APIs

```python
from pathlib import Path

from toktrail.api.imports import import_usage
from toktrail.api.sources import capture_source_snapshot, list_source_sessions
from toktrail.api.reports import session_report, usage_report
from toktrail.api.sessions import init_state, start_session

db_path = Path(".toktrail/toktrail.db")
source_path = Path("~/.codex/sessions").expanduser()

init_state(db_path)
snapshot = capture_source_snapshot("codex", source_path=source_path)
session = start_session(db_path, name="benchmark-1")
result = import_usage(db_path, "codex", session_id=session.id, source_path=source_path)
source_sessions = list_source_sessions("codex", source_path=source_path, limit=5)
report = session_report(db_path, session.id)
window = usage_report(db_path, period="today", timezone="UTC")
```

`import_usage()` can import canonical usage rows without an active session. A
later import into a specific tracking session links existing canonical rows to
that session idempotently instead of duplicating them.

`capture_source_snapshot()` and `list_source_sessions()` use the same supported
harness set, so Codex source sessions can be inspected before import with the
same public API used for OpenCode, Pi, and Copilot.

`usage_report()` no longer requires `session_id`. When used for canonical
period/time-range reporting, it returns `TrackingSessionReport(session=None, ...)`.

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
    stop_session=True,
)

record = {
    "toktrail_session_id": finalized.tracking_session.id,
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
