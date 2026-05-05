Configuration
=============

toktrail uses three user config files:

- ``config.toml`` for imports + costing policy
- ``prices.toml`` for manual ``[[pricing.virtual]]`` and ``[[pricing.actual]]`` rows
- ``prices/`` for generated provider files such as ``prices/openai.toml``
- ``subscriptions.toml`` for ``[[subscriptions]]`` plans and windows

Use ``toktrail config init`` to create all three files.

Report commands use ``[imports]`` as their automatic refresh policy by default.
Use ``toktrail refresh`` for manual refresh operations.

Example
-------

``config.toml``:

.. code-block:: toml

   config_version = 1

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

``prices.toml`` (manual overrides):

.. code-block:: toml

   config_version = 1

   [[pricing.virtual]]
   provider = "openai"
   model = "gpt-5-mini"
   input_usd_per_1m = 0.25
   output_usd_per_1m = 2.0

``subscriptions.toml``:

.. code-block:: toml

   config_version = 1

   [[subscriptions]]
   id = "opencode-go"
   usage_providers = ["opencode-go"]
   display_name = "OpenCode Go"
   timezone = "Europe/Berlin"
   quota_cost_basis = "virtual"
   fixed_cost_usd = 10
   fixed_cost_period = "monthly"
   fixed_cost_reset_at = "2026-05-01T00:00:00+02:00"
   fixed_cost_basis = "virtual"
   
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

``[[subscriptions]]`` are config-only definitions keyed by ``id`` with explicit
``usage_providers`` coverage. They are not stored in the state database. Use
``toktrail subscriptions`` to inspect current windows, used cost, and remaining
quota per configured subscription.

When ``fixed_cost_usd`` is set, ``toktrail subscriptions`` also reports billing
value, net savings, and break-even progress for the fixed billing period
(``daily``, ``weekly``, ``monthly``, or ``yearly``).

Effective pricing is loaded from ``prices/*.toml`` first and then ``prices.toml``
last. This lets manual rows override generated provider rows when they share the
same provider/model key.

See ``README.md`` for the canonical CLI workflow and ``API.md`` for the public
Python integration surface.
