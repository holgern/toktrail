from __future__ import annotations

import importlib
from pathlib import Path

import toktrail.api as public_api
from toktrail.api.harnesses import supported_harnesses
from toktrail.api.models import (
    CostTotals,
    TokenBreakdown,
    UsageEvent,
)
from toktrail.api.paths import (
    default_amp_threads_path,
    default_droid_sessions_path,
    default_goose_sessions_db_path,
    default_source_path,
    resolve_source_path,
)


def test_public_modules_import_successfully() -> None:
    for module_name in (
        "toktrail.errors",
        "toktrail.api",
        "toktrail.api.models",
        "toktrail.api.paths",
        "toktrail.api.config",
        "toktrail.api.harnesses",
        "toktrail.api.sessions",
        "toktrail.api.sources",
        "toktrail.api.imports",
        "toktrail.api.reports",
        "toktrail.api.environment",
        "toktrail.api.workflow",
    ):
        assert importlib.import_module(module_name) is not None


def test_public_models_star_export_includes_documented_models() -> None:
    namespace: dict[str, object] = {}
    exec("from toktrail.api.models import *", namespace, namespace)

    assert "UnconfiguredModelRow" in namespace
    assert "TrackingSessionReport" in namespace
    assert "FinalizedManualRun" in namespace
    assert "UsageSeriesBucket" in namespace
    assert "UsageSeriesInstance" in namespace
    assert "UsageSeriesReport" in namespace


def test_root_api_exports_documented_models_and_functions() -> None:
    required = {
        "ActivitySummaryRow",
        "CostTotals",
        "FinalizedManualRun",
        "HarnessDefinition",
        "HarnessEnvironment",
        "HarnessSummaryRow",
        "ImportUsageResult",
        "ModelSummaryRow",
        "PreparedManualRun",
        "ScanUsageResult",
        "SessionTotals",
        "SourceSessionDiff",
        "SourceSessionSnapshot",
        "SourceSessionSummary",
        "TokenBreakdown",
        "TrackingSession",
        "TrackingSessionReport",
        "UnconfiguredModelRow",
        "UsageEvent",
        "UsageSeriesBucket",
        "UsageSeriesInstance",
        "UsageSeriesReport",
        "capture_source_snapshot",
        "config_exists",
        "config_summary",
        "default_source_path",
        "default_toktrail_config_path",
        "default_toktrail_db_path",
        "diff_source_snapshots",
        "finalize_manual_run",
        "get_active_session",
        "get_harness_definition",
        "get_session",
        "import_configured_usage",
        "import_usage",
        "init_config",
        "init_state",
        "is_supported_harness",
        "list_sessions",
        "list_source_sessions",
        "normalize_harness_name",
        "prepare_environment",
        "prepare_manual_run",
        "render_config_template",
        "require_active_session",
        "resolve_source_path",
        "resolve_toktrail_config_path",
        "resolve_toktrail_db_path",
        "scan_usage",
        "session_report",
        "start_session",
        "stop_session",
        "supported_harnesses",
        "usage_report",
        "usage_series_report",
    }

    assert required.issubset(set(public_api.__all__))
    assert "db" not in public_api.__all__
    assert "adapters" not in public_api.__all__
    assert "config" not in public_api.__all__


def test_solvecost_style_imports_use_only_public_modules() -> None:
    namespace: dict[str, object] = {}
    exec(
        "\n".join(
            [
                "from pathlib import Path",
                "from toktrail.errors import ToktrailError",
                (
                    "from toktrail.api import capture_source_snapshot, "
                    "finalize_manual_run, import_usage, init_state, "
                    "prepare_environment, prepare_manual_run, session_report, "
                    "TokenBreakdown, UsageEvent"
                ),
                "assert ToktrailError.__name__ == 'ToktrailError'",
                "assert callable(finalize_manual_run)",
                "assert callable(prepare_manual_run)",
                "assert callable(session_report)",
                "assert callable(import_usage)",
                "assert callable(capture_source_snapshot)",
                "assert callable(init_state)",
                "assert callable(prepare_environment)",
                "assert TokenBreakdown.__name__ == 'TokenBreakdown'",
                "assert UsageEvent.__name__ == 'UsageEvent'",
                "path = Path('.')",
            ]
        ),
        namespace,
        namespace,
    )
    assert "path" in namespace


def test_public_models_preserve_raw_json_privacy_by_default() -> None:
    event = UsageEvent(
        harness="pi",
        source_session_id="pi_ses_001",
        source_row_id="row-1",
        source_message_id="msg-1",
        source_dedup_key="msg-1",
        global_dedup_key="pi:msg-1",
        fingerprint_hash="fp-1",
        provider_id="anthropic",
        model_id="claude-3-5-sonnet",
        thinking_level="high",
        agent=None,
        created_ms=1,
        completed_ms=None,
        tokens=TokenBreakdown(input=1, output=2, cache_read=3),
        source_cost_usd=0.5,
        raw_json='{"secret": true}',
    )

    assert "raw_json" not in event.as_dict()
    assert event.as_dict(include_raw_json=True)["raw_json"] == '{"secret": true}'
    assert event.as_dict()["thinking_level"] == "high"
    assert CostTotals(actual_cost_usd=1.0, virtual_cost_usd=2.5).savings_usd == 1.5


def test_public_harness_metadata_and_paths_include_codex_goose_and_droid(
    monkeypatch,
    tmp_path,
) -> None:
    codex_env_path = tmp_path / "codex-sessions"
    copilot_env_path = tmp_path / "copilot.jsonl"
    goose_env_path = tmp_path / "goose-sessions.db"
    droid_env_path = tmp_path / "factory-sessions"
    amp_env_path = tmp_path / "amp-threads"
    monkeypatch.setenv("TOKTRAIL_CODEX_SESSIONS", str(codex_env_path))
    monkeypatch.setenv("TOKTRAIL_COPILOT_FILE", str(copilot_env_path))
    monkeypatch.setenv("TOKTRAIL_GOOSE_SESSIONS", str(goose_env_path))
    monkeypatch.setenv("TOKTRAIL_DROID_SESSIONS", str(droid_env_path))
    monkeypatch.setenv("TOKTRAIL_AMP_THREADS", str(amp_env_path))

    harness_names = {definition.name for definition in supported_harnesses()}

    assert "codex" in harness_names
    assert "goose" in harness_names
    assert "droid" in harness_names
    assert "amp" in harness_names
    assert default_source_path("amp") == default_amp_threads_path()
    assert default_source_path("codex") == Path.home() / ".codex" / "sessions"
    assert default_source_path("goose") == default_goose_sessions_db_path()
    assert default_source_path("droid") == default_droid_sessions_path()
    assert default_source_path("droid") == Path.home() / ".factory" / "sessions"
    assert resolve_source_path("codex", tmp_path / "explicit-codex") == (
        tmp_path / "explicit-codex"
    )
    assert resolve_source_path("codex") == codex_env_path
    assert resolve_source_path("goose", tmp_path / "explicit-goose.db") == (
        tmp_path / "explicit-goose.db"
    )
    assert resolve_source_path("goose") == goose_env_path
    assert resolve_source_path("droid", tmp_path / "explicit-droid") == (
        tmp_path / "explicit-droid"
    )
    assert resolve_source_path("droid") == droid_env_path
    assert resolve_source_path("amp", tmp_path / "explicit-amp") == (
        tmp_path / "explicit-amp"
    )
    assert resolve_source_path("amp") == amp_env_path


def test_harness_watch_metadata_aligns_with_cli_registration() -> None:
    """Verify that harness registry watch support matches CLI-registered watch commands.

    This test prevents drift between:
    - harness_registry metadata (supports_watch flags)
    - CLI-registered watch commands
    - Public API harness definitions

    Watch commands registered in CLI: opencode, copilot, pi, codex, amp, claude
    Harnesses marked supports_watch=True in registry: must match CLI
    Harnesses marked supports_watch=False: must NOT have CLI watch commands
    """
    from toktrail.adapters.registry import HARNESS_REGISTRY

    # Known watch harnesses from CLI registration (grep @watch_app.command in cli.py)
    cli_watch_harnesses = {"opencode", "copilot", "pi", "codex", "amp", "claude"}

    # Verify registry metadata matches CLI registration
    for harness_name, definition in HARNESS_REGISTRY.items():
        if definition.supports_watch:
            assert harness_name in cli_watch_harnesses, (
                f"Harness {harness_name} marked supports_watch=True "
                "but no CLI watch command registered"
            )
        else:
            assert harness_name not in cli_watch_harnesses, (
                f"Harness {harness_name} marked supports_watch=False "
                "but CLI watch command exists"
            )

    # Verify all CLI watch commands have supports_watch=True
    for watch_harness in cli_watch_harnesses:
        assert watch_harness in HARNESS_REGISTRY, (
            f"CLI watch command for {watch_harness} but not in registry"
        )
        assert HARNESS_REGISTRY[watch_harness].supports_watch, (
            f"CLI watch command for {watch_harness} "
            "but registry has supports_watch=False"
        )
