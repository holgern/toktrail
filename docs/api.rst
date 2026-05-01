Public Python API
=================

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

.. automodule:: toktrail.api.reports
   :members:

The reports module includes ``subscription_usage_report()`` for provider
subscription quota windows and ``usage_report()``/``session_report()`` for
run and period usage summaries with provider-level breakdowns.

.. automodule:: toktrail.api.environment
   :members:

.. automodule:: toktrail.api.workflow
   :members:
