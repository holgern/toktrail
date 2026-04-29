from __future__ import annotations

import ast
import importlib
from pathlib import Path

from examples import _manual_run_common
from toktrail.api.models import (
    AgentSummaryRow,
    CostTotals,
    FinalizedManualRun,
    HarnessSummaryRow,
    ImportUsageResult,
    ModelSummaryRow,
    SessionTotals,
    SourceSessionDiff,
    SourceSessionSummary,
    TokenBreakdown,
    TrackingSession,
    TrackingSessionReport,
    UnconfiguredModelRow,
)

EXAMPLE_PATHS = (
    Path("examples/_manual_run_common.py"),
    Path("examples/manual_run_opencode.py"),
    Path("examples/manual_run_pi.py"),
    Path("examples/manual_run_copilot.py"),
    Path("examples/manual_run_codex.py"),
    Path("examples/manual_run_goose.py"),
    Path("examples/manual_run_droid.py"),
    Path("examples/manual_run_amp.py"),
)

WRAPPER_MODULES = (
    "examples.manual_run_opencode",
    "examples.manual_run_pi",
    "examples.manual_run_copilot",
    "examples.manual_run_codex",
    "examples.manual_run_goose",
    "examples.manual_run_droid",
    "examples.manual_run_amp",
)

FORBIDDEN_TOKTRAIL_IMPORTS = (
    "toktrail.db",
    "toktrail.models",
    "toktrail.reporting",
    "toktrail.paths",
    "toktrail.config",
    "toktrail.cli",
    "toktrail.adapters",
)


def _parse(path: Path) -> ast.Module:
    return ast.parse(path.read_text(), filename=str(path))


def test_example_modules_import_without_launching_harnesses() -> None:
    common = importlib.import_module("examples._manual_run_common")
    assert common.PROMPT

    for module_name in WRAPPER_MODULES:
        module = importlib.import_module(module_name)
        assert module.main


def test_examples_import_only_public_toktrail_modules() -> None:
    for path in EXAMPLE_PATHS:
        tree = _parse(path)
        imported_modules = [
            node.module
            for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module is not None
        ]

        assert not any(
            module == forbidden or module.startswith(f"{forbidden}.")
            for module in imported_modules
            for forbidden in FORBIDDEN_TOKTRAIL_IMPORTS
        )
        assert all(
            not module.startswith("toktrail.")
            or module.startswith("toktrail.api.")
            or module == "toktrail.errors"
            for module in imported_modules
        )


def test_manual_workflow_prepares_before_input_and_finalizes_after_input() -> None:
    tree = _parse(Path("examples/_manual_run_common.py"))
    functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)]
    run_function = next(node for node in functions if node.name == "run_manual_example")
    calls = [
        node.func.id
        for node in ast.walk(run_function)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    ]

    assert calls.index("prepare_manual_run") < calls.index("input")
    assert calls.index("input") < calls.index("finalize_manual_run")

    finalize_call = next(
        node
        for node in ast.walk(run_function)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Name)
        and node.func.id == "finalize_manual_run"
    )
    keyword_values = {keyword.arg: keyword.value for keyword in finalize_call.keywords}
    include_raw_json = keyword_values["include_raw_json"]
    assert isinstance(include_raw_json, ast.Constant)
    assert include_raw_json.value is False


def test_argument_parser_can_be_constructed() -> None:
    parser = _manual_run_common.build_parser(
        harness="copilot",
        default_db=".toktrail/copilot-example.db",
    )

    args = parser.parse_args(
        [
            "--db",
            ".toktrail/custom.db",
            "--source",
            "source.jsonl",
            "--shell",
            "nu",
            "--source-session-id",
            "session-1",
        ]
    )

    assert args.db == Path(".toktrail/custom.db")
    assert args.source == Path("source.jsonl")
    assert args.shell == "nu"
    assert args.source_session_id == "session-1"


