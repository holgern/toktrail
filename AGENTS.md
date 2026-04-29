# AGENTS.md

This file defines how coding agents should work in the `toktrail` repository.

`toktrail` is a Python CLI and library for tracking token usage from coding harnesses into a local SQLite database. Its core contract is: read supported harness source data without mutating it, normalize each billable model response into a durable `UsageEvent`, import events idempotently into a tracking session, and report token/cost breakdowns by session, harness, model, and agent/mode.

## 1. Communication

- Assume the user is technically strong.
- Be direct, concrete, and brief.
- Do not explain obvious Python, Typer, SQLite, dataclass, pytest, ruff, mypy, or packaging basics.
- Do not narrate trivial edits.
- Push back when a request would weaken token accounting, deduplication, source traceability, SQLite invariants, CLI contracts, or privacy defaults.
- Ask a clarifying question only when ambiguity is likely to cause the wrong import behavior, wrong accounting, or a breaking public contract.
- Otherwise, proceed with the smallest correct change.
- Report results as: changed, verified, not verified, risks.

## 2. Operating Principles

### 2.1 Prefer the smallest correct change

Priorities:

1. token accounting is correct
2. imports are idempotent
3. source data remains read-only
4. behavior is verified with focused tests
5. intent is obvious in code
6. changes stay in the owning layer
7. CLI and machine-readable contracts stay stable unless explicitly changed

Avoid:

- speculative abstractions without a near-term harness need
- broad rewrites during feature work
- unrelated formatting or cleanup
- casual command, flag, status, field, table, or JSON shape changes
- changing token semantics to make a report look nicer
- hiding skipped rows or malformed source data in tests
- storing secrets or full raw source content in output
- adding network calls, daemons, or cloud sync unless explicitly requested
- creating commits

### 2.2 Treat toktrail state as product data

Preserve these invariants:

- The toktrail SQLite database is durable local product state.
- Supported source databases and JSONL files are read-only inputs.
- `tracking_sessions` represent user-visible tracking windows.
- `harness_sessions` link imported source sessions to tracking sessions.
- `usage_events` are the normalized accounting ledger.
- Imports must be idempotent across repeated `import` and `watch` runs.
- Raw source JSON is local debugging data only and must not be printed by default.
- `--no-raw` must suppress raw source storage for import and watch paths.
- Cost values come from source data or explicit future pricing code. Do not invent estimates.
- Schema migrations must be explicit, versioned, and tested.

### 2.3 Work as a verifiable loop

For each task:

1. identify the owned layer
2. make the smallest coherent change
3. add or update focused tests
4. run the narrowest useful verification
5. widen verification only when the change crosses layers

Examples:

- parser bug -> the relevant `toktrail/adapters/*.py` module plus parser tests
- token total bug -> `toktrail/models.py`, parser normalization, or `toktrail/db.py` plus accounting tests
- import/watch behavior -> `toktrail/cli.py` and `toktrail/db.py` plus CLI and DB tests
- session report bug -> `toktrail/reporting.py`, `toktrail/db.py`, and CLI status tests
- path resolution bug -> `toktrail/paths.py` plus CLI/path tests
- command grammar change -> `toktrail/cli.py`, README, and CLI contract tests
- provider inference bug -> `toktrail/provider_identity.py` plus provider tests

## 3. Project Shape

### 3.1 What toktrail is

`toktrail` provides:

- a Typer CLI named `toktrail`
- local SQLite state under `~/.local/state/toktrail/toktrail.db` by default
- imports from OpenCode SQLite, Pi JSONL sessions, and GitHub Copilot CLI OTEL JSONL
- watch commands that repeatedly import new usage
- source-session inspection commands
- normalized token breakdowns for input, output, reasoning, cache-read, and cache-write tokens
- summaries by tracking session, harness, model, and agent/mode
- optional raw source JSON storage for local reprocessing and debugging

Canonical workflow:

```text
init -> start -> import/watch -> status -> stop
```

This workflow is the product contract, not decoration.

### 3.2 Important code surfaces

Use the owning layer before editing.

