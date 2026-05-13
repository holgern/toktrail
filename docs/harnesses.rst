Harness registry
================

Toktrail keeps harness discovery metadata in ``toktrail.adapters.registry``.
The public harness/source listings expose the metadata used by config, source
discovery, and watch/import commands.

Registry rows include:

* ``config_key``
* ``id_prefix``
* ``watch_subdirs``
* ``shallow_watch``
* ``file_based``
* ``platform_notes``
* effective roots in JSON source listings

Use::

   toktrail sources list --json

The command returns one row per configured harness with discovery metadata and
the resolved effective root when available.

Supported harnesses
--------------------

amp, claude, codex, copilot, droid, goose, harnessbridge, opencode, pi, vibe

Harnessbridge is a ledger source: it scans ``~/.harnessbridge/sessions`` (or
``TOKTRAIL_HARNESSBRIDGE_SESSIONS``) and imports only rows marked
``accounting="primary"``. Imported events keep the inner harness name from the
ledger row so reporting still groups by the actual harness.
