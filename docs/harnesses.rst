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


Reader matrix
-------------

The following table summarizes all 11 registry entries with their key
discovery, format, and watch properties.

.. list-table::
   :header-rows: 1
   :widths: auto

   * - Reader
     - Display name
     - Source kind
     - Default path
     - Env overrides
     - Config key
     - File patterns
     - Watch subdirs
     - Source cost
   * - amp
     - Amp
     - json
     - ``~/.local/share/amp/threads``
     - ``TOKTRAIL_AMP_THREADS``
     - ``amp_threads``
     - ``*.json``
     - (none)
     - yes (credits)
   * - claude
     - Claude Code
     - mixed
     - ``~/.claude/projects``
     - ``TOKTRAIL_CLAUDE_PROJECTS``
     - ``claude_projects``
     - ``*.jsonl``, ``*.json``
     - ``.``
     - config only
   * - code
     - Code
     - mixed
     - ``~/.code/sessions``
     - ``TOKTRAIL_CODE_SESSIONS``, ``CODE_HOME``
     - ``code_sessions``
     - ``*.json``, ``*.jsonl``
     - ``.``, ``archived_sessions``
     - config only
   * - codex
     - Codex
     - mixed
     - ``~/.codex/sessions``
     - ``TOKTRAIL_CODEX_SESSIONS``
     - ``codex_sessions``
     - ``*.json``, ``*.jsonl``
     - ``.``, ``archived_sessions``
     - config only
   * - copilot
     - Copilot
     - mixed
     - ``~/.copilot/otel``
     - ``TOKTRAIL_COPILOT_FILE``, ``COPILOT_OTEL_FILE_EXPORTER_PATH``, ``TOKTRAIL_COPILOT_OTEL_DIR``
     - ``copilot_otel``
     - ``*.jsonl``, ``*.json``
     - (none)
     - config only
   * - droid
     - Droid
     - json
     - ``~/.factory/sessions``
     - ``TOKTRAIL_DROID_SESSIONS``
     - ``droid_sessions``
     - ``*.settings.json``
     - (none)
     - config only
   * - goose
     - Goose
     - sqlite
     - ``~/.local/share/goose/sessions/sessions.db``
     - ``TOKTRAIL_GOOSE_SESSIONS``, ``GOOSE_PATH_ROOT``
     - ``goose_sessions``
     - ``*.db``
     - (none)
     - config only
   * - harnessbridge
     - Harnessbridge
     - jsonl
     - ``~/.harnessbridge/sessions``
     - ``TOKTRAIL_HARNESSBRIDGE_SESSIONS``
     - ``harnessbridge_sessions``
     - ``*.jsonl``
     - ``.``
     - yes
   * - opencode
     - OpenCode
     - sqlite
     - ``~/.local/share/opencode/opencode*.db``
     - ``XDG_DATA_HOME``
     - ``opencode_db``
     - ``*.db``
     - (none)
     - yes
   * - pi
     - Pi
     - mixed
     - ``~/.pi/agent/sessions``, ``~/.omp/agent/sessions``
     - ``TOKTRAIL_PI_SESSIONS``
     - ``pi_sessions``
     - ``*.jsonl``, ``*.json``
     - ``.``
     - config only
   * - vibe
     - Vibe
     - directory
     - ``~/.vibe/logs/session``
     - ``TOKTRAIL_VIBE_LOGS``
     - ``vibe_logs``
     - ``meta.json``
     - (none)
     - yes

.. note::

   "Source cost" indicates whether the harness source data includes a cost
   field (such as ``credits``, ``cost``, or ``source_cost_usd``) that
   toktrail imports alongside token data. When absent, toktrail falls back to
   configured pricing. Harnesses marked "yes" may still fall back to config
   pricing when the source cost field is missing or zero.


Per-reader reference
--------------------

The following subsections document source format, session-id derivation,
token/cost fields, skipped content, and known limitations for each reader.


Amp
~~~

**Source format**: JSON thread files in ``~/.local/share/amp/threads``. Each
file (``T-*.json``) contains a top-level ``messages`` array with
``ledger_record`` and ``message_record`` entries per turn.

