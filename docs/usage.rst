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
   toktrail refresh
   toktrail run status
   toktrail usage today
   toktrail subscriptions
   toktrail sync export --out toktrail-state.tar.gz
   toktrail sessions
   toktrail run stop

Report commands refresh configured sources first by default:

.. code-block:: bash

   toktrail usage today
   toktrail run status
   toktrail subscriptions
   toktrail pricing list --used-only
   toktrail pricing list --missing-only

Use ``--no-refresh`` to read stale local state without touching source logs:

.. code-block:: bash

   toktrail usage today --no-refresh
   toktrail run status --no-refresh
   toktrail subscriptions --no-refresh

Use ``--refresh-details`` to show a compact refresh summary before the report.

``toktrail sync import`` validates archive paths, manifest checksums, schema
version, and usage-event fingerprints before merging.

Use ``toktrail refresh`` for explicit/manual refresh operation. It reads enabled
harnesses and source paths from ``config.toml``:

.. code-block:: toml

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
   toktrail subscriptions --provider opencode-go --json
   toktrail sync export --out toktrail-state.tar.gz --no-refresh
   toktrail sync import toktrail-state.tar.gz --dry-run --json
   toktrail sessions
   toktrail sessions opencode
   toktrail sessions pi
   toktrail sessions codex
   toktrail sessions goose
   toktrail refresh --harness goose --source ~/.local/share/goose/sessions/sessions.db
   toktrail refresh --harness droid --source ~/.factory/sessions
   toktrail refresh --harness amp --source ~/.local/share/amp/threads
   toktrail pricing list
   toktrail pricing list --missing-only
   toktrail pricing parse --provider zai --input zai-pricing.md

For harness-session inspection, use ``toktrail sessions <harness>`` to inspect
raw harness sessions without mutating toktrail state.


Provider subscription status
----------------------------

Use ``toktrail subscriptions`` to inspect configured ``[[subscriptions]]`` quota
windows. The command reports configured windows (for example ``5h``, ``weekly``,
``monthly``, and ``yearly``) per subscription, including ``fixed`` and
``first_use`` reset status, used, remaining, and over-limit values.

.. code-block:: bash

   toktrail subscriptions
   toktrail subscriptions --provider opencode-go
   toktrail subscriptions --period 5h --json
   toktrail subscriptions --period monthly --json

If ``fixed_cost_usd`` is configured for a subscription, the output also
includes a billing row with fixed fee, value, net savings, and break-even
progress for the configured billing period.

Pricing parser
--------------

Use ``toktrail pricing parse`` to convert provider pricing text into provider
price files under ``prices/<provider>.toml`` by default:

.. code-block:: bash

   toktrail pricing parse --provider openai --tier standard --input openai-pricing.jsx
   toktrail pricing parse --provider zai --input zai-pricing.md
   toktrail pricing parse --provider opencode-go --table actual --input opencode-go.txt
   toktrail pricing parse --provider openai --input openai-pricing.jsx --output -

Manual overrides still belong in ``prices.toml``. toktrail loads provider files
first and ``prices.toml`` last, so manual rows override generated rows.
