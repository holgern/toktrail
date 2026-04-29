# toktrail

`toktrail` is a Python CLI for tracking OpenCode, Pi, and GitHub Copilot CLI
token usage inside a local toktrail SQLite database.

The first implementation focuses on:

- OpenCode SQLite, Pi JSONL sessions, and GitHub Copilot CLI OTEL JSONL as
  supported source harnesses
- local SQLite for both the OpenCode source database and toktrail state
- reporting totals by tracking session, harness, model, and agent/mode

## Requirements

- Python 3.10 or newer
- an OpenCode SQLite database, typically at
  `~/.local/share/opencode/opencode.db`, and/or
- Pi session JSONL files, typically under `~/.pi/agent/sessions`, and/or
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
API, canonical errors, and privacy defaults.

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
toktrail import --harness opencode --source tests/fixtures/opencode.db
toktrail import --no-session
toktrail import opencode
toktrail import pi
toktrail import pi --pi-path ~/.pi/agent/sessions
toktrail import pi --pi-path ~/.pi/agent/sessions/<encoded-cwd>/session.jsonl
toktrail import pi --source-session pi_ses_001
toktrail import pi --since-start
toktrail import pi --no-raw
toktrail import copilot --copilot-file /path/to/copilot-otel.jsonl
```

For local acceptance and testing, a sample OpenCode source database is checked
in at `tests/fixtures/opencode.db`:

```bash
toktrail import opencode --opencode-db tests/fixtures/opencode.db
```

Show the current session totals:

```bash
toktrail status
toktrail status --json
toktrail status --thinking high --json
toktrail status --collapse-thinking
toktrail --config ~/.config/toktrail/config.toml status --json
toktrail status --harness pi --source-session pi_ses_001 --json
```

Show period-based usage across canonical ledger rows, even without an active
tracking session:

```bash
toktrail usage today
toktrail usage last-week --utc --json
toktrail usage --since 2026-05-01 --until 2026-06-01 --timezone Europe/Berlin
```

Stop the active tracking session:

```bash
toktrail stop
```

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
toktrail sessions opencode
toktrail sessions copilot
```

Inspect and manage pricing config:

```bash
toktrail config path
toktrail config init
toktrail config init --template copilot
toktrail config show
toktrail config validate
toktrail --config /path/to/config.toml status --json
```

Import usage:

```bash
toktrail import
toktrail import --harness opencode --source /path/to/opencode.db
toktrail import --harness pi --source ~/.pi/agent/sessions
toktrail import --session 3
toktrail import --no-session
toktrail import --raw
toktrail import --no-raw
```

The plain `toktrail import` command reads enabled harnesses and source paths from
`[imports]` and `[imports.sources]` in `config.toml`. Legacy
`toktrail import opencode|pi|copilot` subcommands still work for compatibility.

Import or watch OpenCode usage:

```bash
toktrail import opencode
toktrail import opencode --session 3
toktrail import opencode --source-session ses_456
toktrail import opencode --since-start
toktrail import opencode --no-raw
toktrail import opencode --opencode-db /path/to/opencode.db

toktrail watch opencode --interval 2
toktrail watch opencode --session 3 --source-session ses_456
```

Import or watch Pi usage:

```bash
toktrail import pi
toktrail import pi --pi-path ~/.pi/agent/sessions
toktrail import pi --pi-path ~/.pi/agent/sessions/<encoded-cwd>/session.jsonl
toktrail import pi --source-session pi_ses_001
toktrail import pi --since-start
toktrail import pi --no-raw

toktrail watch pi --interval 2
toktrail watch pi --pi-path ~/.pi/agent/sessions
```

Import or watch GitHub Copilot CLI OTEL JSONL usage:

```bash
toktrail import copilot --copilot-file /path/to/copilot-otel.jsonl
toktrail import copilot --source-session conv-1 --copilot-file /path/to/copilot-otel.jsonl
toktrail import copilot --since-start --no-raw --copilot-file /path/to/copilot-otel.jsonl

toktrail watch copilot --copilot-file /path/to/copilot-otel.jsonl --interval 2
toktrail copilot run -- gh copilot suggest "explain git reflog"
eval "$(toktrail copilot env bash)"
eval "$(toktrail copilot env zsh)"
toktrail copilot env fish | source
```

Supported `toktrail copilot env` shells are `bash`, `zsh`, `fish`,
`nu`/`nushell`, and `powershell`/`pwsh`. Pass `--json` to output a JSON object
instead of shell code.

For Nushell:

```nu
toktrail copilot env nu | save -f /tmp/toktrail-copilot-env.nu
source-env /tmp/toktrail-copilot-env.nu
```

Or use `--json` for direct consumption:

```nu
toktrail copilot env nu --json | from json | load-env
```

For PowerShell:

```powershell
toktrail copilot env powershell | Invoke-Expression
```

If `TOKTRAIL_COPILOT_FILE` or `COPILOT_OTEL_FILE_EXPORTER_PATH` is set,
`toktrail import copilot` and `toktrail watch copilot` can omit
`--copilot-file`. If neither is set, toktrail also discovers the latest
`~/.copilot/otel/*.jsonl` export when available.

Inspect raw source sessions without mutating toktrail state:

```bash
toktrail sessions opencode
toktrail sessions opencode --opencode-db /path/to/opencode.db
toktrail sessions pi
toktrail sessions pi --pi-path ~/.pi/agent/sessions
toktrail sessions pi --last --breakdown
toktrail sessions pi --sort tokens --limit 5 --columns source_session_id,total,actual,virtual,savings --rich
toktrail sessions pi pi_ses_001 --json
toktrail sessions copilot
toktrail sessions copilot --copilot-path ~/.copilot/otel
toktrail --config ~/.config/toktrail/config.toml sessions copilot --sort virtual --limit 5
```

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

Usage imports store normalized usage metadata locally and store raw source JSON
by default for local debugging and reprocessing. Pi imports store raw JSONL
entry lines by default. Use `--no-raw` with import or watch commands to
suppress raw JSON storage.

toktrail never prints raw OpenCode, Pi, or Copilot JSON in CLI output.

## Reporting

`toktrail status` reports:

- total input, output, reasoning, cache-read, and cache-write tokens
- source cost from imported data when the harness provides it
- actual cost based on configured accounting rules
- virtual cost based on configured pricing tables
- savings (`virtual - actual`) plus unpriced model-group counts
- grouped summaries by harness, model, and agent/mode
- thinking-level metadata on model rows when the source exposes it
- optional filtered views by harness, source session, provider, model, agent,
  and created-at time range

`toktrail usage` applies the same token and cost reporting to the canonical
ledger without requiring a tracking session. Named periods use half-open
`[since, until)` windows for `today`, `yesterday`, `this-week`, `last-week`,
`this-month`, and `last-month`.

`toktrail status --json` returns the same information in a machine-readable JSON
shape for automation.

By default:

- OpenCode keeps imported source cost as actual cost
- Pi and Copilot treat actual cost as `$0.00`
- virtual cost uses configured pricing tables when available

This makes Copilot subscription analysis straightforward: source and actual cost
stay at `$0.00` while virtual cost shows what the same usage would have cost via
public API pricing.

Example workflow:

```bash
toktrail config init --template copilot
toktrail import copilot --copilot-file ~/.copilot/otel/copilot-otel-20260429-090000.jsonl
toktrail status
toktrail sessions copilot --sort savings --columns source_session_id,actual,virtual,savings
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