**Session-id derivation**: File stem of ``T-*.json`` (e.g., ``T-abc123`` →
``amp:T-abc123``).

**Token fields**: ``input_tokens``, ``output_tokens``,
``cache_read_input_tokens``, ``cache_creation_input_tokens`` (mapped to
cache-write), ``reasoning_tokens``. Parsed from both ``ledger_record`` and
``message_record`` usage objects.

**Cost fields**: ``credits`` field on ledger-level and message-level records.
The higher non-zero value wins. Falls back to config pricing when both are
zero.

**Skipped content**: Non-assistant messages, messages without a usage object,
files that are not valid JSON.

**Known limitations**: The Amp JSON format can vary between clients. Cost from
source is treated as experimental; config pricing is the canonical fallback.
Only ``*.json`` files are scanned; ``*.jsonl`` files in the threads directory
are ignored.


Claude Code
~~~~~~~~~~~

**Source format**: JSONL files in ``~/.claude/projects``, one file per
session directory. Each line is a Claude API streaming event
(``message_start``, ``content_block_start``, ``content_block_delta``,
``message_delta``, etc.). ``*.meta.json`` files are ignored.

**Session-id derivation**: The session directory name under the projects root,
joined with the file stem. Example: ``project-name/session-id`` →
``claude:project-name/session-id``.

**Token fields**: ``input_tokens`` (from ``message_start``),
``output_tokens`` (from ``message_delta``),
``cache_read_input_tokens`` (from ``message_start``),
``cache_creation_input_tokens`` (from ``message_start``, mapped to
cache-write), ``cache_output_tokens`` (from ``message_delta``). Headless
sessions use a state-accumulation model with the same fields.

**Cost fields**: Not present in Claude streaming events. Config pricing only.

**Skipped content**: Non-token streaming events, non-assistant turns, files
matching ``*.meta.json``, non-JSONL files.

**Known limitations**: Claude API streaming format is complex and
version-sensitive. Multi-file sessions (where a single conversation spans
multiple JSONL files) are fully supported but require the entire project
directory to be present. Headless sessions use a state-machine parser that
differs from the interactive streaming parser.


Code
~~~~

**Source format**: JSON and JSONL session files in ``~/.code/sessions`` (plus
``~/.code/archived_sessions``). Code is the Every Code
(``just-every/code``) harness. It reuses the Codex-compatible session parser
while keeping imported usage under the distinct harness name ``code``.

**Session-id derivation**: File stem of the session file (e.g.,
``session-abc.json`` → ``code:session-abc``).

**Token fields**: ``input_tokens``, ``output_tokens``,
``cached_input_tokens`` (max of ``cached_input_tokens`` and
``cache_read_input_tokens``), ``reasoning_output_tokens``. Reuses Codex parser
semantics.

**Cost fields**: Not present in Code session files. Config pricing only.

**Skipped content**: Non-assistant turns, entries without usage data, delta
sessions where per-entry token computation produces zero tokens for
unrecognized checkpoints.

**Known limitations**: The Code harness reuses the Codex parser, so all Codex
parser limitations apply. Harness identity is normalized to ``code`` at
import time. ``CODE_HOME`` env var can redirect the sessions root; when set,
``TOKTRAIL_CODE_SESSIONS`` takes precedence.


Codex
~~~~~

**Source format**: JSON and JSONL session files in ``~/.codex/sessions``
(plus ``~/.codex/archived_sessions``). Each file is a JSON array of session
entries or one JSON object per line.

**Session-id derivation**: File stem (e.g., ``session-xyz.jsonl`` →
``codex:session-xyz``).

**Token fields**: ``input_tokens``, ``output_tokens``,
``cached_input_tokens`` (the max of ``cached_input_tokens`` and
``cache_read_input_tokens``, mapped to cache-read),
``reasoning_output_tokens``.

**Cost fields**: Not present in Codex session files. Config pricing only.

**Skipped content**: Non-assistant entries, entries without a
recognizable usage object, delta-mode sessions where checkpoint deltas
compute to zero.

