Usage
=====

Canonical command flow
----------------------

The preferred CLI workflow is:

.. code-block:: bash

   toktrail init
   toktrail config init
   toktrail sources
   toktrail start --name <name>
   toktrail import
   toktrail status
   toktrail usage today
   toktrail subscriptions
   toktrail sessions
   toktrail stop

Use ``toktrail import`` for normal operation. It reads enabled harnesses and
source paths from ``config.toml``:

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
paths. Use ``toktrail import --harness <name> --source <path>`` for one-off
imports. The canonical pre-release CLI no longer documents harness-specific
compatibility commands.

Core commands
-------------

.. code-block:: bash

   toktrail status
   toktrail status --json
   toktrail status --split-thinking
   toktrail usage today
   toktrail usage last-week --utc --json
   toktrail subscriptions --provider opencode-go --json
   toktrail sessions
   toktrail sessions opencode
   toktrail sessions pi
   toktrail sessions codex
   toktrail sessions goose
   toktrail import --harness goose --source ~/.local/share/goose/sessions/sessions.db
   toktrail import --harness droid --source ~/.factory/sessions
   toktrail import --harness amp --source ~/.local/share/amp/threads
   toktrail pricing list
   toktrail pricing list --missing-only

For harness-session inspection, use ``toktrail sessions <harness>`` to inspect
raw harness sessions without mutating toktrail state.


Provider subscription status
----------------------------

Use ``toktrail subscriptions`` to inspect configured ``[[subscriptions]]`` quota
windows. The command reports daily, weekly, and monthly windows that are
configured for each provider, including used, remaining, and over-limit values.

.. code-block:: bash

   toktrail subscriptions
   toktrail subscriptions --provider opencode-go
   toktrail subscriptions --period monthly --json
