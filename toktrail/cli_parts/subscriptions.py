from __future__ import annotations

import json
from typing import Callable

import typer


def subscriptions_status_impl(
    *,
    ctx: typer.Context,
    provider_id: str | None,
    period: str,
    json_output: bool,
    rich_output: bool,
    now_ms: int | None,
    refresh: bool,
    refresh_details: bool,
    raw: bool | None,
    timezone_name: str | None,
    utc: bool,
    exit_with_error_fn: Callable[[str], None],
    refresh_before_report_fn: Callable[..., list[object]],
    load_costing_config_or_exit_fn: Callable[[typer.Context], object],
    open_toktrail_connection_fn: Callable[[typer.Context], object],
    summarize_subscription_usage_fn: Callable[..., object],
    filter_subscription_usage_report_fn: Callable[..., object],
    wrap_refresh_json_payload_fn: Callable[..., dict[str, object]],
    print_subscription_usage_report_fn: Callable[..., None],
) -> None:
    if timezone_name is not None and utc:
        exit_with_error_fn("Use either --timezone or --utc, not both.")

    normalized_period = period.strip().lower()
    if normalized_period not in {"all", "5h", "daily", "weekly", "monthly", "yearly"}:
        exit_with_error_fn(
            "--period must be one of: all, 5h, daily, weekly, monthly, yearly."
        )

    refresh_results = refresh_before_report_fn(
        ctx,
        enabled=refresh,
        details=refresh_details,
        json_output=json_output,
        include_raw_json=raw,
    )
    costing_config = load_costing_config_or_exit_fn(ctx)
    conn = open_toktrail_connection_fn(ctx)
    try:
        report = summarize_subscription_usage_fn(
            conn,
            costing_config,
            provider_id=provider_id,
            now_ms=now_ms,
        )
    except ValueError as exc:
        exit_with_error_fn(str(exc))
    finally:
        conn.close()

    filtered_report = filter_subscription_usage_report_fn(
        report,
        period=normalized_period,
    )

    if json_output:
        typer.echo(
            json.dumps(
                wrap_refresh_json_payload_fn(
                    filtered_report.as_dict(),
                    refresh_results=refresh_results,
                    include_refresh=refresh_details,
                ),
                indent=2,
            )
        )
        return

    print_subscription_usage_report_fn(
        filtered_report,
        provider_filter=provider_id,
        rich_output=rich_output,
        display_timezone_name=timezone_name,
        display_utc=utc,
    )