**Known limitations**: Codex delta-mode sessions store cumulative token
counts at checkpoints, so the parser computes deltas between successive
entries. Malformed or out-of-order checkpoints produce zero-delta entries
that are dropped. ``TOKTRAIL_CODEX_SESSIONS`` path override replaces all
default roots.


Copilot
~~~~~~~

**Source format**: OTEL JSONL files in ``~/.copilot/otel`` (or via
``TOKTRAIL_COPILOT_FILE`` for a single file, ``COPILOT_OTEL_FILE_EXPORTER_PATH``
or ``TOKTRAIL_COPILOT_OTEL_DIR`` for a directory). Each line is an OTEL span
JSON object with ``gen_ai.usage.*`` attributes.

**Session-id derivation**: OTEL span ``session.id`` attribute, falling back
to ``trace_id`` when the session attribute is missing.

**Token fields**: ``gen_ai.usage.input_tokens``,
``gen_ai.usage.output_tokens``. Cache and reasoning tokens are not exposed
in current Copilot OTEL spans.

**Cost fields**: Not present in Copilot OTEL spans. Config pricing only.

**Skipped content**: Non-chat spans, spans without token attributes, spans
with zero tokens across all fields.

**Known limitations**: OTEL span format varies between Copilot CLI versions.
Older versions may omit session IDs or use different attribute naming.
Copilot does not expose cache-read, cache-write, or reasoning tokens in its
OTEL output.


Droid
~~~~~

**Source format**: JSON settings files (``*.settings.json``) in
``~/.factory/sessions``. Each file contains a ``conversation`` array with
``role``, ``content``, and ``usage`` fields per turn.

**Session-id derivation**: File stem of ``*.settings.json`` (e.g.,
``abc123.settings.json`` → ``droid:abc123``).

**Token fields**: ``usage.input_tokens``, ``usage.output_tokens``. No cache
or reasoning token fields in the Droid session format.

**Cost fields**: Not present in Droid settings files. Config pricing only.

**Skipped content**: Non-assistant turns, turns without a ``usage`` object,
non-``*.settings.json`` files.

**Known limitations**: Droid does not expose cache tokens or reasoning tokens.
The session format is determined by the Droid settings file schema; custom
or future format changes may cause unrecognized fields to be skipped without
error.


Goose
~~~~~

**Source format**: SQLite ``sessions.db`` in
``~/.local/share/goose/sessions/sessions.db`` (Linux), ``~/Library/Application
Support/goose/sessions/sessions.db`` (macOS), or
``~/.local/share/Block/goose/sessions/sessions.db`` (Block legacy). The
database contains ``sessions`` and ``messages`` tables.

**Session-id derivation**: The ``sessions.id`` primary key, prefixed with
``goose:``.

**Token fields**: ``input_tokens``, ``output_tokens`` extracted from the
message metadata JSON blob. Cache and reasoning tokens are not exposed in
the Goose session schema.

**Cost fields**: Not present in Goose session data. Config pricing only.

**Skipped content**: Non-assistant messages, messages without token metadata,
database open errors (gracefully handled with empty results).

**Known limitations**: Goose sessions are stored in a single SQLite database,
so path discovery targets the database file, not a directory of session
files. macOS and Block legacy paths are probed automatically but may not
exist on all systems. ``TOKTRAIL_GOOSE_SESSIONS`` overrides the file path;
``GOOSE_PATH_ROOT`` overrides the root directory (appending
``data/sessions/sessions.db``).


Harnessbridge
~~~~~~~~~~~~~

**Source format**: JSONL ledger files in ``~/.harnessbridge/sessions``. Each
file contains session-header lines and message rows. Harnessbridge is a ledger
source that aggregates usage from multiple harnesses into a unified accounting
stream.

**Session-id derivation**: From the ``session_id`` field in the session header
line. Prefixed with ``harnessbridge:``.

**Token fields**: ``input_tokens``, ``output_tokens``, ``cache_read_tokens``,
``cache_write_tokens``, ``reasoning_tokens``. Message rows carry the full
token breakdown.

**Cost fields**: ``source_cost_usd`` on message rows, plus nested cost objects
(``cost.total_tokens``, ``native_usage.cost.total_tokens``). Falls back to
config pricing when no source cost is present.

**Skipped content**: Rows with ``accounting`` ≠ ``"primary"``, non-chat rows,
session header lines without a valid session ID.

**Known limitations**: Only primary accounting rows are imported; secondary or
reference rows are discarded. The inner harness name from the ledger row is
preserved so reporting groups by the original harness, not by Harnessbridge.
All imported rows share the Harnessbridge source-session identity.


OpenCode
~~~~~~~~

**Source format**: SQLite database ``~/.local/share/opencode/opencode*.db``.
The ``message`` table stores each interaction with a JSON ``data`` column
containing the full message payload.

**Session-id derivation**: The ``session_id`` column in the ``message`` table,
prefixed with ``opencode:``.

**Token fields**: ``usage.input_tokens``, ``usage.output_tokens``,
``usage.cache_read_input_tokens``, ``usage.cache_write_tokens``,
``usage.reasoning_tokens``. All parsed from the ``data`` JSON column.

**Cost fields**: ``cost`` field in the message payload JSON. Falls back to
config pricing when absent or zero.

**Skipped content**: Non-assistant roles (``user``, ``system``), messages
without a ``usage`` object, database open errors (gracefully returns empty
results).

**Known limitations**: The database is opened read-only with ``PRAGMA
query_only = ON``. Multiple ``opencode*.db`` files in the directory are
scanned independently. The ``XDG_DATA_HOME`` env var overrides the default
path root; combine with ``opencode/opencode.db`` to produce the full path.


Pi
~~

**Source format**: JSONL session files in ``~/.pi/agent/sessions`` and
``~/.omp/agent/sessions``. Each line is a JSON object representing a single
message or turn. ``*.settings.json`` files are ignored via the
``ignored_patterns`` list.

**Session-id derivation**: The ``session_id`` field in the message JSON,
falling back to the file name stem when the field is missing. Prefixed
with ``pi:``.

**Token fields**: ``usage.input_tokens``, ``usage.output_tokens``,
``usage.cache_read_input_tokens``, ``usage.cache_write_tokens``,
``usage.reasoning_tokens``.

**Cost fields**: Not present in Pi session messages. Config pricing only.

**Skipped content**: Non-assistant messages, malformed JSON lines,
``*.settings.json`` files, lines without a ``usage`` object.

**Known limitations**: Pi and OMP session directories are scanned
independently. If both directories contain the same session data (e.g.,
symlinks or copies), duplicate sessions may appear in source listings.
Individual file-level import state tracking prevents double-import of the
same file bytes.


Vibe
~~~~

**Source format**: Directory-based session storage in
``~/.vibe/logs/session``. Each session is a subdirectory containing a
``meta.json`` file and conversation JSON files.

**Session-id derivation**: The session subdirectory name (e.g.,
``session-2025-01-01T00-00-00`` → ``vibe:session-2025-01-01T00-00-00``).

**Token fields**: ``input_tokens``, ``output_tokens``,
``cache_read_input_tokens``, ``cache_write_tokens``,
``reasoning_output_tokens``. Parsed from ``token_usage`` objects in
assistant turns within conversation files.

**Cost fields**: Cost fields (``cost_usd``, ``input_cost``, ``output_cost``)
on individual turns. Falls back to config pricing when absent.

**Skipped content**: Non-assistant turns, turns without ``token_usage``,
directories without ``meta.json``.

**Known limitations**: Vibe sessions span multiple files per directory. The
parser must aggregate across conversation files within a session directory.
Conversation file naming conventions are not formally specified and may vary
between Vibe versions.


Reader invariants
-----------------

All toktrail readers follow these behavioral invariants:

* **Read-only source access**: Source databases are opened with ``PRAGMA
  query_only = ON``; JSONL files are opened for reading only. No reader
  mutates source harness data.

* **Idempotent imports**: Repeated imports do not duplicate accounting
  events. Each reader produces stable ``source_dedup_key`` and
  ``global_dedup_key`` values that prevent double-counting.

* **Token accounting**: Token fields are always non-negative integers.
  Missing or null token values are treated as zero. Cache-read and
  cache-write tokens are kept distinct from input tokens.

* **Timestamp handling**: Timestamps are normalized to milliseconds since
  epoch. Missing or invalid timestamps use a deterministic fallback
  (typically file modification time or session metadata).

* **Malformed data is skipped**: Malformed JSON, unparseable rows, and
  unrecognized source formats produce skipped-row counts, never fatal errors.
  The source path itself being invalid (nonexistent or unreadable) is the
  only hard error.

* **Source traceability**: Every imported ``UsageEvent`` carries a
  ``source_session_id``, ``source_row_id``, and ``fingerprint_hash`` that
  trace back to the originating source record.

* **Raw JSON privacy**: Raw source JSON is stored only when explicitly
  enabled (``--raw``). The default ``--no-raw`` behavior suppresses raw
  storage for all readers.

* **Config pricing fallback**: When source data lacks cost information (or
  has zero cost), all readers fall back to the configured pricing tables
  in ``[costing]`` config sections.


Adding a reader
---------------

Adding a new harness reader follows this process:

1. **Add the adapter module**: Create a new module under
   ``toktrail/adapters/`` (e.g., ``toktrail/adapters/newharness.py``). The
   module must export:

   * ``scan_newharness_path`` (or ``scan_newharness_sqlite`` for SQLite
     sources) — accepts a ``source_path``, returns a ``ScanResult`` with
     parsed ``UsageEvent`` objects.
   * ``list_newharness_sessions`` — returns ``list[SourceSessionSummary]``
     for source-session inspection.

2. **Add path resolution**: Add a ``resolve_newharness_sessions_path``
   function in ``toktrail/paths.py`` (or add the new harness's env var
   and default path logic to an existing resolver pattern).

3. **Register the harness**: Add a ``HarnessDefinition`` entry in
   ``toktrail/adapters/registry.py`` in the ``HARNESS_REGISTRY`` dict,
   specifying:

   * ``name`` — short canonical name used in CLI (e.g., ``--harness newharness``)
   * ``display_name`` — human-readable name for reports and status output
   * ``default_roots`` — one or more ``PathTemplate`` entries for default
     path discovery
   * ``env_roots`` — ``EnvRoot`` entries for environment-variable overrides
   * ``patterns`` — file patterns for scanning
   * ``source_kind`` — one of ``"json"``, ``"jsonl"``, ``"sqlite"``,
     ``"directory"``, or ``"mixed"``
   * ``resolve_source_path`` — the path resolution function
   * ``scan`` — the scan function
   * ``list_sessions`` — the session-listing function
   * ``config_key`` — config TOML key for ``[imports.sources]``
   * ``id_prefix`` — prefix for source-session IDs
   * ``supports_watch`` — ``True`` if the harness supports file watching
   * ``watch_subdirs`` — subdirectory names to watch recursively
   * ``ignored_patterns`` — file patterns to skip during scanning

4. **Add parser tests**: Create ``tests/test_newharness_parser.py`` with:

   * Happy-path parsing of well-formed source records
   * Token field extraction and normalization
   * Handling of malformed, missing, and zero-token records
   * Session-ID derivation
   * Dedup-key stability
   * Fingerprint hashing
   * ``--no-raw`` behavior

5. **Add CLI and DB tests**: Update ``tests/test_cli.py`` and
   ``tests/test_db.py`` with import/watch/status coverage for the new
   harness. Include source-session listing and detail tests.

6. **Update docs**: Add the new harness to:

   * The reader matrix table above
   * A new per-reader subsection under Per-reader reference
   * ``API.md`` supported harnesses list
   * ``README.md`` supported harnesses section

7. **Run full verification**:

   .. code-block:: bash

      pytest tests/test_newharness_parser.py
      pytest tests/test_cli.py tests/test_db.py
      ruff check --config=.ruff.toml .
      mypy toktrail
