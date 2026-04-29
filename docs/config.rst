Configuration
=============

toktrail reads pricing and import defaults from ``config.toml``. The key
sections are:

- ``[imports]``
- ``[imports.sources]``
- ``[costing]``
- ``[[actual_cost]]``
- ``[[pricing.virtual]]``
- ``[[pricing.actual]]``

Example
-------

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

   [costing]
   timezone = "UTC"

See ``README.md`` for the canonical CLI workflow and ``API.md`` for the public
Python integration surface.
