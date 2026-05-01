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
from toktrail.api.reports import session_report, usage_report
from toktrail.api.sessions import init_state, start_session

db_path = Path(".toktrail/toktrail.db")
source_path = Path("tests/fixtures/opencode.db")

init_state(db_path)
import_usage(db_path, "opencode", source_path=source_path)
session = start_session(db_path, name="benchmark-run")
import_usage(db_path, "opencode", session_id=session.id, source_path=source_path)
session_usage = session_report(db_path, session.id)
today_usage = usage_report(db_path, period="today", timezone="UTC")
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
toktrail start --name refactor-auth-flow
```

Import usage from config or a single harness:

```bash
toktrail config init
toktrail import
toktrail import --harness codex --source ~/.codex/sessions
toktrail import --harness amp --source ~/.local/share/amp/threads
toktrail import --harness claude --source ~/.claude/projects
toktrail import --dry-run
toktrail import --no-session
```

For local acceptance and testing, the repository includes a sample OpenCode
source database at `tests/fixtures/opencode.db`:

```bash
toktrail import --harness opencode --source tests/fixtures/opencode.db
```

Show the current session totals:

```bash
toktrail status
toktrail status --json
toktrail status --thinking high --json
toktrail status --split-thinking
toktrail status --price-state unpriced --sort tokens --limit 20
toktrail --config ~/.config/toktrail/config.toml status --json
toktrail status --harness pi --source-session pi_ses_001 --json
```

Show period-based usage across canonical ledger rows, even without an active
tracking session:

```bash
toktrail usage today
toktrail usage last-week --utc --json
toktrail usage --since 2026-05-01 --until 2026-06-01 --timezone Europe/Berlin
toktrail usage --price-state priced --sort provider --limit 10 --json
```

Stop the active tracking session:

```bash
toktrail stop
```

## Command model

The canonical CLI flow is:

```bash
toktrail init
toktrail config init
toktrail sources
toktrail start --name <name>
toktrail import
toktrail status
toktrail usage today
toktrail sessions
toktrail stop
```

Use `toktrail import` for normal operation. It reads enabled harnesses and
source paths from `config.toml`:

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

`imports.sources.<harness>` accepts either a single path string or a list of
paths. Use `toktrail import --harness <name> --source <path>` for one-off
imports. The pre-release contract does not preserve harness-specific
`import`, `watch`, `sessions`, or `env` compatibility subcommands.

## Commands

Initialize or override the toktrail state database:

```bash
toktrail --db /path/to/toktrail.db init
```

Create and manage tracking sessions:

```bash
toktrail start --name refactor-auth-flow
toktrail stop
toktrail stop 3
toktrail sessions
toktrail sessions pi
toktrail sessions pi pi_ses_001
```

Discover configured source paths before importing:

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
toktrail --config /path/to/config.toml status --json
```

Import usage:

```bash
toktrail import
toktrail import --harness opencode --source /path/to/opencode.db
toktrail import --harness pi --source ~/.pi/agent/sessions
toktrail import --harness codex --source ~/.codex/sessions
toktrail import --harness goose --source ~/.local/share/goose/sessions/sessions.db
toktrail import --harness droid --source ~/.factory/sessions
toktrail import --harness amp --source ~/.local/share/amp/threads
toktrail import --dry-run
toktrail import --session 3
toktrail import --no-session
toktrail import --no-raw
```

The plain `toktrail import` command reads enabled harnesses and source paths from
`[imports]` and `[imports.sources]` in `config.toml`.

## Advanced: generic import, watch, environment, and harness-session flows

Use the generic command surface for every harness:

```bash
toktrail import --harness opencode --source /path/to/opencode.db
toktrail import --harness pi --source ~/.pi/agent/sessions
toktrail import --harness copilot --source ~/.copilot/otel
toktrail import --harness codex --source ~/.codex/sessions
toktrail import --harness goose --source ~/.local/share/goose/sessions/sessions.db
toktrail import --harness droid --source ~/.factory/sessions
toktrail import --harness amp --source ~/.local/share/amp/threads
toktrail import --harness claude --source ~/.claude/projects
toktrail import --harness vibe --source ~/.vibe/logs/session

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

`toktrail status` reports:

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

`toktrail status --json` returns the same information in a machine-readable JSON
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
toktrail import --harness copilot --source ~/.copilot/otel/copilot-otel-20260429-090000.jsonl
toktrail status --price-state unpriced --sort tokens --limit 20
toktrail pricing list --missing-only
toktrail sessions copilot
```

Virtual and pricing-based actual costs are computed at report time, not during
import. Updating `config.toml` immediately changes future `status` and
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
