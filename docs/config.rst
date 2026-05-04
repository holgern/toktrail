Configuration
=============

toktrail reads pricing, refresh defaults, and optional provider subscription quota
settings from ``config.toml``.

Key sections:

- ``[imports]``
- ``[imports.sources]``
- ``[costing]``
- ``[[actual_cost]]``
- ``[[pricing.virtual]]``
- ``[[pricing.actual]]``
- ``[[subscriptions]]``

Report commands use ``[imports]`` as their automatic refresh policy by default.
Use ``toktrail refresh`` for manual refresh operations.

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
   default_actual_mode = "source"
   default_virtual_mode = "pricing"
   missing_price = "warn"

   [[subscriptions]]
   provider = "opencode-go"
   display_name = "OpenCode Go"
   timezone = "Europe/Berlin"
   cost_basis = "virtual"
   
   [[subscriptions.windows]]
   period = "5h"
   limit_usd = 10
   reset_mode = "fixed"
   reset_at = "2026-05-01T00:00:00+02:00"

   [[subscriptions.windows]]
   period = "weekly"
   limit_usd = 50
   reset_mode = "fixed"
   reset_at = "2026-05-01T00:00:00+02:00"

   [[subscriptions.windows]]
   period = "monthly"
   limit_usd = 200
   reset_mode = "fixed"
   reset_at = "2026-05-01T00:00:00+02:00"

``[[subscriptions]]`` are config-only definitions. They are not stored in the
state database. Use ``toktrail subscriptions`` to inspect current windows,
used cost, and remaining quota per configured provider.

See ``README.md`` for the canonical CLI workflow and ``API.md`` for the public
Python integration surface.
