API reference
=============

This page is the reference for the stable Python API. For task-oriented usage,
see :doc:`api_usage`. For source-specific details, see :doc:`harnesses`.

The public import boundary is ``toktrail.api`` and ``toktrail.errors``.
Everything under ``toktrail.db``, ``toktrail.adapters.*``, ``toktrail.cli``,
``toktrail.config``, ``toktrail.paths``, and ``toktrail.reporting`` is
internal.

The stable API is under ``toktrail.api.*`` and canonical errors are in
``toktrail.errors``. Downstream tools should not import from ``toktrail.db``,
``toktrail.models``, ``toktrail.reporting``, ``toktrail.paths``,
``toktrail.config``, ``toktrail.cli``, or ``toktrail.adapters.*``.

Runnable manual-run examples for the stable API are documented in
`stable_api_examples.md <stable_api_examples.md>`_.

.. automodule:: toktrail.errors
   :members:

.. automodule:: toktrail.api.models
   :members:

.. automodule:: toktrail.api.config
   :members:

.. automodule:: toktrail.api.harnesses
   :members:

.. automodule:: toktrail.api.paths
   :members:

.. automodule:: toktrail.api.sessions
   :members:

.. automodule:: toktrail.api.sources
   :members:

.. automodule:: toktrail.api.imports
   :members:

.. automodule:: toktrail.api.events
   :members:

.. automodule:: toktrail.api.sync
   :members:

.. automodule:: toktrail.api.reports
   :members:

.. automodule:: toktrail.api.areas
   :members:

The reports module includes ``subscription_usage_report()`` for provider
subscription quota windows and ``usage_report()``/``session_report()`` for
run and period usage summaries with provider-level breakdowns.

The sessions module includes scoped run lifecycle helpers
(``start_run(..., scope=RunScope(...))``, ``archive_run()``,
``unarchive_run()``, and archive-aware ``list_runs()`` filters).

The sync module provides ``export_state_archive()`` and
``import_state_archive()`` for cross-machine state archive workflows.
The areas module provides hierarchical area management and source-session
assignment helpers.

.. automodule:: toktrail.api.environment
   :members:

.. automodule:: toktrail.api.workflow
   :members:
