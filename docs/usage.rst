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
   toktrail sessions
   toktrail session show <tracking-session-id>
   toktrail source-sessions --harness <name>
   toktrail source-session show --harness <name> <source-session-id>
   toktrail stop

Use ``toktrail import`` for normal operation. It reads enabled harnesses and
source paths from ``config.toml``:

.. code-block:: toml

   [imports]
   harnesses = ["opencode", "pi", "copilot", "codex", "goose", "droid", "amp"]
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
   toktrail sessions
   toktrail session show 3
   toktrail source-sessions --harness codex
   toktrail source-session show --harness goose goose-session-id --breakdown
   toktrail import --harness goose --source ~/.local/share/goose/sessions/sessions.db
   toktrail import --harness droid --source ~/.factory/sessions
   toktrail import --harness amp --source ~/.local/share/amp/threads
   toktrail models --group-by provider,model
   toktrail pricing check

For source-session inspection, use ``toktrail source-sessions --harness`` and
``toktrail source-session show --harness`` to inspect raw harness sessions
without mutating toktrail state.
