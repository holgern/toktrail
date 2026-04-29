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

## Quickstart

Initialize the toktrail state database:

```bash
toktrail init
```

Start a tracking session:

```bash
toktrail start --name refactor-auth-flow
```

Import usage into the active tracking session:

```bash
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
toktrail status --harness pi --source-session pi_ses_001 --json
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
toktrail sessions pi --sort tokens --limit 5 --columns source_session_id,total --rich
toktrail sessions pi pi_ses_001 --json
toktrail sessions copilot
toktrail sessions copilot --copilot-path ~/.copilot/otel
```

Legacy aliases remain available:

```bash
toktrail opencode sessions
toktrail pi sessions
toktrail copilot sessions
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

Usage imports store normalized usage metadata locally and store raw source JSON
by default for local debugging and reprocessing. Pi imports store raw JSONL
entry lines by default. Use `--no-raw` with import or watch commands to
suppress raw JSON storage.

toktrail never prints raw OpenCode, Pi, or Copilot JSON in CLI output.

## Reporting

`toktrail status` reports:

- total input, output, reasoning, cache-read, and cache-write tokens
- total cost in USD
- grouped summaries by harness, model, and agent/mode
- optional filtered views by harness, source session, provider, model, agent,
  and created-at time range

`toktrail status --json` returns the same information in a machine-readable JSON
shape for automation.

## Limitations

The first pass intentionally does not include:

- legacy OpenCode JSON file parsing
- JSON migration caches
- background daemons or services
- pricing or cost estimation for Pi or Copilot imports; both are stored with
  `$0.00` cost for now
- workspace metadata extraction from Pi session headers
- Copilot tool-span or metric accounting; phase 1 imports chat spans only and
  ignores tools, agent invocations, and metrics
- network sync or cloud storage
- external pricing lookups
- TUI reporting