- `toktrail/cli.py` — Typer app, command groups, import/watch flows, human and JSON output
- `toktrail/db.py` — SQLite connection, migrations, session lifecycle, harness-session linking, event insertion, summaries
- `toktrail/models.py` — canonical dataclasses for token and event semantics
- `toktrail/reporting.py` — report dataclasses and machine-readable report shape
- `toktrail/paths.py` — default paths and environment-variable overrides
- `toktrail/provider_identity.py` — conservative provider inference from model IDs
- `toktrail/adapters/base.py` — adapter protocol
- `toktrail/adapters/opencode.py` — OpenCode SQLite scanning, parsing, session summaries
- `toktrail/adapters/pi.py` — Pi JSONL path/file scanning, parsing, session summaries
- `toktrail/adapters/copilot.py` — Copilot OTEL JSONL scanning and parsing
- `tests/test_cli.py` — CLI behavior and end-to-end import/status contracts
- `tests/test_db.py` — SQLite state and aggregation contracts
- `tests/test_opencode_parser.py` — OpenCode parser contracts
- `tests/test_pi_parser.py` — Pi parser contracts
- `tests/test_copilot_parser.py` — Copilot parser contracts
- `tests/test_provider_identity.py` — provider inference contracts
- `README.md` — user-facing command and behavior documentation
- `pyproject.toml` — packaging, entrypoint, pytest, ruff, mypy configuration

## 4. Harness Adapter Contract

### 4.1 Current harnesses

Supported harnesses are:

- `opencode` — reads OpenCode SQLite `message` rows
- `pi` — reads Pi session JSONL files under a sessions directory or a single JSONL file
- `copilot` — reads GitHub Copilot CLI OTEL JSONL chat spans

Each harness adapter must convert source rows/spans/messages into `UsageEvent` objects with the same semantics.

### 4.2 Adapter invariants

Preserve:

- source inputs are opened read-only where the backing format supports it
- malformed source records are skipped, not fatal, unless the requested source path itself is invalid
- unsupported source record types are skipped
- non-assistant or non-chat records are skipped unless the harness contract changes explicitly
- token counts are non-negative integers
- cache-read and cache-write tokens remain distinct from normal input tokens where the source exposes them
- timestamps are milliseconds since epoch
- missing or invalid timestamps use a deterministic fallback already established by that adapter
- `source_session_id` is stable and filterable with `--source-session`
- `source_row_id`, `source_message_id`, `source_dedup_key`, and `global_dedup_key` remain traceable to the source
- `fingerprint_hash` changes when accounting-relevant source fields change
- `raw_json` is stored only when raw storage is enabled
- parser functions return an empty list/result for missing optional source files where that is the established parser contract

### 4.3 Adding a harness

When adding a harness:

1. add a dedicated adapter module under `toktrail/adapters/`
2. produce `UsageEvent` objects, not harness-specific storage rows
3. add path resolution in `toktrail/paths.py` only if the harness needs a default path or environment variable
4. add import/watch/source-session inspection through a shared harness registry if the change touches more than one harness command
5. avoid copying `_run_*_import`, `watch_*`, and `*_sessions` logic yet again
6. add parser tests, CLI tests, and DB/report tests when the harness affects aggregation
7. update README command examples

The repository already has three harnesses. Favor a small registry or strategy table over a fourth round of copy-pasted CLI functions.

## 5. Token Accounting Contracts

### 5.1 Canonical token fields

The canonical token breakdown is:

```text
input
output
reasoning
cache_read
cache_write
total = input + output + reasoning + cache_read + cache_write
```

Preserve this shape in dataclasses, SQLite rows, JSON output, and human reports.

### 5.2 Semantics

- `input` is non-cached prompt/input tokens after adapter-specific normalization.
- `output` is generated response tokens.
- `reasoning` is reasoning/output reasoning tokens when the source exposes them.
- `cache_read` is cached input reused by the provider/model.
- `cache_write` is input written into cache.
- `total` includes cache tokens because it is a usage total, not just billable fresh tokens.
- Do not collapse cache tokens into input just to simplify reporting.
- Do not drop cache-only events.
- Do not infer missing output or reasoning tokens from total unless the source contract explicitly supports that.

### 5.3 Provider, model, and agent identity

Preserve:

- `model_id` comes from the source when available.
- `provider_id` comes from the source or conservative model-name inference.
- unknown providers remain unknown or harness-specific fallback values.
- OpenCode agent/mode normalization prefers mode over agent where that is the established behavior.
- reports keep model and agent/mode grouping stable.

## 6. SQLite Storage Contracts

### 6.1 State database

Preserve:

- local SQLite as the only state backend unless explicitly requested
- `PRAGMA foreign_keys = ON`
- WAL mode and normal synchronous mode unless there is a measured reason to change them
- `SCHEMA_VERSION` and `PRAGMA user_version` checks
- rejection of unsupported future schema versions
- idempotent migrations
- explicit tests for any schema change

### 6.2 Tables

The core tables are:

- `tracking_sessions`
- `harness_sessions`
- `usage_events`

Preserve:

- tracking session start/end timestamps
- active-session behavior
- harness/source-session linking
- first/last seen ranges for harness sessions
- one normalized usage-event row per imported source event
- unique/import-idempotence behavior based on stable dedup keys
- raw JSON column privacy semantics

### 6.3 Import idempotence

Repeated imports must not duplicate accounting.

Rules:

- preserve `global_dedup_key` uniqueness semantics
- keep source-specific dedup keys stable across parser changes
- preserve fingerprint hashing for accounting-relevant fields
- when dedup semantics change, write migration/test coverage for old and new data
- count skipped rows clearly in import/watch output

## 7. CLI Contract

### 7.1 Current command families

Preserve these command families unless a task explicitly changes grammar:

```text
toktrail init
toktrail start
toktrail stop
toktrail status
toktrail sessions
toktrail import opencode
toktrail import pi
toktrail import copilot
toktrail watch opencode
toktrail watch pi
toktrail watch copilot
toktrail opencode sessions
toktrail pi sessions
```

Global `--db` is the state database override. Environment variables may also resolve paths.

### 7.2 Desired session-inspection direction

The current source-session inspection grammar is harness-first for OpenCode and Pi:

```text
toktrail opencode sessions
toktrail pi sessions
```

The preferred future grammar is session-first and complete across harnesses:

```text
toktrail sessions opencode
toktrail sessions pi
toktrail sessions copilot
```

When implementing this:

- keep existing harness-first commands as compatibility aliases unless the user explicitly asks for a breaking cleanup
- add Copilot source-session inspection rather than leaving it missing
- avoid duplicating per-harness rendering logic
- ensure human output includes readable timestamps, not raw epoch milliseconds only
- add CLI tests for both old and new spellings during the transition
- update README examples in the same change

### 7.3 Active tracking session defaulting

Import and watch commands should default to the active tracking session.

Rules:

- use `--session` for explicit tracking-session override
- no active session should produce a clear error
- missing requested tracking session should produce a clear error
- `--since-start` filters source events by the selected tracking session start time
- active session behavior must be tested for all harnesses

### 7.4 Human output

Human output should be concise and stable.

Preserve:

- import result labels: source path, tracking session, rows seen, rows imported, rows skipped
- status sections for totals and grouped summaries
- cost formatting as dollars unless intentionally changed
- token formatting as readable integers
- no raw JSON in CLI output

For session listings and session-detail commands, prefer:

- ISO-like local or UTC readable times plus raw IDs where useful
- token columns split into input, output, reasoning, cache-read, cache-write, total
- model/provider information when available
- harness and source session IDs
- stable column labels that tests can assert

### 7.5 JSON output

`toktrail status --json` is a machine-readable contract.

When touching JSON:

- preserve existing top-level keys unless explicitly changing the contract
- preserve token field names: `input`, `output`, `reasoning`, `cache_read`, `cache_write`, `total`
- preserve report grouping shapes for harness, model, and agent summaries
- test payload shape and exit code together
- do not force consumers to parse human text

If adding JSON to additional commands, make the shape explicit and test it.

## 8. Reporting Contracts

`toktrail status` must answer:

- which tracking session is being reported
- total token breakdown
- total cost
- usage by harness
- usage by model
- usage by agent/mode

A source-session detail command should answer:

- which harness/source session is being reported
- first and last message times in readable form
- assistant/message count
- token breakdown by input, output, reasoning, cache-read, cache-write, and total
- model and provider breakdown
- agent/mode breakdown where available
- cost where source data provides it

Do not compute reports in the CLI by re-parsing source files after import when the state database can answer the question.

## 9. Paths and Privacy

Preserve default path behavior:

- toktrail state: `~/.local/state/toktrail/toktrail.db`, or `$XDG_STATE_HOME/toktrail/toktrail.db`
- state override: `TOKTRAIL_DB` or global `--db`
- OpenCode default source: `~/.local/share/opencode/opencode.db`
- Pi default source: `~/.pi/agent/sessions`, with `TOKTRAIL_PI_SESSIONS` override
- Copilot source: explicit `--copilot-file` or `TOKTRAIL_COPILOT_FILE`

