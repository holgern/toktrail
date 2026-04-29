Usage
=====

Canonical command flow
----------------------

The preferred CLI workflow is:

.. code-block:: bash

   toktrail init
   toktrail config init
   toktrail start --name <name>
   toktrail import
   toktrail status
   toktrail usage today
   toktrail sessions
   toktrail sessions <harness>
   toktrail stop

Use ``toktrail import`` for normal operation. It reads enabled harnesses and
source paths from ``config.toml``:

.. code-block:: toml

   [imports]
   harnesses = ["opencode", "pi", "copilot", "codex", "goose", "droid", "amp"]
   missing_source = "warn"
   include_raw_json = false

   [imports.sources]
   opencode = "~/.local/share/opencode/opencode.db"
   pi = "~/.pi/agent/sessions"
   copilot = "~/.copilot/otel"
   codex = "~/.codex/sessions"
   goose = "~/.local/share/goose/sessions/sessions.db"
   droid = "~/.factory/sessions"
   amp = "~/.local/share/amp/threads"

Use ``toktrail import --harness <name> --source <path>`` for one-off imports.
Harness-specific commands such as ``toktrail import codex`` and
``toktrail watch copilot`` remain available for compatibility and advanced
workflows.

Core commands
-------------

.. code-block:: bash

   toktrail status
   toktrail status --json
   toktrail usage today
   toktrail usage last-week --utc --json
   toktrail sessions
   toktrail sessions codex
   toktrail sessions goose
   toktrail sessions droid
   toktrail sessions amp
   toktrail import goose --goose-db ~/.local/share/goose/sessions/sessions.db
   toktrail import droid --droid-path ~/.factory/sessions
   toktrail import amp --amp-path ~/.local/share/amp/threads

For source-session inspection, use ``toktrail sessions <harness>`` to inspect
raw harness sessions without mutating toktrail state.
