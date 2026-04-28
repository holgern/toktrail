# toktrail

`toktrail` is a Python CLI for tracking OpenCode assistant token usage and cost
inside a local toktrail SQLite database.

The first implementation focuses on:

- OpenCode as the only supported source harness
- local SQLite for both the OpenCode source database and toktrail state
- reporting totals by tracking session, harness, model, and agent/mode

## Requirements

- Python 3.10 or newer
- an OpenCode SQLite database, typically at
  `~/.local/share/opencode/opencode.db`

toktrail reads the OpenCode database in read-only mode and does not modify the
source data.

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

Import OpenCode usage into the active tracking session:

```bash
toktrail import opencode
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

OpenCode usage imports store normalized usage metadata locally and store raw
message JSON by default for local debugging and reprocessing. Use
`toktrail import opencode --no-raw` to suppress raw JSON storage.

toktrail never prints raw OpenCode JSON in CLI output.

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
- non-OpenCode adapters
- network sync or cloud storage
- external pricing lookups
- TUI reporting
