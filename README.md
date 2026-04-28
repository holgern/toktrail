# toktrail

`toktrail` is a Python CLI for tracking OpenCode and GitHub Copilot CLI token
usage inside a local toktrail SQLite database.

The first implementation focuses on:

- OpenCode SQLite and GitHub Copilot CLI OTEL JSONL as supported source
  harnesses
- local SQLite for both the OpenCode source database and toktrail state
- reporting totals by tracking session, harness, model, and agent/mode

## Requirements

- Python 3.10 or newer
- an OpenCode SQLite database, typically at
  `~/.local/share/opencode/opencode.db`, and/or
- a GitHub Copilot CLI OTEL JSONL export file

toktrail reads supported source data in read-only mode and does not modify the
source database or Copilot export file.

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

Import or watch GitHub Copilot CLI OTEL JSONL usage:

```bash
toktrail import copilot --copilot-file /path/to/copilot-otel.jsonl
toktrail import copilot --source-session conv-1 --copilot-file /path/to/copilot-otel.jsonl
toktrail import copilot --since-start --no-raw --copilot-file /path/to/copilot-otel.jsonl

toktrail watch copilot --copilot-file /path/to/copilot-otel.jsonl --interval 2
```

If `TOKTRAIL_COPILOT_FILE` is set, `toktrail import copilot` and
`toktrail watch copilot` can omit `--copilot-file`.

Inspect OpenCode source sessions without mutating toktrail state:

```bash
toktrail opencode sessions
toktrail opencode sessions --opencode-db /path/to/opencode.db
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
by default for local debugging and reprocessing. Use `--no-raw` with import or
watch commands to suppress raw JSON storage.

toktrail never prints raw OpenCode or Copilot JSON in CLI output.

## Reporting

`toktrail status` reports:

- total input, output, reasoning, cache-read, and cache-write tokens
- total cost in USD
- grouped summaries by harness, model, and agent/mode

`toktrail status --json` returns the same information in a machine-readable JSON
shape for automation.

## Limitations

The first pass intentionally does not include:

- legacy OpenCode JSON file parsing
- JSON migration caches
- background daemons or services
- pricing or cost estimation for Copilot imports; Copilot OTEL usage is stored
  with `$0.00` cost for now
- Copilot tool-span or metric accounting; phase 1 imports chat spans only and
  ignores tools, agent invocations, and metrics
- network sync or cloud storage
- external pricing lookups
- TUI reporting
