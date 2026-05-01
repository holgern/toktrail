from __future__ import annotations

import argparse
from pathlib import Path

from toktrail.api.models import FinalizedManualRun, TrackingSessionReport
from toktrail.api.workflow import finalize_manual_run, prepare_manual_run
from toktrail.errors import AmbiguousSourceSessionError, SourcePathError

PROMPT = """Please answer in a concise way:

Inspect the current repository and list:
1. The main package entry points.
2. The command-line entry point.
3. One test file that verifies the public API.

Do not edit files.
"""


def _money(value: float) -> str:
    return f"${value:.6f}"


def print_report(report: TrackingSessionReport) -> None:
    totals = report.totals
    tokens = totals.tokens
    costs = totals.costs

    print("\n== Totals ==")
    print(f"messages:     {totals.message_count}")
    print(f"input:        {tokens.input}")
    print(f"output:       {tokens.output}")
    print(f"reasoning:    {tokens.reasoning}")
    print(f"cache_read:   {tokens.cache_read}")
    print(f"cache_write:  {tokens.cache_write}")
    print(f"total:        {tokens.total}")
    print(f"source_cost:  {_money(costs.source_cost_usd)}")
    print(f"actual_cost:  {_money(costs.actual_cost_usd)}")
    print(f"virtual_cost: {_money(costs.virtual_cost_usd)}")
    print(f"savings:      {_money(costs.savings_usd)}")
    print(f"unpriced:     {costs.unpriced_count}")

    print("\n== By harness ==")
    for row in report.by_harness:
        print(
            f"{row.harness}: messages={row.message_count} "
            f"tokens={row.total_tokens} "
            f"actual={_money(row.costs.actual_cost_usd)} "
            f"virtual={_money(row.costs.virtual_cost_usd)} "
            f"savings={_money(row.costs.savings_usd)} "
            f"unpriced={row.costs.unpriced_count}"
        )

    print("\n== By model ==")
    for row in report.by_model:
        tokens = row.tokens
        costs = row.costs
        thinking = row.thinking_level or "-"
        print(
            f"{row.provider_id}/{row.model_id} thinking={thinking} "
            f"messages={row.message_count} "
            f"input={tokens.input} output={tokens.output} "
            f"reasoning={tokens.reasoning} "
            f"cache_read={tokens.cache_read} "
            f"cache_write={tokens.cache_write} total={tokens.total} "
            f"actual={_money(costs.actual_cost_usd)} "
            f"virtual={_money(costs.virtual_cost_usd)} "
            f"savings={_money(costs.savings_usd)} "
            f"unpriced={costs.unpriced_count}"
        )

    print("\n== By activity ==")
    for row in report.by_activity:
        print(
            f"{row.agent}: messages={row.message_count} "
            f"tokens={row.total_tokens} "
            f"actual={_money(row.costs.actual_cost_usd)} "
            f"virtual={_money(row.costs.virtual_cost_usd)}"
        )

    if report.unconfigured_models:
        print("\n== Unconfigured pricing ==")
        for row in report.unconfigured_models:
            tokens = row.tokens
            thinking = row.thinking_level or "-"
            required = ",".join(row.required)
            print(
                f"{row.harness} {row.provider_id}/{row.model_id} "
                f"thinking={thinking} required={required} "
                f"messages={row.message_count} total={tokens.total}"
            )


def print_finalized(finalized: FinalizedManualRun) -> None:
    print("\n== Tracking session ==")
    print(f"id:           {finalized.run.id}")
    print(f"name:         {finalized.run.name}")
    print(f"started_ms:   {finalized.run.started_at_ms}")
    print(f"ended_ms:     {finalized.run.ended_at_ms}")
    print(f"active:       {finalized.run.active}")

    print("\n== Source session ==")
    source = finalized.source_session
    print(f"harness:      {source.harness}")
    print(f"session_id:   {source.source_session_id}")
    print(f"first_ms:     {source.first_created_ms}")
    print(f"last_ms:      {source.last_created_ms}")
    print(f"messages:     {source.assistant_message_count}")
    print(f"models:       {', '.join(source.models) if source.models else '-'}")
    print(f"providers:    {', '.join(source.providers) if source.providers else '-'}")

    print("\n== Import ==")
    result = finalized.import_result
    print(f"status:       {result.status}")
    print(f"rows_seen:    {result.rows_seen}")
    print(f"rows_imported:{result.rows_imported}")
    print(f"rows_linked:  {result.rows_linked}")
    print(f"rows_skipped: {result.rows_skipped}")
    print(f"events_seen:  {result.events_seen}")
    print(f"events_imported:{result.events_imported}")
    print(f"events_skipped:{result.events_skipped}")
    print(f"files_seen:   {result.files_seen}")

    print_report(finalized.report)


def run_manual_example(
    *,
    harness: str,
    display_name: str,
    db_path: Path,
    source_path: Path | None,
    shell: str,
    source_session_id: str | None,
) -> int:
    prepared = prepare_manual_run(
        db_path,
        harness,
        name=f"stable-api-example:{harness}",
        source_path=source_path,
        shell=shell,
    )

    print(f"Started toktrail tracking session {prepared.run.id}.")
    print(f"Harness: {display_name}")
    print(f"Source path: {prepared.source_path}")

    if prepared.environment.instructions:
        print("\nEnvironment instructions:")
        for line in prepared.environment.instructions:
            print(f"- {line}")

    if prepared.environment.shell_exports:
        print("\nRun these shell exports before starting the harness:")
        for line in prepared.environment.shell_exports:
            print(line)

    print("\nStart the harness manually now.")
    print("Paste this prompt into the harness:")
    print("-" * 72)
    print(PROMPT)
    print("-" * 72)

    input(
        "\nAfter the harness has completed, close/exit the harness, "
        "then press Enter here to import usage..."
    )

    try:
        finalized = finalize_manual_run(
            db_path,
            prepared,
            source_session_id=source_session_id,
            include_raw_json=False,
            stop_session=True,
        )
    except AmbiguousSourceSessionError:
        print(
            "\nMultiple source sessions changed. Re-run this example with "
            "--source-session-id set to the session created by the harness."
        )
        raise
    except SourcePathError:
        print(
            "\nNo new or updated source session was detected. Verify the harness "
            "wrote to the selected source path."
        )
        raise

    print_finalized(finalized)
    return 0


def build_parser(*, harness: str, default_db: str) -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=f"Run a toktrail stable API manual-run example for {harness}."
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(default_db),
        help="toktrail state database path.",
    )
    parser.add_argument(
        "--source",
        type=Path,
        default=None,
        help="Harness source path. Omit to use toktrail defaults.",
    )
    parser.add_argument(
        "--shell",
        default="bash",
        choices=("bash", "zsh", "fish", "nu", "nushell", "powershell", "pwsh"),
        help="Shell syntax for environment exports. Relevant for Copilot.",
    )
    parser.add_argument(
        "--source-session-id",
        default=None,
        help="Disambiguate when multiple source sessions changed.",
    )
    return parser
