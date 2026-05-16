[![PyPI - Version](https://img.shields.io/pypi/v/toktrail)](https://pypi.org/project/toktrail/)
![PyPI - Python Version](https://img.shields.io/pypi/pyversions/toktrail)
![PyPI - Downloads](https://img.shields.io/pypi/dm/toktrail)
[![codecov](https://codecov.io/gh/holgern/toktrail/graph/badge.svg?token=3b1K0FOPiY)](https://codecov.io/gh/holgern/toktrail)

# toktrail

`toktrail` is a Python CLI for tracking OpenCode, Pi, Codex, Every Code, Goose,
Droid, Amp, Vibe, Harnessbridge ledger, and GitHub Copilot CLI token usage
inside a local toktrail SQLite database.

The first implementation focuses on:

- OpenCode SQLite, Pi JSONL sessions, Codex/Every Code JSONL sessions, Goose
  SQLite sessions, Droid settings JSON sessions, Amp thread JSON sessions, Vibe
  session logs, and GitHub Copilot CLI OTEL JSONL as supported source harnesses
- local SQLite for both the OpenCode source database and toktrail state
- reporting totals by tracking session, harness, model, and agent/mode

## Requirements

- Python 3.10 or newer
- an OpenCode SQLite database, typically at
  `~/.local/share/opencode/opencode.db`, and/or
- Pi session JSONL files, typically under `~/.pi/agent/sessions`, and/or
- Codex session JSONL files, typically under `~/.codex/sessions`, and/or
- Every Code session JSONL files, typically under `~/.code/sessions`, and/or
- Goose SQLite sessions, typically at
  `~/.local/share/goose/sessions/sessions.db`, and/or
- Harnessbridge JSONL session ledgers, typically under
  `~/.harnessbridge/sessions`, and/or
- Droid settings JSON sessions, typically under `~/.factory/sessions`, and/or
- Amp thread JSON sessions, typically under `~/.local/share/amp/threads`, and/or
- Vibe session logs, typically under `~/.vibe/logs/session`, and/or
- Claude Code project transcripts, typically under `~/.claude/projects`, and/or
- GitHub Copilot CLI OTEL JSONL export files, typically under `~/.copilot/otel`

toktrail reads supported source data in read-only mode and does not modify the
source database or source JSONL files.

## Code / Every Code

Toktrail supports Every Code (`just-every/code`) as harness `code`. Code keeps
the Codex-compatible session format, so toktrail reuses the Codex parser while
storing imported usage separately under `harness=code`.

- Default source path: `~/.code/sessions`
- `TOKTRAIL_CODE_SESSIONS` overrides the exact file or directory to import
- `CODE_HOME` sets the Code home directory and resolves `${CODE_HOME}/sessions`
  when `TOKTRAIL_CODE_SESSIONS` is unset

## Configuration files

toktrail uses these configuration files:

- `config.toml` for imports and costing policy
- `machine.toml` for local machine identity (not meant to be synced)
- `prices.toml` for manual `[[pricing.virtual]]` and `[[pricing.actual]]` overrides
- `prices/` for generated provider files like `prices/openai.toml`
- `subscriptions.toml` for `[[subscriptions]]` plans/windows

You can optionally keep pricing/subscription files in your Git sync repo while
keeping local bootstrap config machine-specific:

```toml
[sync.git]
repo = "~/toktrail-state"
track = ["prices", "provider-prices", "subscriptions"]
```

With `track` enabled, toktrail resolves:

- `<repo>/config/prices.toml`
- `<repo>/config/prices/*.toml`
- `<repo>/config/subscriptions.toml`

Initialize them together:

```bash
toktrail config init
toktrail config path
toktrail config show
```

## Install

```bash
python -m pip install -e .
```

For development:

```bash
python -m pip install -e ".[dev]"
```

## Performance benchmark

Run the local synthetic report benchmark:

```bash
python tests/perf/bench_reports.py
```

The benchmark is intentionally not part of default test runs.

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
session_usage = session_report(db_path, run.id)
today_usage = usage_report(db_path, period="today", timezone="UTC")
subscription_usage = subscription_usage_report(db_path, provider_id="opencode-go")
```

See [`API.md`](API.md) for the stable import boundary, public models, workflow
API, canonical errors, and privacy defaults. Task-oriented Python usage is in
[`docs/api_usage.rst`](docs/api_usage.rst). Runnable manual-run examples for
OpenCode, Pi, Copilot, Codex, Goose, Harnessbridge, Droid, Amp, Claude, and
Vibe are documented in [`docs/stable_api_examples.md`](docs/stable_api_examples.md).

## Quickstart

Initialize the toktrail state database:

```bash
toktrail init
```

Start a tracking session:

```bash
toktrail run start --name refactor-auth-flow
toktrail run start --name codex-task --harness codex
toktrail run start --name openai-gpt --provider openai --model gpt-5.5
```

Refresh usage from config or a single harness:

```bash
toktrail config init
toktrail refresh
toktrail refresh --harness codex --source ~/.codex/sessions
toktrail refresh --harness code --source ~/.code/sessions
toktrail refresh --harness harnessbridge --source ~/.harnessbridge/sessions
toktrail refresh --harness amp --source ~/.local/share/amp/threads
toktrail refresh --harness claude --source ~/.claude/projects
toktrail refresh --dry-run
toktrail refresh --no-run
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
toktrail analyze cache opencode --last
toktrail analyze cache opencode ses-1 --json
```

Show period-based usage across canonical ledger rows, even without an active
tracking session:

```bash
toktrail machine set-name thinkpad
toktrail machine status
toktrail machine list
toktrail usage today
toktrail usage today --machine thinkpad
toktrail usage machines
toktrail usage machines --today --json
toktrail usage today --rich
toktrail usage last-week --utc --json
toktrail usage summary --since 2026-05-01 --until 2026-06-01 --timezone Europe/Berlin
toktrail usage summary --price-state priced --sort provider --limit 10 --json
toktrail usage today --no-refresh
toktrail usage today --refresh-details
toktrail usage runs --rich
toktrail usage sessions --today
toktrail usage sessions --today --table
toktrail usage sessions --this-week --harness codex
toktrail usage daily --rich
toktrail usage sessions --last
toktrail usage sessions --order asc --limit 10 --table
toktrail usage runs --last --limit 5
toktrail usage runs --archived
toktrail area create work/odoo
toktrail area use work/odoo
toktrail area use work/odoo --ttl 4h
toktrail area status
toktrail area assign work/odoo --harness opencode --source-session-id ses-1
toktrail area assign work/odoo --session "pc1/opencode/ses-1"
toktrail area sessions --unassigned --today
toktrail area bulk-assign work/odoo --unassigned --today --dry-run
toktrail area bulk-assign work/odoo --unassigned --today --apply
toktrail area detect
toktrail area bind-cwd work/odoo --git-root
toktrail usage today --area work
toktrail usage today --area work --area-exact
toktrail usage today --unassigned-area
toktrail usage areas --today
toktrail usage areas --today --direct
toktrail usage areas --today --leaves --percent --share-by tokens
toktrail subscriptions status
toktrail subscriptions status --timezone Europe/Berlin
toktrail subscriptions status --utc
toktrail subscriptions status --provider opencode-go --json
toktrail sync export --out toktrail-state.tar.gz --no-refresh
toktrail sync import toktrail-state.tar.gz
toktrail sync import toktrail-state.tar.gz --dry-run --json
```

## Statusline

Render a compact prompt/status line from local toktrail state:

```bash
toktrail statusline --harness codex --no-refresh
toktrail statusline --harness opencode --refresh auto --json
toktrail statusline --session latest --harness pi --no-refresh
toktrail statusline test --harness codex --no-refresh
toktrail statusline install --target starship
toktrail statusline install --target tmux
toktrail statusline config show
toktrail statusline config set elements harness,model,tokens,cached,cost,quota,burn
toktrail usage statusline --no-refresh
```

`toktrail statusline` prefers the active scoped source session when one is known,
otherwise the latest matching source session, then falls back to today totals.
Use `--session auto|latest|none` to control that behavior. `--refresh never|auto|always` keeps prompt-time refresh conservative, and `--no-refresh`
is the fastest state-only path. `toktrail usage statusline` remains as a
compatibility alias.

Safe install targets print ready-to-paste snippets for `starship`, `tmux`,
`bash`, and `zsh`. Native harness targets (`pi`, `opencode`, `codex`, `code`)
print instructions instead of editing unknown config files.

Statusline config lives in `config.toml`:

```toml
[statusline]
default_harness = "auto"
basis = "virtual"
refresh = "auto"
session = "auto"
max_width = 120
active_session_window_minutes = 30
elements = ["harness", "area", "model", "tokens", "cached", "cost", "quota", "burn", "unpriced"]

[statusline.cache]
output_cache_secs = 2
min_refresh_interval_secs = 5
stale_after_secs = 60

[[context_window]]
provider = "openai"
model = "gpt-5.3-codex"
tokens = 272000
```

Stop the active tracking session:

```bash
toktrail run stop
toktrail run archive 42
toktrail run list --archived
toktrail run unarchive 42
```

## Command model

The canonical CLI flow is:

```bash
toktrail init
toktrail config init
toktrail sources
toktrail machine status
toktrail run start --name <name>
toktrail refresh
toktrail run status
toktrail analyze cache opencode --last
toktrail usage today
toktrail usage machines
toktrail run list
toktrail subscriptions status
toktrail sync git sync
toktrail sync export --out toktrail-state.tar.gz
toktrail run stop
```

Report commands (`toktrail usage`, `toktrail run status`, and
`toktrail subscriptions status`) refresh configured sources first by default. Use
`--no-refresh` for stale local-state reads, and `--refresh-details` to print a
compact refresh summary.

`--rich` renders report tables with Rich formatting; default output stays
borderless/plain. Install the optional extra to enable it:

```bash
pip install "toktrail[rich]"
```

For subscriptions, `subscriptions.timezone` controls quota/billing window
calculation. Human output timestamps are rendered in local timezone by default;
use `--timezone <IANA>` or `--utc` to override display timezone.

Session terminology:

- `toktrail run list` lists tracking **runs** (start/stop windows).
- `toktrail sources sessions <h>` lists raw **source sessions** from a specific harness.
- `toktrail usage sessions` summarizes imported source-session **usage** (tokens, costs, models).
- Use `toktrail usage sessions --today` or `--this-week` for bounded source-session lists.
- Use `toktrail usage sessions --table` for the legacy wide table.
- `toktrail usage runs` summarizes usage grouped by tracking **run**.

Area classification:

- `toktrail area use <path>` sets the active area for new source sessions on the
  current machine only.
- `toktrail area use <path> --ttl <duration>` or `--until <iso-datetime>` sets an
  expiring active area.
- `toktrail area list` defaults to a tree view by path; use
  `toktrail area list --verbose` for stable/local IDs.
- `toktrail area assign <path> --harness <h> --source-session-id <id>` assigns an
  area to an existing source session and backfills imported events.
- `toktrail area assign <path> --session <machine/harness/source-session-id>`
  assigns from printed session keys.
- `toktrail area assign <path> --last` defaults to the local machine. Use
  `--all-machines` for global newest-session behavior.
- `toktrail area sessions` lists source sessions with area metadata; use
  `--unassigned` to find cleanup candidates.
- `toktrail area bulk-assign` supports dry-run and apply flows for historical
  repair.
- `toktrail usage ... --area <path>` includes descendants by default.
- Add `--area-exact` to match only the exact area path.
- Use `--unassigned-area` to report events without any area.
- `toktrail usage areas` reports subtree totals and explicit direct-vs-subtree
  columns; use `--direct`, `--subtree`, `--leaves`, and `--percent`.
- Area identity contract: `area_id`/`local_id` is machine-local SQLite identity,
  while `sync_id`/`stable_id` is durable cross-machine identity.

`toktrail sync import` validates archive paths, manifest checksums, schema
version, and usage-event fingerprints before merging.
Sync archives preserve area hierarchy rows, source-session area assignments,
machine-scoped active areas, and event-level `area_id` values.

## Git sync

Use `toktrail sync git` to exchange immutable state archives through a Git repo.
The live SQLite DB remains local; toktrail imports archives idempotently into
local state.

```bash
toktrail sync git init --repo ~/toktrail-state --remote git@github.com:me/toktrail-state.git
cd ~/toktrail-state
git pull
git push
```

On another machine:

```bash
toktrail sync git init --repo ~/toktrail-state --remote git@github.com:me/toktrail-state.git
cd ~/toktrail-state
git pull
```

`toktrail sync git init` installs local Git hooks by default so plain `git pull`
imports archives into the local toktrail DB. Hooks are clone-local, so run
`toktrail sync git init` (or `toktrail sync git hooks install`) once per clone.

By default Git sync exports with raw JSON redaction and stores archives under
`archives/<machine_id>/...tar.gz`. Do not commit live sqlite files
(`toktrail.db`, `toktrail.db-wal`, `toktrail.db-shm`) into the sync repo.

### Git-backed prices and subscriptions

Keep `config.toml` local, then opt into shared costing files:

```toml
[sync.git]
repo = "~/toktrail-state"
track = ["prices", "provider-prices", "subscriptions"]
```

toktrail then reads/writes:

- `<repo>/config/prices.toml`
- `<repo>/config/prices/*.toml`
- `<repo>/config/subscriptions.toml`

Compatibility commands remain available for explicit flows and recovery:

```bash
toktrail sync git import-local
toktrail sync git export-local --no-refresh
toktrail sync git pull
toktrail sync git push
toktrail sync git sync
```

CLI/env overrides still win over tracked paths:
`--prices`, `--prices-dir`, `--subscriptions`, `TOKTRAIL_PRICES`,
`TOKTRAIL_PRICES_DIR`, `TOKTRAIL_SUBSCRIPTIONS`.

Use `toktrail refresh` for explicit/manual refresh operations. It reads enabled
harnesses and source paths from `config.toml`:

```toml
[imports]
harnesses = ["opencode", "pi", "copilot", "codex", "code", "goose", "harnessbridge", "droid", "amp", "claude", "vibe"]
missing_source = "warn"
include_raw_json = false

[imports.sources]
opencode = ["~/.local/share/opencode/opencode.db", "~/.local/share/opencode/opencode-stable.db"]
pi = ["~/.pi/agent/sessions", "~/.omp/agent/sessions"]
copilot = "~/.copilot/otel"
codex = ["~/.codex/sessions", "~/.codex/archived_sessions"]
code = "~/.code/sessions"
goose = "~/.local/share/goose/sessions/sessions.db"
harnessbridge = "~/.harnessbridge/sessions"
droid = "~/.factory/sessions"
amp = "~/.local/share/amp/threads"
claude = "~/.claude/projects"
vibe = "~/.vibe/logs/session"
```

`[[subscriptions]]` rows live in `subscriptions.toml`.

Manual pricing rows live in `prices.toml`. Generated provider pricing files
live in `prices/<provider>.toml`. toktrail loads provider files first and
`prices.toml` last, so manual rows override generated rows.

You can generate provider files directly from provider docs text:

When `[sync.git].track` includes `"provider-prices"`, default output moves to
`<repo>/config/prices/<provider>.toml`.

```bash
toktrail prices parse --provider openai --tier standard --input openai-pricing.jsx
toktrail prices parse --provider zai --input zai-pricing.md
toktrail prices parse --provider opencode-go --table actual --input opencode-go.txt
toktrail prices parse --provider openai --input openai-pricing.jsx --output -
toktrail prices parse --provider openai --input openai-pricing.jsx --output ~/.config/toktrail/prices/openai.toml
```

Context-tier pricing is supported with multiple rows for the same
`provider/model` using inclusive context ranges:

```toml
[[pricing.virtual]]
provider = "openai"
model = "gpt-5.4"
context_min_tokens = 0
context_max_tokens = 272000
context_label = "<= 272K"
input_usd_per_1m = 2.5
cached_input_usd_per_1m = 0.25
output_usd_per_1m = 15.0

[[pricing.virtual]]
provider = "openai"
model = "gpt-5.4"
context_min_tokens = 272001
context_label = "> 272K"
input_usd_per_1m = 5.0
cached_input_usd_per_1m = 0.5
output_usd_per_1m = 22.5
```

Tier selection uses prompt-like context tokens:
`input + cache_read + cache_write`.

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
toktrail run list
toktrail subscriptions status
toktrail sync git status --repo ~/toktrail-state
toktrail sync git sync --repo ~/toktrail-state
toktrail sync export --out toktrail-state.tar.gz
toktrail sync import toktrail-state.tar.gz --dry-run --json
toktrail sources sessions pi
toktrail sources session pi pi_ses_001
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
toktrail prices list
toktrail prices list --provider openai --sort model
toktrail prices list --query gpt-5 --aliases
toktrail prices list --model gpt-5-mini --json
toktrail prices list --used-only
toktrail prices list --missing-only
toktrail config validate
toktrail subscriptions status
toktrail --config /path/to/config.toml run status --json
```

Refresh usage:

```bash
toktrail refresh
toktrail refresh --harness opencode --source /path/to/opencode.db
toktrail refresh --harness pi --source ~/.pi/agent/sessions
toktrail refresh --harness codex --source ~/.codex/sessions
toktrail refresh --harness code --source ~/.code/sessions
toktrail refresh --harness goose --source ~/.local/share/goose/sessions/sessions.db
toktrail refresh --harness harnessbridge --source ~/.harnessbridge/sessions
toktrail refresh --harness droid --source ~/.factory/sessions
toktrail refresh --harness amp --source ~/.local/share/amp/threads
toktrail refresh --dry-run
toktrail refresh --run 3
toktrail refresh --no-run
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
toktrail refresh --harness harnessbridge --source ~/.harnessbridge/sessions
toktrail refresh --harness droid --source ~/.factory/sessions
toktrail refresh --harness amp --source ~/.local/share/amp/threads
toktrail refresh --harness claude --source ~/.claude/projects
toktrail refresh --harness vibe --source ~/.vibe/logs/session

toktrail watch

toktrail watch --harness opencode
toktrail watch --harness opencode --harness codex --harness code

toktrail copilot env bash
toktrail copilot env zsh
toktrail copilot env fish
toktrail copilot env nu
toktrail copilot env powershell

toktrail sources sessions pi
toktrail sources sessions codex
toktrail sources sessions code
toktrail sources sessions claude
toktrail sources session pi pi_ses_001
toktrail sources session goose goose_session_id

toktrail prices list
toktrail prices list --missing-only
```

Copilot source discovery honors `TOKTRAIL_COPILOT_FILE`,
`COPILOT_OTEL_FILE_EXPORTER_PATH`, and `TOKTRAIL_COPILOT_OTEL_DIR`. Codex
discovery honors `TOKTRAIL_CODEX_SESSIONS`. Code discovery honors
`TOKTRAIL_CODE_SESSIONS` and `CODE_HOME`. Goose discovery honors
`TOKTRAIL_GOOSE_SESSIONS` and `GOOSE_PATH_ROOT`. Harnessbridge discovery
honors `TOKTRAIL_HARNESSBRIDGE_SESSIONS` and defaults to
`~/.harnessbridge/sessions`.

Harnessbridge is treated as a source format, not a reporting harness. Imported
usage rows keep the inner harness name from each ledger row, so reports still
group under `pi`, `codex`, `copilot`, `opencode`, or other recorded harnesses.
Rows marked `accounting="primary"` import normally; rows marked
`accounting="mirror"` are skipped by default to avoid double-counting native
sessions.

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

Local machine identity defaults to:

```text
~/.config/toktrail/machine.toml
```

If `XDG_CONFIG_HOME` is set, toktrail uses:

```text
$XDG_CONFIG_HOME/toktrail/machine.toml
```

`TOKTRAIL_CONFIG`/`--config`, `TOKTRAIL_PRICES`/`--prices`, and
`TOKTRAIL_SUBSCRIPTIONS`/`--subscriptions` can override each config file path.
Missing files are safe: toktrail falls back to built-in defaults.
`TOKTRAIL_MACHINE_CONFIG`/`--machine-config` overrides `machine.toml`, and
`TOKTRAIL_MACHINE_NAME` overrides the configured machine name directly.

Usage imports store normalized usage metadata locally. Raw source JSON is
disabled by default and remains opt-in local debugging data only. Use `--raw`
to store raw source payloads for a run, or `--no-raw` to make that choice
explicit in automation.

toktrail never prints raw OpenCode, Pi, Codex, Goose, Harnessbridge, Droid,
Amp, or Copilot JSON in CLI output.

## Reporting

`toktrail run status` reports:

- total input, output, reasoning, cache-read, and cache-write tokens
- source cost from imported data when the harness provides it
- actual cost based on configured accounting rules
- virtual cost based on configured pricing tables
- savings (`virtual - actual`) plus unpriced model-group counts
- exact unconfigured harness/provider/model diagnostics when pricing is missing
- grouped summaries by harness, model, and agent/mode
- grouped summaries by machine
- collapsed thinking-level metadata by default, with `--split-thinking` to
  expand model rows when needed
- optional filtered views by harness, source session, provider, model, agent,
  area path (`--area`, `--area-exact`, `--unassigned-area`), created-at time
  range, price state, minimum message/token thresholds, sort, and grouped-row
  limits

`toktrail usage` applies the same token and cost reporting to the canonical
ledger without requiring a tracking session. Named periods use half-open
`[since, until)` windows for `today`, `yesterday`, `this-week`, `last-week`,
`this-month`, and `last-month`.

Machine-aware usage views:

- `toktrail usage machines` groups usage by origin machine
- `toktrail usage today --machine <selector>` filters by machine
- `toktrail usage sessions --machine <selector>` and
  `toktrail usage runs --machine <selector>` apply the same filter
- machine selectors accept full IDs, unambiguous ID prefixes (8+ chars),
  exact names, and unambiguous normalized name prefixes

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
toktrail prices list --missing-only
toktrail sources sessions copilot
```

Virtual and pricing-based actual costs are computed at report time, not during
refresh. Updating `prices.toml`, files under `prices/`, or `config.toml`
immediately changes future `status` and `sessions` output for already imported
data without re-importing source files.

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