def test_print_report_formats_public_dataclasses(capsys) -> None:
    report = TrackingSessionReport(
        session=TrackingSession(
            id=1,
            name="example",
            started_at_ms=1000,
            ended_at_ms=None,
        ),
        totals=SessionTotals(
            tokens=TokenBreakdown(
                input=10,
                output=20,
                reasoning=3,
                cache_read=4,
                cache_write=5,
            ),
            costs=CostTotals(
                source_cost_usd=0.01,
                actual_cost_usd=0.02,
                virtual_cost_usd=0.05,
                unpriced_count=1,
            ),
            message_count=2,
        ),
        by_harness=(
            HarnessSummaryRow(
                harness="codex",
                message_count=2,
                total_tokens=42,
                costs=CostTotals(actual_cost_usd=0.02, virtual_cost_usd=0.05),
            ),
        ),
        by_model=(
            ModelSummaryRow(
                provider_id="openai",
                model_id="gpt-5",
                thinking_level="medium",
                message_count=2,
                tokens=TokenBreakdown(input=10, output=20, reasoning=3),
                costs=CostTotals(actual_cost_usd=0.02, virtual_cost_usd=0.05),
            ),
        ),
        by_agent=(
            AgentSummaryRow(
                agent="build",
                message_count=2,
                total_tokens=42,
                costs=CostTotals(actual_cost_usd=0.02, virtual_cost_usd=0.05),
            ),
        ),
        unconfigured_models=(
            UnconfiguredModelRow(
                required=("actual", "virtual"),
                harness="codex",
                provider_id="openai",
                model_id="gpt-5",
                thinking_level="medium",
                message_count=2,
                tokens=TokenBreakdown(input=10, output=20, reasoning=3),
            ),
        ),
    )

    _manual_run_common.print_report(report)

    output = capsys.readouterr().out
    assert "input:        10" in output
    assert "output:       20" in output
    assert "reasoning:    3" in output
    assert "cache_read:   4" in output
    assert "cache_write:  5" in output
    assert "total:        42" in output
    assert "source_cost:  $0.010000" in output
    assert "actual_cost:  $0.020000" in output
    assert "virtual_cost: $0.050000" in output
    assert "savings:      $0.030000" in output
    assert "unpriced:     1" in output
    assert "openai/gpt-5 thinking=medium" in output
    assert "codex openai/gpt-5 thinking=medium required=actual,virtual" in output


def test_print_finalized_formats_import_and_source_session(capsys) -> None:
    source_session = SourceSessionSummary(
        harness="codex",
        source_session_id="session-1",
        first_created_ms=1000,
        last_created_ms=2000,
        assistant_message_count=1,
        tokens=TokenBreakdown(input=1, output=2),
        costs=CostTotals(source_cost_usd=0.01),
        models=("gpt-5",),
        providers=("openai",),
    )
    report = TrackingSessionReport(
        session=None,
        totals=SessionTotals(
            tokens=TokenBreakdown(input=1, output=2),
            costs=CostTotals(source_cost_usd=0.01),
            message_count=1,
        ),
        by_harness=(),
        by_model=(),
        by_agent=(),
    )
    finalized = FinalizedManualRun(
        tracking_session=TrackingSession(
            id=1,
            name="example",
            started_at_ms=1000,
            ended_at_ms=3000,
        ),
        source_session=source_session,
        source_diff=SourceSessionDiff(
            harness="codex",
            before_count=0,
            after_count=1,
            new_sessions=(source_session,),
            updated_sessions=(),
            unchanged_sessions=(),
        ),
        import_result=ImportUsageResult(
            tracking_session_id=1,
            harness="codex",
            source_path=Path("sessions"),
            source_session_id="session-1",
            rows_seen=3,
            rows_imported=2,
            rows_linked=1,
            rows_skipped=1,
            events_seen=3,
            events_imported=2,
            events_skipped=1,
            files_seen=1,
        ),
        report=report,
    )

    _manual_run_common.print_finalized(finalized)

    output = capsys.readouterr().out
    assert "id:           1" in output
    assert "active:       False" in output
    assert "session_id:   session-1" in output
    assert "models:       gpt-5" in output
    assert "providers:    openai" in output
    assert "rows_seen:    3" in output
    assert "rows_imported:2" in output
    assert "rows_linked:  1" in output
    assert "events_imported:2" in output
