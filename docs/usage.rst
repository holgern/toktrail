Usage
=====

Canonical command flow
----------------------

The preferred CLI workflow is:

.. code-block:: bash

   toktrail init
   toktrail config init
   toktrail sources
   toktrail run start --name <name>
   toktrail run start --name codex-task --harness codex
   toktrail run start --name openai-gpt --provider openai --model gpt-5.5
   toktrail refresh
   toktrail run status
   toktrail usage today
   toktrail subscriptions status
   toktrail sync export --out toktrail-state.tar.gz
   toktrail run list
   toktrail run list --archived
   toktrail run stop
   toktrail run archive 42
   toktrail run unarchive 42

Report commands refresh configured sources first by default:

.. code-block:: bash

   toktrail usage today
   toktrail usage runs --archived
   toktrail run status
   toktrail subscriptions status
   toktrail prices list --used-only
   toktrail prices list --missing-only

Use ``--no-refresh`` to read stale local state without touching source logs:

.. code-block:: bash

   toktrail usage today --no-refresh
   toktrail usage statusline
   toktrail usage statusline --provider openai --basis virtual
   toktrail usage statusline --json
   toktrail stats --format json
   toktrail refresh --full
   toktrail sources skipped

   toktrail usage today --no-refresh
   toktrail run status --no-refresh
   toktrail subscriptions status --no-refresh

Use ``--refresh-details`` to show a compact refresh summary before the report.

``toktrail sync import`` validates archive paths, manifest checksums, schema
version, and usage-event fingerprints before merging.

Use ``toktrail refresh`` for explicit/manual refresh operation. It reads enabled
harnesses and source paths from ``config.toml``:

.. code-block:: toml

   [imports]
   harnesses = ["opencode", "pi", "copilot", "codex", "goose", "harnessbridge", "droid", "amp", "claude", "vibe"]
   missing_source = "warn"
   include_raw_json = false

   [imports.sources]
   opencode = ["~/.local/share/opencode/opencode.db", "~/.local/share/opencode/opencode-stable.db"]
   pi = ["~/.pi/agent/sessions", "~/.omp/agent/sessions"]
   copilot = "~/.copilot/otel"
   codex = ["~/.codex/sessions", "~/.codex/archived_sessions"]
   goose = "~/.local/share/goose/sessions/sessions.db"
   harnessbridge = "~/.harnessbridge/sessions"
   droid = "~/.factory/sessions"
   amp = "~/.local/share/amp/threads"
   claude = "~/.claude/projects"
   vibe = "~/.vibe/logs/session"

``imports.sources.<harness>`` accepts either a single path string or a list of
paths. Use ``toktrail refresh --harness <name> --source <path>`` for one-off
refreshes. The canonical pre-release CLI no longer documents harness-specific
compatibility commands.

Core commands
-------------

.. code-block:: bash

   toktrail run status
   toktrail run status --json
   toktrail run status --split-thinking
   toktrail usage today
   toktrail usage last-week --utc --json
   toktrail subscriptions status --provider opencode-go --json
   toktrail sync export --out toktrail-state.tar.gz --no-refresh
   toktrail sync import toktrail-state.tar.gz --dry-run --json
   toktrail run list
   toktrail sources sessions opencode
   toktrail sources sessions pi
   toktrail sources sessions codex
   toktrail sources sessions goose
   toktrail refresh --harness harnessbridge --source ~/.harnessbridge/sessions
   toktrail refresh --harness goose --source ~/.local/share/goose/sessions/sessions.db
   toktrail refresh --harness droid --source ~/.factory/sessions
   toktrail refresh --harness amp --source ~/.local/share/amp/threads
   toktrail prices list
   toktrail prices list --missing-only
   toktrail prices parse --provider zai --input zai-pricing.md

For harness-session inspection, use ``toktrail sources sessions <harness>`` to inspect
raw harness sessions without mutating toktrail state.

Session usage by period
-----------------------

.. code-block:: bash

   toktrail usage sessions --today
   toktrail usage sessions --yesterday
   toktrail usage sessions --this-week
   toktrail usage sessions --last-week
   toktrail usage sessions --this-month
   toktrail usage sessions --last-month
   toktrail usage sessions --today --table

The default sessions output is line-based. Use ``--table`` for the wide tabular
view.


Provider subscription status
----------------------------

Use ``toktrail subscriptions status`` to inspect configured ``[[subscriptions]]`` quota
windows. The command reports configured windows (for example ``5h``, ``weekly``,
``monthly``, and ``yearly``) per subscription, including ``fixed`` and
``first_use`` reset status, used, remaining, and over-limit values.

Window calculations use each subscription plan timezone from config. Human output
renders timestamps in local timezone by default. Use ``--timezone`` or ``--utc``
to control the display timezone.

.. code-block:: bash

   toktrail subscriptions status
   toktrail subscriptions status --timezone Europe/Berlin
   toktrail subscriptions status --provider opencode-go
   toktrail subscriptions status --period 5h --json
   toktrail subscriptions status --period monthly --json

If ``fixed_cost_usd`` is configured for a subscription, the output also
includes a billing row with fixed fee, value, net savings, and break-even
progress for the configured billing period.

Pricing parser
--------------

Use ``toktrail prices parse`` to convert provider pricing text into provider
price files under ``prices/<provider>.toml`` by default:

.. code-block:: bash

   toktrail prices parse --provider openai --tier standard --input openai-pricing.jsx
   toktrail prices parse --provider zai --input zai-pricing.md
   toktrail prices parse --provider opencode-go --table actual --input opencode-go.txt
   toktrail prices parse --provider openai --input openai-pricing.jsx --output -

Manual overrides still belong in ``prices.toml``. toktrail loads provider files
first and ``prices.toml`` last, so manual rows override generated rows.

When provider pricing exposes context-length tiers (for example short vs long
context), toktrail keeps multiple rows for the same canonical model and resolves
tiers per usage event using prompt-like tokens
(``input + cache_read + cache_write``).
