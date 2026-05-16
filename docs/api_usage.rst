Using toktrail from Python
==========================

toktrail exposes a stable Python API under ``toktrail.api``. Use this API from
scripts, CI jobs, local dashboards, or wrappers around coding agents. Do not
import from ``toktrail.db``, ``toktrail.adapters.*``, ``toktrail.cli``, or other
internal modules.

Basic configured import and report
----------------------------------

Use this when ``~/.config/toktrail/config.toml`` already lists the harnesses and
source paths to refresh.

.. code-block:: python

   from pathlib import Path

   from toktrail.api import init_state, import_configured_usage, usage_report

   db_path = Path(".toktrail/toktrail.db")

   init_state(db_path)
   refresh_results = import_configured_usage(db_path)
   report = usage_report(db_path, period="today", timezone="Europe/Berlin")

   print(report.totals.tokens.total)
   print(report.totals.tokens.input)
   print(report.totals.tokens.cache_read)
   print(report.totals.costs.virtual_cost_usd)

   for provider in report.by_provider:
       print(provider.provider_id, provider.tokens.total, provider.costs.virtual_cost_usd)

Area management and area-filtered reports
-----------------------------------------

Use the stable area APIs to classify source sessions and filter reports by
hierarchical area paths.

.. code-block:: python

   from pathlib import Path

   from toktrail.api import (
       assign_area_to_session,
       create_area,
       set_active_area,
       usage_areas_report,
       usage_report,
   )

   db_path = Path(".toktrail/toktrail.db")

   create_area("work/odoo", db_path=db_path)
   set_active_area("work/odoo", db_path=db_path)
   assign_area_to_session(
       "work/odoo",
       harness="opencode",
       source_session_id="ses-1",
       db_path=db_path,
   )

   rollup = usage_report(db_path, period="today", area="work")
   exact = usage_report(db_path, period="today", area="work", area_exact=True)
   unassigned = usage_report(db_path, period="today", unassigned_area=True)
   by_area = usage_areas_report(db_path, period="today")

   print(rollup.totals.tokens.total, exact.totals.tokens.total)
   print(unassigned.totals.tokens.total, len(by_area.areas))

Scan a harness source without writing state
-------------------------------------------

Use ``scan_usage()`` when a program only needs to inspect a source log and does
not want to modify the toktrail database.

.. code-block:: python

   from pathlib import Path

   from toktrail.api import scan_usage

   scan = scan_usage(
       "codex",
       source_path=Path("~/.codex/sessions").expanduser(),
   )

   print(scan.rows_seen, len(scan.events))
   for event in scan.events:
       print(event.provider_id, event.model_id, event.tokens.total)

Import one harness and read usage
---------------------------------

Use ``import_usage()`` when the application knows which harness and source path
should be refreshed.

.. code-block:: python

   from pathlib import Path

   from toktrail.api import init_state, import_usage, usage_report

   db_path = Path(".toktrail/toktrail.db")
   source_path = Path("~/.local/share/opencode/opencode.db").expanduser()

   init_state(db_path)
   result = import_usage(db_path, "opencode", source_path=source_path)
   report = usage_report(db_path, period="today", timezone="Europe/Berlin")

   print(result.events_imported, result.events_skipped)
   print(report.totals.as_dict())

Track one embedded run
----------------------

Use a run when a Python script starts work, launches or instructs a coding
agent, then wants usage only for that window.

.. code-block:: python

   from pathlib import Path

   from toktrail.api import (
       import_usage,
       init_state,
       session_report,
       start_run,
       stop_run,
   )

   db_path = Path(".toktrail/toktrail.db")
   source_path = Path("~/.codex/sessions").expanduser()

   init_state(db_path)
   run = start_run(db_path, name="codex-refactor", harnesses=("codex",))

   # Run your tool or ask the user to run the harness here.

   import_usage(
       db_path,
       "codex",
       session_id=run.id,
       source_path=source_path,
       since_start=True,
   )
   report = session_report(db_path, run.id)
   stop_run(db_path, run.id)

   print(report.totals.tokens.total)
   print(report.totals.costs.virtual_cost_usd)

Record token usage produced by your own Python code
---------------------------------------------------

Use ``record_usage_event()`` when your application already has token counters
from direct LLM API calls and wants toktrail to store/report them.

.. code-block:: python

   from decimal import Decimal
   from pathlib import Path

   from toktrail.api import TokenBreakdown, init_state, record_usage_event, usage_report

   db_path = Path(".toktrail/toktrail.db")
   init_state(db_path)

   record_usage_event(
       db_path,
       harness="my-app",
       source_session_id="batch-2026-05-06",
       source_message_id="req_123",
       provider_id="openai",
       model_id="gpt-5.5",
       tokens=TokenBreakdown(
           input=12_000,
           output=800,
           reasoning=120,
           cache_read=50_000,
       ),
       source_cost_usd=Decimal("0.0123"),
   )

   today = usage_report(db_path, period="today", timezone="Europe/Berlin")
   print(today.totals.tokens.total)

Manual harness workflow
-----------------------

Use ``prepare_manual_run()`` and ``finalize_manual_run()`` when a wrapper script
asks a user to run an external harness manually and then imports the changed
source session.

.. code-block:: python

   from pathlib import Path

   from toktrail.api import finalize_manual_run, prepare_manual_run

   db_path = Path(".toktrail/toktrail.db")

   prepared = prepare_manual_run(
       db_path,
       "copilot",
       name="copilot-investigation",
       shell="bash",
   )

   for line in prepared.environment.shell_exports:
       print(line)

   input("Run the harness now, then press Enter...")

   finalized = finalize_manual_run(db_path, prepared)
   print(finalized.source_session.source_session_id)
   print(finalized.report.totals.tokens.total)

Reports as dictionaries
-----------------------

All public result models expose ``as_dict()`` for JSON serialization.

.. code-block:: python

   import json

   payload = report.as_dict()
   print(json.dumps(payload, indent=2, default=str))

Privacy defaults
----------------

Raw source JSON is opt-in. Keep ``include_raw_json=False`` unless debugging a
parser. Public ``UsageEvent.as_dict()`` omits raw JSON unless explicitly called
with ``include_raw_json=True``.

Error handling
--------------

Catch ``ToktrailError`` for expected toktrail failures.

.. code-block:: python

   from toktrail.errors import ToktrailError

   try:
       report = usage_report(db_path, period="today")
   except ToktrailError as exc:
       print(f"toktrail failed: {exc}")