Privacy rules:

- never modify source harness data
- never print raw OpenCode, Pi, or Copilot JSON by default
- keep raw JSON local to SQLite only when enabled
- make `--no-raw` work for every import/watch path
- do not add telemetry, network sync, or external pricing lookups unless explicitly requested

## 10. Docs and Packaging Rules

When changing commands, workflows, storage, or reports, update as needed:

- `README.md`
- command examples in tests
- parser or DB tests that document behavior
- `pyproject.toml` only when packaging, dependencies, tooling, or entrypoints actually change

Packaging rules:

- keep `toktrail.py.typed`
- keep the console entrypoint `toktrail = "toktrail.cli:cli_main"`
- avoid new runtime dependencies unless explicitly justified
- do not package generated context files or local task state
- do not edit generated `toktrail/_version.py` by hand unless the task is specifically about version-file handling

## 11. Testing Expectations

### 11.1 Minimum rule

Every non-trivial behavior change needs verification.

Prefer the closest tests:

- OpenCode parser change -> `tests/test_opencode_parser.py`
- Pi parser change -> `tests/test_pi_parser.py`
- Copilot parser change -> `tests/test_copilot_parser.py`
- DB schema/import/aggregation change -> `tests/test_db.py`
- CLI command or output change -> `tests/test_cli.py`
- provider inference change -> `tests/test_provider_identity.py`
- README command drift -> CLI tests plus README update

### 11.2 Regression paths to test

Include error paths when relevant:

- no active tracking session
- missing tracking session from `--session`
- missing OpenCode database
- missing Pi sessions path
- missing Copilot file and missing `TOKTRAIL_COPILOT_FILE`
- malformed JSON/JSONL rows
- non-assistant or non-chat source records
- missing usage or model data
- negative token values
- cache-only events
- duplicate source rows
- repeated import idempotence
- `--source-session` filtering
- `--since-start` filtering
- `--no-raw` storage behavior
- invalid or future SQLite schema version
- JSON and human output modes

### 11.3 Verification command progression

Start narrow. Expand only when needed.

```bash
python -m pip install -e .
python -m pip install -e ".[dev]"

pytest tests/test_opencode_parser.py
pytest tests/test_pi_parser.py
pytest tests/test_copilot_parser.py
pytest tests/test_db.py
pytest tests/test_cli.py
pytest tests/test_provider_identity.py
pytest

ruff check --config=.ruff.toml .
ruff format --check .
mypy toktrail
```

Run `ruff check` when touching Python code.
Run `mypy toktrail` when changing typed public or core logic.
Run CLI tests when changing command grammar, output, path resolution, import/watch behavior, or status reporting.
Run parser tests when changing adapter behavior.
Run DB tests when changing schema, migrations, deduplication, insertion, or summaries.

## 12. Code Style

- Follow existing style first.
- Keep functions focused.
- Prefer explicit names over clever compression.
- Add type hints for new or changed public functions.
- Keep dataclasses frozen where current models are frozen.
- Keep public exception and exit-code behavior stable.
- Avoid new dependencies unless explicitly requested.
- Do not reformat unrelated files.
- Do not rename public symbols without a strong reason.
- Do not use git commands that create commits or rewrite history.

## 13. Good Agent Work

A strong change usually:

- edits the owning layer
- preserves read-only source handling
- preserves token breakdown semantics
- preserves idempotent imports
- preserves active tracking-session behavior
- preserves human and JSON output contracts
- keeps harness-specific parsing inside adapters
- removes duplicated CLI harness logic only when it is part of the requested change
- updates README/examples when commands change
- adds focused tests
- runs targeted verification first
- states what was not verified

## 14. Avoid

- CLI-only patches for parser or DB bugs
- parser changes without fixture-like tests
- changing token totals without updating all report paths
- hiding cache tokens inside input tokens
- dropping cache-only events
- silently changing dedup keys
- silently changing SQLite schema without migration tests
- printing raw source JSON
- mutating source harness databases or JSONL files
- leaving Copilot behind when generalizing source-session commands
- broad style churn
- mixing refactors with behavior changes unless the task explicitly requires it
