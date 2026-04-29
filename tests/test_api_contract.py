from __future__ import annotations

import importlib

from toktrail.api.models import CostTotals, TokenBreakdown, UsageEvent


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
        agent=None,
        created_ms=1,
        completed_ms=None,
        tokens=TokenBreakdown(input=1, output=2, cache_read=3),
        source_cost_usd=0.5,
        raw_json='{"secret": true}',
    )

    assert "raw_json" not in event.as_dict()
    assert event.as_dict(include_raw_json=True)["raw_json"] == '{"secret": true}'
    assert CostTotals(actual_cost_usd=1.0, virtual_cost_usd=2.5).savings_usd == 1.5
