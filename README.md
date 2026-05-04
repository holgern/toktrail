# toktrail

`toktrail` is a Python CLI for tracking OpenCode, Pi, Codex, Goose, Droid, Amp, Vibe,
and GitHub Copilot CLI token usage inside a local toktrail SQLite database.

The first implementation focuses on:

- OpenCode SQLite, Pi JSONL sessions, Codex JSONL sessions, Goose SQLite
  sessions, Droid settings JSON sessions, Amp thread JSON sessions, Vibe session logs, and GitHub
  Copilot CLI OTEL JSONL as supported source harnesses
- local SQLite for both the OpenCode source database and toktrail state
- reporting totals by tracking session, harness, model, and agent/mode

## Requirements

- Python 3.10 or newer
- an OpenCode SQLite database, typically at
  `~/.local/share/opencode/opencode.db`, and/or
- Pi session JSONL files, typically under `~/.pi/agent/sessions`, and/or
- Codex session JSONL files, typically under `~/.codex/sessions`, and/or
- Goose SQLite sessions, typically at
  `~/.local/share/goose/sessions/sessions.db`, and/or
- Droid settings JSON sessions, typically under `~/.factory/sessions`, and/or
- Amp thread JSON sessions, typically under `~/.local/share/amp/threads`, and/or
- Vibe session logs, typically under `~/.vibe/logs/session`, and/or
- Claude Code project transcripts, typically under `~/.claude/projects`, and/or
- GitHub Copilot CLI OTEL JSONL export files, typically under `~/.copilot/otel`

toktrail reads supported source data in read-only mode and does not modify the
source database or source JSONL files.

## Install

```bash
python -m pip install -e .
```

For development:

```bash
python -m pip install -e ".[dev]"
```

## Public Python API

Automation should prefer the stable Python API in `toktrail.api.*` instead of
importing internals like `toktrail.db` or `toktrail.adapters.*`.

```python
from pathlib import Path

from toktrail.api.imports import import_usage
from toktrail.api.reports import session_report, subscription_usage_report, usage_report
from toktrail.api.sessions import init_state, start_run

db_path = Path(".toktrail/toktrail.db")
source_path = Path("tests/fixtures/opencode.db")

init_state(db_path)
import_usage(db_path, "opencode", source_path=source_path)
run = start_run(db_path, name="benchmark-run")
import_usage(db_path, "opencode", session_id=run.id, source_path=source_path)
session_usage = session_report(db_path, session.id)
today_usage = usage_report(db_path, period="today", timezone="UTC")
subscription_usage = subscription_usage_report(db_path, provider_id="opencode-go")
```

See [`API.md`](API.md) for the stable import boundary, public models, workflow
API, canonical errors, and privacy defaults. Runnable manual-run examples for
OpenCode, Pi, Copilot, Codex, Goose, Droid, Amp, and Vibe are documented in
[`docs/stable_api_examples.md`](docs/stable_api_examples.md).

## Quickstart

Initialize the toktrail state database:

```bash
toktrail init
```

Start a tracking session:

```bash
toktrail run start --name refactor-auth-flow
```

Refresh usage from config or a single harness:

```bash
toktrail config init
toktrail refresh
toktrail refresh --harness codex --source ~/.codex/sessions
toktrail refresh --harness amp --source ~/.local/share/amp/threads
toktrail refresh --harness claude --source ~/.claude/projects
toktrail refresh --dry-run
toktrail refresh --no-session
```

For local acceptance and testing, the repository includes a sample OpenCode
source database at `tests/fixtures/opencode.db`:

```bash
toktrail refresh --harness opencode --source tests/fixtures/opencode.db
```

Show the current session totals:

```bash
toktrail run status
toktrail run status --json
toktrail run status --thinking high --json
toktrail run status --split-thinking
toktrail run status --price-state unpriced --sort tokens --limit 20
toktrail --config ~/.config/toktrail/config.toml run status --json
toktrail run status --harness pi --source-session pi_ses_001 --json
```

Show period-based usage across canonical ledger rows, even without an active
tracking session:

```bash
toktrail usage today
toktrail usage last-week --utc --json
toktrail usage --since 2026-05-01 --until 2026-06-01 --timezone Europe/Berlin
toktrail usage --price-state priced --sort provider --limit 10 --json
toktrail usage today --no-refresh
toktrail usage today --refresh-details
toktrail subscriptions
toktrail subscriptions --provider opencode-go --json
toktrail sync export --out toktrail-state.tar.gz --no-refresh
toktrail sync import toktrail-state.tar.gz
toktrail sync import toktrail-state.tar.gz --dry-run --json
```

Stop the active tracking session:

```bash
toktrail run stop
```

## Command model

The canonical CLI flow is:

```bash
toktrail init
toktrail config init
toktrail sources
toktrail run start --name <name>
toktrail refresh
toktrail run status
toktrail usage today
toktrail sessions
toktrail subscriptions
toktrail sync export --out toktrail-state.tar.gz
toktrail run stop
```

Report commands (`toktrail usage`, `toktrail run status`, and
`toktrail subscriptions`) refresh configured sources first by default. Use
`--no-refresh` for stale local-state reads, and `--refresh-details` to print a
compact refresh summary.

`toktrail sync import` validates archive paths, manifest checksums, schema
version, and usage-event fingerprints before merging.

Use `toktrail refresh` for explicit/manual refresh operations. It reads enabled
harnesses and source paths from `config.toml`:

```toml
[imports]
harnesses = ["opencode", "pi", "copilot", "codex", "goose", "droid", "amp", "claude", "vibe"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = ["~/.local/share/opencode/opencode.db", "~/.local/share/opencode/opencode-stable.db"]
pi = ["~/.pi/agent/sessions", "~/.omp/agent/sessions"]
copilot = "~/.copilot/otel"
codex = ["~/.codex/sessions", "~/.codex/archived_sessions"]
goose = "~/.local/share/goose/sessions/sessions.db"
droid = "~/.factory/sessions"
amp = "~/.local/share/amp/threads"
claude = "~/.claude/projects"
```

```toml
[[subscriptions]]
id = "opencode-go"
usage_providers = ["opencode-go"]
display_name = "OpenCode Go"
timezone = "Europe/Berlin"
quota_cost_basis = "virtual"
fixed_cost_usd = 10.00
fixed_cost_period = "monthly"
fixed_cost_reset_at = "2026-05-01T00:00:00+02:00"
fixed_cost_basis = "virtual"

[[subscriptions.windows]]
period = "5h"
limit_usd = 12
reset_mode = "first_use"
reset_at = "2026-05-01T00:00:00+02:00"

[[subscriptions.windows]]
period = "weekly"
limit_usd = 30
reset_mode = "fixed"
reset_at = "2026-05-01T00:00:00+02:00"

[[subscriptions.windows]]
period = "monthly"
limit_usd = 60
reset_mode = "fixed"
reset_at = "2026-05-01T00:00:00+02:00"
```

```toml

[[subscriptions]]
id = "zai-coding-plan"
usage_providers = ["zai"]
display_name = "Zai Coding Plan"
timezone = "Asia/Singapore"
quota_cost_basis = "virtual"
fixed_cost_usd = 36.00
fixed_cost_period = "yearly"
fixed_cost_reset_at = "2025-09-01T00:00:00+02:00"
fixed_cost_basis = "virtual"

[[subscriptions.windows]]
period = "5h"
limit_usd = 10
reset_mode = "first_use"
reset_at = "2026-05-01T00:00:00+02:00"
```

`imports.sources.<harness>` accepts either a single path string or a list of
paths. Use `toktrail refresh --harness <name> --source <path>` for one-off
refreshes. The pre-release contract does not preserve harness-specific
`refresh`, `watch`, `sessions`, or `env` compatibility subcommands.

## Commands

Initialize or override the toktrail state database:

```bash
toktrail --db /path/to/toktrail.db init
```

Create and manage tracking sessions:

```bash
toktrail run start --name refactor-auth-flow
toktrail run stop
toktrail run stop 3
toktrail sessions
toktrail subscriptions
toktrail sync export --out toktrail-state.tar.gz
toktrail sync import toktrail-state.tar.gz --dry-run --json
toktrail sessions pi
toktrail sessions pi pi_ses_001
```

Discover configured source paths before refreshing:

```bash
toktrail sources
toktrail sources --harness opencode
toktrail sources --harness opencode --source /path/to/opencode.db
toktrail sources --json
```

Inspect and manage pricing config and used model pricing:

```bash
toktrail config init
toktrail config init --template copilot
toktrail config show
toktrail config prices
toktrail config prices --provider openai --sort model
toktrail config prices --query gpt-5 --aliases
toktrail config prices --model gpt-5-mini --json
toktrail pricing list
toktrail pricing list --used-only
toktrail pricing list --missing-only
toktrail config validate
toktrail subscriptions
toktrail --config /path/to/config.toml run status --json
```

Refresh usage:

```bash
toktrail refresh
toktrail refresh --harness opencode --source /path/to/opencode.db
toktrail refresh --harness pi --source ~/.pi/agent/sessions
toktrail refresh --harness codex --source ~/.codex/sessions
toktrail refresh --harness goose --source ~/.local/share/goose/sessions/sessions.db
toktrail refresh --harness droid --source ~/.factory/sessions
toktrail refresh --harness amp --source ~/.local/share/amp/threads
toktrail refresh --dry-run
toktrail refresh --session 3
toktrail refresh --no-session
toktrail refresh --no-raw
```

The plain `toktrail refresh` command reads enabled harnesses and source paths from
`[imports]` and `[imports.sources]` in `config.toml`.

## Advanced: generic refresh, watch, environment, and harness-session flows

Use the generic command surface for every harness:

```bash
toktrail refresh --harness opencode --source /path/to/opencode.db
toktrail refresh --harness pi --source ~/.pi/agent/sessions
toktrail refresh --harness copilot --source ~/.copilot/otel
toktrail refresh --harness codex --source ~/.codex/sessions
toktrail refresh --harness goose --source ~/.local/share/goose/sessions/sessions.db
toktrail refresh --harness droid --source ~/.factory/sessions
toktrail refresh --harness amp --source ~/.local/share/amp/threads
toktrail refresh --harness claude --source ~/.claude/projects
toktrail refresh --harness vibe --source ~/.vibe/logs/session

toktrail watch

toktrail watch --harness opencode
toktrail watch --harness opencode --harness codex

toktrail copilot env bash
toktrail copilot env zsh
toktrail copilot env fish
toktrail copilot env nu
toktrail copilot env powershell

toktrail sessions pi
toktrail sessions codex
toktrail sessions claude
toktrail sessions pi pi_ses_001
toktrail sessions goose goose_session_id

toktrail pricing list
toktrail pricing list --missing-only
```

Copilot source discovery honors `TOKTRAIL_COPILOT_FILE`,
`COPILOT_OTEL_FILE_EXPORTER_PATH`, and `TOKTRAIL_COPILOT_OTEL_DIR`. Codex
discovery honors both `TOKTRAIL_CODEX_SESSIONS` and `CODEX_HOME`, including
archived sessions. Goose discovery honors `TOKTRAIL_GOOSE_SESSIONS` and
`GOOSE_PATH_ROOT`.

## Storage and privacy

By default toktrail stores its own SQLite database at:

```text
~/.local/state/toktrail/toktrail.db
```

If `XDG_STATE_HOME` is set, toktrail uses:

```text
$XDG_STATE_HOME/toktrail/toktrail.db
```

The `TOKTRAIL_DB` environment variable or global `--db` option can override the
toktrail state path.

Pricing config defaults to:

```text
~/.config/toktrail/config.toml
```

If `XDG_CONFIG_HOME` is set, toktrail uses:

```text
$XDG_CONFIG_HOME/toktrail/config.toml
```

The `TOKTRAIL_CONFIG` environment variable or global `--config` option can
override the pricing config path. Missing config files are safe: toktrail falls
back to built-in defaults and still reports source and actual costs.

Usage imports store normalized usage metadata locally. Raw source JSON is
disabled by default and remains opt-in local debugging data only. Use `--raw`
to store raw source payloads for a run, or `--no-raw` to make that choice
explicit in automation.

toktrail never prints raw OpenCode, Pi, Codex, Goose, Droid, Amp, or Copilot JSON in
CLI output.

## Reporting

`toktrail run status` reports:

- total input, output, reasoning, cache-read, and cache-write tokens
- source cost from imported data when the harness provides it
- actual cost based on configured accounting rules
- virtual cost based on configured pricing tables
- savings (`virtual - actual`) plus unpriced model-group counts
- exact unconfigured harness/provider/model diagnostics when pricing is missing
- grouped summaries by harness, model, and agent/mode
- collapsed thinking-level metadata by default, with `--split-thinking` to
  expand model rows when needed
- optional filtered views by harness, source session, provider, model, agent,
  created-at time range, price state, minimum message/token thresholds, sort,
  and grouped-row limits

`toktrail usage` applies the same token and cost reporting to the canonical
ledger without requiring a tracking session. Named periods use half-open
`[since, until)` windows for `today`, `yesterday`, `this-week`, `last-week`,
`this-month`, and `last-month`.

`toktrail run status --json` returns the same information in a machine-readable JSON
shape for automation, including `unconfigured_models` and `display_filters`.

By default:

- OpenCode keeps imported source cost as actual cost
- Pi, Codex, Goose, Droid, and Copilot treat actual cost as `$0.00`
- virtual cost uses configured pricing tables when available

This makes Copilot subscription analysis straightforward: source and actual cost
stay at `$0.00` while virtual cost shows what the same usage would have cost via
public API pricing.

Example workflow:

```bash
toktrail config init --template copilot
toktrail copilot env bash
toktrail refresh --harness copilot --source ~/.copilot/otel/copilot-otel-20260429-090000.jsonl
toktrail run status --price-state unpriced --sort tokens --limit 20
toktrail pricing list --missing-only
toktrail sessions copilot
```

Virtual and pricing-based actual costs are computed at report time, not during
refresh. Updating `config.toml` immediately changes future `status` and
`sessions` output for already imported data without re-importing source files.

Pricing is provider-aware. If an event already has a real provider, toktrail
does not fall back to an inferred provider from the model name. That keeps
identities like `github-copilot/gpt-5.4` and `openai-codex/gpt-5.4` distinct
from `openai/gpt-5.4`.

## Limitations

The first pass intentionally does not include:

- legacy OpenCode JSON file parsing
- JSON migration caches
- background daemons or services
- workspace metadata extraction from Pi session headers
- Copilot tool-span or metric accounting; phase 1 imports chat spans only and
  ignores tools, agent invocations, and metrics
- network sync or cloud storage
- external pricing lookups
- TUI reporting
