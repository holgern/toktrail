# Stable API manual-run examples

These examples show how an external tool can measure token usage for a manually
run coding agent without importing toktrail internals.

## Flow

1. `prepare_manual_run()` creates toktrail state if needed, starts a tracking
   session, snapshots existing source sessions, and prepares any harness
   environment.
2. The caller asks the user to start the harness manually and paste a prompt.
3. The user closes the harness and returns to the script.
4. `finalize_manual_run()` snapshots source sessions again, detects the new or
   updated source session, imports token usage into the toktrail state DB,
   optionally stops the tracking session, and returns a detailed report.

## Supported harnesses

- OpenCode: `opencode`
- Pi: `pi`
- GitHub Copilot CLI: `copilot`
- Codex CLI: `codex`
- Goose: `goose`
- Droid: `droid`
- Amp: `amp`
- Claude: `claude`
- Vibe: `vibe`

## Run examples

```bash
python examples/manual_run_opencode.py
python examples/manual_run_pi.py
python examples/manual_run_copilot.py --shell bash
python examples/manual_run_codex.py
python examples/manual_run_goose.py
python examples/manual_run_droid.py
python examples/manual_run_amp.py
python examples/manual_run_claude.py
python examples/manual_run_vibe.py
```

## Goose API example

```python
from pathlib import Path

from toktrail.api.imports import import_usage
from toktrail.api.paths import default_goose_sessions_db_path
from toktrail.api.sources import list_source_sessions

source_path = default_goose_sessions_db_path()
result = import_usage(Path(".toktrail/toktrail.db"), "goose", source_path=source_path)
sessions = list_source_sessions("goose", source_path=source_path, limit=5)
```

## Droid API example

```python
from pathlib import Path

from toktrail.api.imports import import_usage
from toktrail.api.paths import default_droid_sessions_path
from toktrail.api.sources import list_source_sessions

source_path = default_droid_sessions_path()
result = import_usage(Path(".toktrail/toktrail.db"), "droid", source_path=source_path)
sessions = list_source_sessions("droid", source_path=source_path, limit=5)
```

## Amp API example

```python
from pathlib import Path

from toktrail.api.imports import import_usage
from toktrail.api.paths import default_amp_threads_path
from toktrail.api.sources import list_source_sessions

source_path = default_amp_threads_path()
result = import_usage(Path(".toktrail/toktrail.db"), "amp", source_path=source_path)
sessions = list_source_sessions("amp", source_path=source_path, limit=5)
```

## Claude API example

```python
from pathlib import Path

from toktrail.api.imports import import_usage
from toktrail.api.paths import default_claude_projects_path
from toktrail.api.sources import list_source_sessions

source_path = default_claude_projects_path()
result = import_usage(Path(".toktrail/toktrail.db"), "claude", source_path=source_path)
sessions = list_source_sessions("claude", source_path=source_path, limit=5)
```

## Vibe API example

```python
from pathlib import Path

from toktrail.api.imports import import_usage
from toktrail.api.paths import default_vibe_logs_path
from toktrail.api.sources import list_source_sessions

source_path = default_vibe_logs_path()
result = import_usage(Path(".toktrail/toktrail.db"), "vibe", source_path=source_path)
sessions = list_source_sessions("vibe", source_path=source_path, limit=5)
```

## Per-harness notes

OpenCode usually reads from `~/.local/share/opencode/opencode.db`. Start
OpenCode in this repository, paste the printed prompt, wait for the answer,
exit OpenCode, then press Enter in the Python script.

Pi usually writes JSONL files under `~/.pi/agent/sessions`. Start Pi in this
repository, paste the printed prompt, wait for the answer, exit Pi, then press
Enter in the Python script. Use `--source /path/to/pi/sessions` to override the
source path.

GitHub Copilot CLI needs OTEL file-export environment variables. Apply the
printed environment exports in the shell where Copilot will run, start Copilot
CLI in this repository, paste the printed prompt, wait for the answer, exit
Copilot, then press Enter in the Python script.

Codex usually writes session logs under `~/.codex/sessions`. Start Codex in
this repository, paste the printed prompt, wait for the answer, exit Codex, then
press Enter in the Python script. Use `--source /path/to/codex/sessions` to
override the source path.

Goose usually writes cumulative SQLite session rows to
`~/.local/share/goose/sessions/sessions.db`. Start Goose in this repository,
paste the printed prompt, wait for the answer, exit Goose, then press Enter in
the Python script. Use `--source /path/to/sessions.db` to override the source
path.

Droid usually writes cumulative settings JSON files under
`~/.factory/sessions`. Start Droid in this repository, paste the printed prompt,
wait for the answer, exit Droid, then press Enter in the Python script. Use
`--source /path/to/factory/sessions` to override the source path.

Vibe writes cumulative session metadata under ~/.vibe/logs/session/<session-dir>/meta.json.
Start Vibe in this repository, paste the printed prompt, wait for the answer, exit Vibe, then press Enter in the Python script. Use --source /path/to/.vibe/logs/session to override the source path.

## Detailed output

Each example prints:

- tracking session id and active/ended state;
- detected source session id;
- rows/events seen, imported, skipped, and linked;
- token totals for input, output, reasoning, cache read, cache write, and total;
- source, actual, virtual, savings, and unpriced cost fields;
- model breakdown with provider, model, thinking level, message count, token
  fields, and cost fields;
- agent breakdown where available;
- unconfigured pricing rows.

## Area classification via stable API

```python
from pathlib import Path

from toktrail.api import (
    assign_area_to_session,
    set_active_area,
    usage_areas_report,
    usage_report,
)

db_path = Path(".toktrail/toktrail.db")

set_active_area("work/odoo", db_path=db_path)
assign_area_to_session(
    "work/odoo",
    harness="opencode",
    source_session_id="ses-1",
    db_path=db_path,
)

rollup = usage_report(db_path, period="today", area="work")
areas = usage_areas_report(db_path, period="today")
```

For subscription reporting, rows are keyed by subscription `id` and expose
`usage_provider_ids` plus `quota_cost_basis`/`billing_basis` fields when
`subscription_usage_report()` is used.

## Ambiguous source sessions

If several source sessions changed while the user was running the harness,
re-run the example with:

```bash
python examples/manual_run_copilot.py --source-session-id <id>
```

The examples deliberately do not select arbitrarily in this case.

## Privacy

The examples call `finalize_manual_run(..., include_raw_json=False)`. Raw source
JSON remains opt-in and is not stored by these examples.
