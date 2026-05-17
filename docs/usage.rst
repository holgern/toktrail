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
   toktrail sync git init --repo ~/toktrail-state --remote git@github.com:me/toktrail-state.git
   cd ~/toktrail-state && git pull && git push
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
   toktrail usage areas --today
   toktrail usage today --area work
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
Area hierarchy rows, source-session assignments, machine-scoped active areas,
and usage-event ``area_id`` values round-trip through sync archives.

Area identity uses two forms:

- ``area_id`` / ``local_id`` is machine-local sqlite row identity.
- ``sync_id`` / ``stable_id`` is durable cross-machine area identity.

Use area paths (for example ``work/odoo``) as the human selector.

Git sync
--------

Use ``toktrail sync git`` to exchange deterministic text state files through a
Git repository while keeping the live sqlite state database local:

.. code-block:: bash

   toktrail sync git init --repo ~/toktrail-state --remote git@github.com:me/toktrail-state.git
   cd ~/toktrail-state
   git pull
   git push

On another machine:

.. code-block:: bash

   toktrail sync git init --repo ~/toktrail-state --remote git@github.com:me/toktrail-state.git
   cd ~/toktrail-state
   git pull

Hooks are clone-local. Run ``toktrail sync git init`` (or
``toktrail sync git hooks install``) on each machine/clone.

Do not commit live sqlite files (``toktrail.db``, ``toktrail.db-wal``,
``toktrail.db-shm``) into the sync repository.

To share costing files across machines while keeping import paths local:

.. code-block:: toml

   [sync.git]
   repo = "~/toktrail-state"
   track = ["prices", "provider-prices", "subscriptions"]

toktrail then uses ``<repo>/config/prices.toml``, ``<repo>/config/prices/*.toml``,
and ``<repo>/config/subscriptions.toml``.

Compatibility commands for explicit orchestration/recovery remain available:

.. code-block:: bash

   toktrail sync git import-local
   toktrail sync git export-local --no-refresh
   toktrail sync git pull
   toktrail sync git push
   toktrail sync git sync

Use ``toktrail refresh`` for explicit/manual refresh operation. It reads enabled
harnesses and source paths from ``config.toml``:

.. code-block:: toml

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
   toktrail sync git status --repo ~/toktrail-state
   toktrail sync git sync --repo ~/toktrail-state
   toktrail sync export --out toktrail-state.tar.gz --no-refresh
   toktrail sync import toktrail-state.tar.gz --dry-run --json
   toktrail run list
   toktrail sources sessions opencode
   toktrail sources sessions pi
   toktrail sources sessions codex
   toktrail sources sessions code
   toktrail sources sessions goose
   toktrail refresh --harness harnessbridge --source ~/.harnessbridge/sessions
   toktrail refresh --harness code --source ~/.code/sessions
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
   toktrail usage areas --today
   toktrail usage areas --today --direct
   toktrail usage areas --today --leaves --percent --share-by tokens
   toktrail usage summary --area work
   toktrail usage summary --area work --area-exact
   toktrail usage summary --unassigned-area

The default sessions output is line-based. Use ``--table`` for the wide tabular
view. In table mode, ``usage sessions`` includes an ``area`` column.

Area workflow
-------------

Areas provide a persistent hierarchy for usage classification.

.. code-block:: bash

   toktrail area create work/odoo
   toktrail area use work/odoo
   toktrail area use work/odoo --ttl 4h
   toktrail area status
   toktrail area assign work/odoo --harness opencode --source-session-id ses-1
   toktrail area assign work/odoo --session "pc1/opencode/ses-1"
   toktrail area unassign --harness opencode --source-session-id ses-1
   toktrail area sessions --unassigned --today
   toktrail area bulk-assign work/odoo --unassigned --today --dry-run
   toktrail area bulk-assign work/odoo --unassigned --today --apply
   toktrail area detect

Key behavior:

- ``toktrail area use`` is machine-scoped. It affects new source sessions
  imported on that machine.
- ``toktrail area use --ttl`` and ``--until`` set expiring active areas.
- Existing imported sessions stay unchanged until explicitly assigned.
- ``toktrail area assign --session`` accepts keys printed by ``usage sessions``
  and ``area sessions``.
- ``toktrail area assign --last`` defaults to the local machine; use
  ``--all-machines`` to restore global latest-session behavior.
- ``--area <path>`` includes descendants by default.
- ``--area-exact`` matches only the exact area path.
- ``--unassigned-area`` filters events with no area assignment.
- ``toktrail usage areas`` exposes direct-vs-subtree totals in both JSON and
  human output.


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

When ``[sync.git].track`` includes ``"provider-prices"``, default output is
``<repo>/config/prices/<provider>.toml``.

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
