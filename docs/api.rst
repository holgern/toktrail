Public Python API
=================

The stable API is under ``toktrail.api.*`` and canonical errors are in
``toktrail.errors``. Downstream tools should not import from ``toktrail.db``,
``toktrail.models``, ``toktrail.reporting``, ``toktrail.paths``,
``toktrail.config``, ``toktrail.cli``, or ``toktrail.adapters.*``.

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

.. automodule:: toktrail.api.environment
   :members:

.. automodule:: toktrail.api.workflow
   :members:
