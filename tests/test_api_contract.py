from __future__ import annotations

import importlib
from pathlib import Path

from toktrail.api.harnesses import supported_harnesses
from toktrail.api.models import CostTotals, TokenBreakdown, UsageEvent
from toktrail.api.paths import default_source_path, resolve_source_path


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


def test_solvecost_style_imports_use_only_public_modules() -> None:
    namespace: dict[str, object] = {}
    exec(
        "\n".join(
            [
                "from pathlib import Path",
                "from toktrail.errors import ToktrailError",
                (
                    "from toktrail.api.workflow import "
                    "finalize_manual_run, prepare_manual_run"
                ),
                "from toktrail.api.reports import session_report",
                "from toktrail.api.imports import import_usage",
                "from toktrail.api.sources import capture_source_snapshot",
                "from toktrail.api.sessions import init_state",
                "from toktrail.api.environment import prepare_environment",
                "assert ToktrailError.__name__ == 'ToktrailError'",
                "assert callable(finalize_manual_run)",
                "assert callable(prepare_manual_run)",
                "assert callable(session_report)",
                "assert callable(import_usage)",
                "assert callable(capture_source_snapshot)",
                "assert callable(init_state)",
                "assert callable(prepare_environment)",
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


def test_public_harness_metadata_and_paths_include_codex(monkeypatch, tmp_path) -> None:
    codex_env_path = tmp_path / "codex-sessions"
    copilot_env_path = tmp_path / "copilot.jsonl"
    monkeypatch.setenv("TOKTRAIL_CODEX_SESSIONS", str(codex_env_path))
    monkeypatch.setenv("TOKTRAIL_COPILOT_FILE", str(copilot_env_path))

    harness_names = {definition.name for definition in supported_harnesses()}

    assert "codex" in harness_names
    assert default_source_path("codex") == Path.home() / ".codex" / "sessions"
    assert resolve_source_path("codex", tmp_path / "explicit-codex") == (
        tmp_path / "explicit-codex"
    )
    assert resolve_source_path("codex") == codex_env_path
