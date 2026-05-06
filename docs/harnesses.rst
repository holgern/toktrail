Supported harnesses
===================

toktrail imports normalized token usage from local source logs/databases. It
reads sources in read-only mode and stores normalized usage in the toktrail
state SQLite database.

Supported harness names
-----------------------

The stable public harness names are:

- ``opencode``
- ``pi``
- ``copilot``
- ``codex``
- ``goose``
- ``droid``
- ``amp``
- ``claude``
- ``vibe``

Use these names consistently in CLI commands, ``config.toml``, and Python API
calls.

Generic CLI shape
-----------------

.. code-block:: bash

   toktrail refresh --harness codex --source ~/.codex/sessions
   toktrail sources sessions codex
   toktrail sources session codex <source-session-id>
   toktrail watch --harness codex

Generic Python shape
--------------------

.. code-block:: python

   from pathlib import Path

   from toktrail.api import import_usage, list_source_sessions, scan_usage

   source = Path("~/.codex/sessions").expanduser()
   sessions = list_source_sessions("codex", source_path=source, limit=5)
   scan = scan_usage("codex", source_path=source)
   result = import_usage(Path(".toktrail/toktrail.db"), "codex", source_path=source)

Harness reference
-----------------

.. list-table::
   :header-rows: 1

   * - Harness
     - Source kind
     - Default source
     - Env vars
     - Environment setup
     - Cost source
     - Notes
   * - ``opencode``
     - SQLite file
     - ``~/.local/share/opencode/opencode.db``
     - ``XDG_DATA_HOME`` (default candidate)
     - No
     - Imports ``payload.cost`` as ``source_cost_usd``
     - Parses provider/model, input/output/reasoning, cache read/write/output, thinking level, mode/agent.
   * - ``pi``
     - JSONL/JSON directory
     - ``~/.pi/agent/sessions``
     - ``TOKTRAIL_PI_SESSIONS``
     - No
     - No source cost
     - Assistant usage rows with provider/model, input/output/reasoning, cache read/write/output, thinking level.
   * - ``copilot``
     - OTEL JSONL/JSON file or directory
     - ``~/.copilot/otel``
     - ``TOKTRAIL_COPILOT_FILE``, ``COPILOT_OTEL_FILE_EXPORTER_PATH``, ``TOKTRAIL_COPILOT_OTEL_DIR``
     - Yes
     - No source cost
     - Chat spans only; provider inferred from model or falls back to ``github-copilot``.
   * - ``codex``
     - JSON/JSONL directory
     - ``~/.codex/sessions``
     - ``TOKTRAIL_CODEX_SESSIONS``
     - No
     - No source cost
     - Handles cumulative counters, stale regressions, headless rows, cache-read counters, and agent/headless metadata.
   * - ``goose``
     - SQLite file
     - ``~/.local/share/goose/sessions/sessions.db``
     - ``TOKTRAIL_GOOSE_SESSIONS``, ``GOOSE_PATH_ROOT``
     - No
     - No source cost
     - Reads cumulative rows; model from ``model_config_json``; provider from ``provider_name`` or inferred from model.
   * - ``droid``
     - JSON settings directory
     - ``~/.factory/sessions``
     - ``TOKTRAIL_DROID_SESSIONS``
     - No
     - No source cost
     - Reads ``*.settings.json`` token usage/provider/model with thinking/cache fields; can infer model from sibling ``.jsonl``.
   * - ``amp``
     - JSON thread directory
     - ``~/.local/share/amp/threads``
     - ``TOKTRAIL_AMP_THREADS``
     - No
     - Imports ``credits`` as ``source_cost_usd``
     - Reconciles ledger/message usage without double-counting; parses cache fields; provider inferred or defaults to ``anthropic``.
   * - ``claude``
     - JSONL/JSON project directory
     - ``~/.claude/projects``
     - ``TOKTRAIL_CLAUDE_PROJECTS``
     - No
     - No source cost
     - Assistant usage with streaming-row dedup, headless output parsing, sidechain/subagent detection, and cache fields.
   * - ``vibe``
     - Session metadata directory
     - ``~/.vibe/logs/session``
     - ``TOKTRAIL_VIBE_LOGS``
     - No
     - Imports ``stats.session_cost`` or computed price fields as ``source_cost_usd``
     - Reads ``meta.json`` + ``messages.jsonl`` for prompt/completion tokens, provider/model config, thinking setting, and last assistant message id.

Cross-harness rules
-------------------

- Imports are read-only against source logs/databases.
- All harnesses support generic ``toktrail refresh --harness <name> --source <path>`` and ``toktrail watch``.
- Source paths can be file or directory depending on harness.
- ``imports.sources.<harness>`` accepts either a string or a list of strings.
- ``include_raw_json`` defaults to ``False`` in API examples.
- Provider/model identity is source-first; explicit source provider values are not rewritten from model aliases.
- Reporting costs are computed at report time from config/prices/subscriptions.

Cost handling by harness
------------------------

Some harnesses provide a source-side cost value. toktrail stores that as
``source_cost_usd``. Actual and virtual costs are report-time calculations based
on ``config.toml`` and price files.

- Source-cost harnesses today: ``opencode``, ``amp``, ``vibe``.
- Zero-default source-cost harnesses: ``pi``, ``copilot``, ``codex``, ``goose``, ``droid``, ``claude``.

Cache fields
------------

Adapters preserve cache token categories when the source exposes them:

- ``cache_read``: cached prompt/input tokens reused by the provider.
- ``cache_write``: prompt/input tokens written into a provider cache.
- ``cache_output``: cached output tokens when a source exposes that category.

User-facing ``TokenBreakdown.total`` is ``input + output``. Use
``TokenBreakdown.accounting_total`` for full accounting including reasoning and
cache categories.

Copilot environment setup
-------------------------

GitHub Copilot CLI requires file-export OTEL variables. Use either the CLI:

.. code-block:: bash

   toktrail copilot env bash

or the Python API:

.. code-block:: python

   from toktrail.api import prepare_environment

   env = prepare_environment("copilot", shell="bash")
   print("\n".join(env.shell_exports))
